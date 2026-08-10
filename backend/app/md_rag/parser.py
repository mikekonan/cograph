"""Markdown parser — extracts structured metadata from raw markdown text.

Produces:
- frontmatter (dict)
- heading_tree (list of {level, text, anchor, line})
- code_blocks (list of {language, content, start_line, end_line})
- tables (list of {header, rows, start_line, end_line})
- links (list of {text, href, line})
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True, kw_only=True)
class ParsedMarkdown:
    title: str | None = None
    frontmatter: dict[str, object] = field(default_factory=dict)
    heading_tree: list[dict[str, object]] = field(default_factory=list)
    code_blocks: list[dict[str, object]] = field(default_factory=list)
    tables: list[dict[str, object]] = field(default_factory=list)
    links: list[dict[str, object]] = field(default_factory=list)
    word_count: int = 0
    line_count: int = 0


class MarkdownParser:
    """Fast regex-based markdown parser. No external deps."""

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    _CODE_BLOCK_RE = re.compile(
        r"^```(\w+)?\n(.*?)```$",
        re.MULTILINE | re.DOTALL,
    )
    _INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    _WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
    _FRONTMATTER_RE = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n",
        re.DOTALL,
    )
    _TABLE_RE = re.compile(
        r"^(\|[^\n]+\|\n\|[\s\-:|]+\|\n(?:\|[^\n]+\|\n?)+)",
        re.MULTILINE,
    )

    def parse(self, text: str) -> ParsedMarkdown:
        lines = text.splitlines()
        line_count = len(lines)
        word_count = len(text.split())

        # Strip frontmatter for downstream parsing
        body = text
        frontmatter: dict[str, object] = {}
        fm_match = self._FRONTMATTER_RE.search(text)
        if fm_match:
            body = text[fm_match.end() :]
            frontmatter = self._parse_yaml_frontmatter(fm_match.group(1))

        title = frontmatter.get("title")
        if title is None:
            title = self._extract_first_h1(body)

        heading_tree = self._extract_headings(text)
        code_blocks = self._extract_code_blocks(text)
        tables = self._extract_tables(text)
        links = self._extract_links(body)

        return ParsedMarkdown(
            title=title if isinstance(title, str) else None,
            frontmatter=frontmatter,
            heading_tree=heading_tree,
            code_blocks=code_blocks,
            tables=tables,
            links=links,
            word_count=word_count,
            line_count=line_count,
        )

    def _parse_yaml_frontmatter(self, raw: str) -> dict[str, object]:
        """Minimal YAML frontmatter parser (key: value lines only)."""
        result: dict[str, object] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                result[key] = value
        return result

    def _extract_first_h1(self, text: str) -> str | None:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return None

    def _extract_headings(self, text: str) -> list[dict[str, object]]:
        headings: list[dict[str, object]] = []
        for match in self._HEADING_RE.finditer(text):
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            anchor = self._make_anchor(heading_text)
            line_no = text[: match.start()].count("\n") + 1
            headings.append(
                {
                    "level": level,
                    "text": heading_text,
                    "anchor": anchor,
                    "line": line_no,
                }
            )
        return headings

    def _extract_code_blocks(self, text: str) -> list[dict[str, object]]:
        blocks: list[dict[str, object]] = []
        for match in self._CODE_BLOCK_RE.finditer(text):
            language = (match.group(1) or "").strip().lower()
            content = match.group(2)
            start_line = text[: match.start()].count("\n") + 1
            end_line = text[: match.end()].count("\n") + 1
            blocks.append(
                {
                    "language": language,
                    "content": content,
                    "start_line": start_line,
                    "end_line": end_line,
                }
            )
        return blocks

    def _extract_tables(self, text: str) -> list[dict[str, object]]:
        tables: list[dict[str, object]] = []
        for match in self._TABLE_RE.finditer(text):
            raw = match.group(1)
            rows = [r.strip() for r in raw.strip().splitlines() if r.strip()]
            if len(rows) < 2:
                continue
            header = [c.strip() for c in rows[0].split("|") if c.strip()]
            data_rows = []
            for row in rows[2:]:
                cells = [c.strip() for c in row.split("|") if c.strip()]
                data_rows.append(cells)
            start_line = text[: match.start()].count("\n") + 1
            end_line = text[: match.end()].count("\n") + 1
            tables.append(
                {
                    "header": header,
                    "rows": data_rows,
                    "start_line": start_line,
                    "end_line": end_line,
                }
            )
        return tables

    def _extract_links(self, text: str) -> list[dict[str, object]]:
        links: list[dict[str, object]] = []
        for match in self._INLINE_LINK_RE.finditer(text):
            link_text = match.group(1)
            href = match.group(2)
            line_no = text[: match.start()].count("\n") + 1
            link_type = self._classify_link(href)
            links.append(
                {
                    "text": link_text,
                    "href": href,
                    "line": line_no,
                    "link_type": link_type,
                }
            )
        for match in self._WIKI_LINK_RE.finditer(text):
            link_text = match.group(1)
            line_no = text[: match.start()].count("\n") + 1
            links.append(
                {
                    "text": link_text,
                    "href": link_text,
                    "line": line_no,
                    "link_type": "wiki",
                }
            )
        return links

    @staticmethod
    def _make_anchor(heading_text: str) -> str:
        anchor = heading_text.lower()
        anchor = re.sub(r"[^\w\s-]", "", anchor)
        anchor = re.sub(r"\s+", "-", anchor).strip("-")
        return anchor

    @staticmethod
    def _classify_link(href: str) -> str:
        if href.startswith(("http://", "https://", "mailto:", "ftp://")):
            return "absolute"
        if href.startswith("#"):
            return "anchor"
        if href.endswith(".md") or href.endswith(".mdx"):
            return "markdown"
        return "relative"
