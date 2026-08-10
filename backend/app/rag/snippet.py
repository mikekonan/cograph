"""Excerpt builder for MCP/REST search responses.

Returns ``(snippet, content_truncated)`` so the agent knows when full content
is available via ``cograph_read_node`` / ``cograph_read_chunk``. The snippet
is centred on the first query-term match and falls back to a head-anchored
window for vector-only hits with no lexical overlap. Word-boundary safe.

This module replaces the older ad-hoc ``context_builder._snippet`` helper
with a single contract: every search-style tool uses the same
excerpt/truncation logic so the agent can reason uniformly about when to
follow up for full text.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "does",
        "for",
        "how",
        "in",
        "is",
        "of",
        "or",
        "the",
        "to",
        "what",
        "where",
        "which",
        "who",
        "why",
    }
)

DEFAULT_SNIPPET_CHARS = 600
MIN_SNIPPET_CHARS = 80
MAX_SNIPPET_CHARS = 4000

_ELLIPSIS = "…"
_WORD_BOUNDARY_LOOKBACK = 80


def extract_query_terms(query: str, *, max_terms: int = 8) -> list[str]:
    """Tokenise a natural-language query into lowercased searchable terms."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_RE.findall(query or ""):
        term = raw.lower().strip("._-:/")
        if len(term) < 2 or term in _STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


def make_snippet(
    content: str | None,
    query_terms: list[str] | None = None,
    *,
    chars: int = DEFAULT_SNIPPET_CHARS,
) -> tuple[str, bool]:
    """Build a query-anchored excerpt and report whether content was truncated.

    - ``chars`` is clamped to ``[MIN_SNIPPET_CHARS, MAX_SNIPPET_CHARS]``.
    - If the (whitespace-collapsed) content fits in the budget, returns it as
      ``(content, False)``.
    - Otherwise centres a window on the first match of any ``query_terms``
      (case-insensitive); on miss, falls back to a head-anchored window.
    - Trims to a word boundary and adds ``…`` ellipses on truncated edges.
    - Returned snippet length is bounded by ``chars`` plus a small ellipsis +
      word-boundary tolerance.

    The truncation flag is the contract MCP tools surface to agents: when
    ``content_truncated`` is ``True``, the agent should fetch the full body
    via ``cograph_read_node`` / ``cograph_read_chunk`` if needed.
    """
    chars = max(MIN_SNIPPET_CHARS, min(MAX_SNIPPET_CHARS, chars))
    raw = content or ""
    if not raw:
        return "", False

    collapsed = _collapse_whitespace(raw)
    if len(collapsed) <= chars:
        return collapsed, False

    lower = collapsed.lower()
    first_match = -1
    for term in query_terms or []:
        if not term:
            continue
        idx = lower.find(term.lower())
        if idx >= 0 and (first_match < 0 or idx < first_match):
            first_match = idx

    if first_match < 0:
        start, end = 0, chars
    else:
        lead = chars // 3
        start = max(0, first_match - lead)
        end = min(len(collapsed), start + chars)
        if end - start < chars and start > 0:
            start = max(0, end - chars)

    snippet = collapsed[start:end]
    has_head_ellipsis = start > 0
    has_tail_ellipsis = end < len(collapsed)
    if has_tail_ellipsis:
        # Only trim mid-word when we'll add a trailing ellipsis. When the
        # snippet runs to content-end, the last token is already complete.
        snippet = _trim_to_word_boundary(snippet)
    if has_head_ellipsis:
        # Drop the partial leading token so the snippet starts on a word.
        snippet = _trim_leading_partial_word(snippet)
        snippet = _ELLIPSIS + snippet.lstrip()
    if has_tail_ellipsis:
        snippet = snippet.rstrip() + _ELLIPSIS
    return snippet, True


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _trim_to_word_boundary(text: str) -> str:
    """Drop a trailing partial word if the cut landed mid-token.

    Python ``str`` slicing operates on Unicode code points (not UTF-8 bytes),
    so multi-byte runes are never split by indexing. The remaining concern is
    purely visual: a snippet ending in mid-word reads worse than one trimmed
    back to the last whitespace.
    """
    if not text:
        return text
    last = text[-1]
    if last.isspace() or last in ".!?;:,)]\"'":
        return text.rstrip()
    last_space = text.rfind(" ")
    if last_space > 0 and last_space >= len(text) - _WORD_BOUNDARY_LOOKBACK:
        return text[:last_space].rstrip()
    return text.rstrip()


def _trim_leading_partial_word(text: str) -> str:
    """Drop a leading partial token so the snippet starts on a word boundary."""
    if not text:
        return text
    first = text[0]
    if first.isspace():
        return text.lstrip()
    first_space = text.find(" ")
    if 0 < first_space <= _WORD_BOUNDARY_LOOKBACK:
        return text[first_space + 1 :]
    return text
