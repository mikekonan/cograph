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
and which questions each page covers. Over MCP this map is the DEFAULT form
of the generated wiki; a full page (or one section) is available on demand
via the `cograph_wiki_page` tool — which is what `extract_section` below
backs — and the web UI renders full bodies too.
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


def extract_lead(
    content: str, *, max_chars: int = 400, max_lead_sections: int = 3
) -> str:
    """Lead prose: the page's opening narrative, gathered across its sections.

    Walks the page top-to-bottom collecting prose — the one-line brief under
    the H1 *and* the body of the opening ``## sections`` (``## Overview``
    first, by the page contract) — until the char budget is spent or the
    `max_lead_sections`-th top-level (``##``) section is reached, whichever
    comes first. Every heading (the H1 title and all section headings), fenced
    code / mermaid blocks, citation ``Source:`` footers and unresolved
    breadcrumbs are dropped; markdown links collapse to their label; the
    result is whitespace-normalised and truncated.

    This previously stopped at the first ``##``, which captured only the
    one-sentence teaser the writer puts under the title and dropped the
    substantive ``## Overview`` prose — so the compact map's lead budget (1200
    chars for the index, 400 elsewhere) sat mostly empty and an agent got a
    table of contents with no real overview. Spending the budget on the
    opening prose is what makes the summarized wiki a useful overview rather
    than a list of headings. The section cap keeps it the *opening* narrative:
    on a short, many-section page the budget alone wouldn't stop trailing
    sections (``## Usage Examples``, ``## FAQ``) from bleeding in.
    """
    prose: list[str] = []
    top_sections_seen = 0
    for line in _strip_code_fences(content.splitlines()):
        heading = _HEADING_RE.match(line)
        if heading is not None:
            if len(heading.group(1)) == 2:
                top_sections_seen += 1
                if top_sections_seen > max_lead_sections:
                    break
            continue  # drop every heading: H1 title + section headings
        if _SOURCE_LINE_RE.match(line):
            continue
        cleaned = _clean_prose(line).strip()
        if cleaned:
            prose.append(cleaned)
    text = re.sub(r"\s+", " ", " ".join(prose)).strip()
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


def _norm_heading(text: str) -> str:
    """Case- and whitespace-insensitive key for matching a heading title."""
    return re.sub(r"\s+", " ", _clean_prose(text)).strip().casefold()


def extract_section(content: str, section: str) -> str | None:
    """Return the full verbatim markdown of one `##`/`###`… section.

    `section` is matched (case- and whitespace-insensitively) against the
    heading titles surfaced by `extract_sections`. The returned block runs from
    the matched heading up to the next heading of the same-or-higher level, so a
    `##` section carries its `###` subsections along — and keeps the code
    fences, mermaid, and citation footers `extract_lead` strips, because this
    is the on-demand full read, not the map. Returns None if no heading matches.

    Headings inside fenced code blocks are ignored (a `# comment` in a shell
    sample is not a section), mirroring `extract_sections`.
    """
    target = _norm_heading(section)
    if not target:
        return None
    lines = content.splitlines()
    in_fence = False
    headings: list[tuple[int, int, str]] = []  # (line_index, level, norm_title)
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING_RE.match(line)
        if heading is not None and len(heading.group(1)) >= 2:
            headings.append(
                (index, len(heading.group(1)), _norm_heading(heading.group(2)))
            )
    for position, (line_index, level, title) in enumerate(headings):
        if title != target:
            continue
        end = len(lines)
        for next_index, next_level, _ in headings[position + 1 :]:
            if next_level <= level:
                end = next_index
                break
        return "\n".join(lines[line_index:end]).strip()
    return None


def truncate_markdown(text: str, *, max_chars: int) -> tuple[str, bool]:
    """Cap a full markdown body at `max_chars` on a line boundary.

    Unlike `extract_lead`/`_truncate` (which collapse whitespace for the map),
    this preserves the document structure — newlines, headings, fenced code,
    mermaid — because it backs the on-demand *full* read (`cograph_wiki_page`),
    where the markdown must come back verbatim, not flattened. Returns
    `(body, truncated)`; cuts at the last newline before the budget so a fenced
    block isn't sliced mid-line.
    """
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    return cut.rstrip(), True
