"""Deterministic compaction of generated wiki pages.

A repo's full generated wiki runs ~34-98k tokens (avg ~73k) — too large to
hand an MCP client up front, which is why clients today never reliably read
it. This module reduces each page to its *essential* shape: the lead prose,
the section headings, and the reader-questions it answers, with fenced code
blocks / mermaid diagrams / citation machinery stripped.

There is no LLM here. It operates on the markdown already stored in the
`documents` table, so it adds zero generation cost and can never drift from
the published page — recompute it on every read and it always reflects the
current wiki. Assembled across a repo's pages the result is the "map":
~2-3k tokens that tell an agent what the service is, how it's structured,
and which questions each page covers. Over MCP this map is the ONLY form
of the generated wiki — full page bodies are served to the web UI only.
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# `[label](target)` -> `label`; keeps the human-readable anchor, drops the
# graph/file URL that only inflates the token count of a map.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
# Citation source footers the writer appends under code samples.
_SOURCE_LINE_RE = re.compile(r"^\s*Source:\s", re.IGNORECASE)
# Unresolved-symbol breadcrumbs the resolver leaves inline.
_UNRESOLVED_RE = re.compile(r"⚠️?\s*unresolved:\s*\S+")


def _strip_code_fences(lines: list[str]) -> list[str]:
    """Drop fenced blocks (```/~~~, including ```mermaid) wholesale.

    A code sample or diagram is exactly what a reader drills into the full
    page for — it has no place in the map.
    """
    out: list[str] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return out


def _clean_prose(text: str) -> str:
    text = _HTML_COMMENT_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _UNRESOLVED_RE.sub("", text)
    return text


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rstrip()
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip() + "…"


def extract_lead(content: str, *, max_chars: int = 400) -> str:
    """Lead prose: text from the top of the page up to the first `##`.

    The H1 title is dropped (carried separately as the page title), fenced
    blocks and citation noise are stripped, markdown links collapse to their
    label, and the result is whitespace-normalised and truncated.
    """
    lead: list[str] = []
    for line in _strip_code_fences(content.splitlines()):
        heading = _HEADING_RE.match(line)
        if heading is not None:
            if len(heading.group(1)) == 1:
                continue  # skip the H1 title itself
            break  # first H2+ ends the lead
        if _SOURCE_LINE_RE.match(line):
            continue
        cleaned = _clean_prose(line).strip()
        if cleaned:
            lead.append(cleaned)
    text = re.sub(r"\s+", " ", " ".join(lead)).strip()
    return _truncate(text, max_chars)


def extract_sections(content: str, *, max_sections: int = 24) -> list[str]:
    """The `##`/`###`… heading titles of the page, in document order.

    This is the page's table of contents — enough for an agent to judge
    whether the full page is worth fetching, without the body.
    """
    sections: list[str] = []
    for line in _strip_code_fences(content.splitlines()):
        heading = _HEADING_RE.match(line)
        if heading is not None and len(heading.group(1)) >= 2:
            title = _clean_prose(heading.group(2)).strip()
            if title and title not in sections:
                sections.append(title)
        if len(sections) >= max_sections:
            break
    return sections
