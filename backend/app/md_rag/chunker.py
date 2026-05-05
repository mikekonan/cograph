"""Heading-aware markdown chunker.

Chunks respect heading boundaries — a chunk never splits a heading section.
Each chunk carries its heading_path, heading_level, and section_anchor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, kw_only=True)
class MdChunkDraft:
    chunk_index: int
    heading_path: list[str]
    heading_level: int | None
    section_anchor: str | None
    content: str


class MdChunker:
    """Chunk markdown by heading boundaries with a token/char budget."""

    def __init__(self, max_chars: int = 2000, max_chunks: int = 256) -> None:
        self.max_chars = max_chars
        self.max_chunks = max_chunks

    def chunk(self, text: str) -> list[MdChunkDraft]:
        lines = text.splitlines()
        sections = self._split_by_headings(lines)

        chunks: list[MdChunkDraft] = []
        current_buffer: list[str] = []
        current_path: list[str] = []
        current_level: int | None = None
        current_anchor: str | None = None
        current_chars = 0

        for section_lines, path, level, anchor in sections:
            section_text = "\n".join(section_lines)
            section_len = len(section_text)

            # If the section alone exceeds max_chars, we must split it further
            # but we still keep the heading_path metadata.
            if section_len > self.max_chars:
                # Flush any pending buffer first
                if current_buffer:
                    chunks.append(
                        MdChunkDraft(
                            chunk_index=len(chunks),
                            heading_path=list(current_path),
                            heading_level=current_level,
                            section_anchor=current_anchor,
                            content="\n".join(current_buffer).strip(),
                        )
                    )
                    current_buffer = []
                    current_chars = 0

                # Split oversized section by paragraphs
                sub_chunks = self._split_section(
                    section_lines, path, level, anchor
                )
                for sub in sub_chunks:
                    sub.chunk_index = len(chunks)
                    chunks.append(sub)
                continue

            # If adding this section would exceed budget, flush first
            if current_chars + section_len > self.max_chars and current_buffer:
                chunks.append(
                    MdChunkDraft(
                        chunk_index=len(chunks),
                        heading_path=list(current_path),
                        heading_level=current_level,
                        section_anchor=current_anchor,
                        content="\n".join(current_buffer).strip(),
                    )
                )
                current_buffer = []
                current_chars = 0

            # Start a new buffer if empty, or append to current
            if not current_buffer:
                current_path = path
                current_level = level
                current_anchor = anchor

            current_buffer.extend(section_lines)
            current_chars += section_len + 1  # +1 for newline

        # Flush remaining buffer
        if current_buffer:
            chunks.append(
                MdChunkDraft(
                    chunk_index=len(chunks),
                    heading_path=list(current_path),
                    heading_level=current_level,
                    section_anchor=current_anchor,
                    content="\n".join(current_buffer).strip(),
                )
            )

        return chunks[: self.max_chunks]

    def _split_by_headings(
        self, lines: list[str]
    ) -> list[tuple[list[str], list[str], int | None, str | None]]:
        """Split lines into (section_lines, heading_path, level, anchor)."""
        import re

        heading_re = re.compile(r"^(#{1,6})\s+(.+)$")
        sections: list[tuple[list[str], list[str], int | None, str | None]] = []
        current_lines: list[str] = []
        current_path: list[str] = []
        current_level: int | None = None
        current_anchor: str | None = None

        for line in lines:
            match = heading_re.match(line)
            if match:
                # Flush previous section
                if current_lines:
                    sections.append(
                        (
                            current_lines,
                            list(current_path),
                            current_level,
                            current_anchor,
                        )
                    )
                # Start new section with this heading as first line
                level = len(match.group(1))
                text = match.group(2).strip()
                anchor = self._make_anchor(text)
                current_path = self._update_path(current_path, level, text)
                current_level = level
                current_anchor = anchor
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            sections.append(
                (
                    current_lines,
                    list(current_path),
                    current_level,
                    current_anchor,
                )
            )

        return sections

    def _split_section(
        self,
        lines: list[str],
        path: list[str],
        level: int | None,
        anchor: str | None,
    ) -> list[MdChunkDraft]:
        """Split an oversized section by paragraphs."""
        chunks: list[MdChunkDraft] = []
        buffer: list[str] = []
        buf_chars = 0

        for line in lines:
            line_len = len(line) + 1
            if buf_chars + line_len > self.max_chars and buffer:
                chunks.append(
                    MdChunkDraft(
                        chunk_index=0,  # filled later
                        heading_path=list(path),
                        heading_level=level,
                        section_anchor=anchor,
                        content="\n".join(buffer).strip(),
                    )
                )
                buffer = []
                buf_chars = 0
            buffer.append(line)
            buf_chars += line_len

        if buffer:
            chunks.append(
                MdChunkDraft(
                    chunk_index=0,
                    heading_path=list(path),
                    heading_level=level,
                    section_anchor=anchor,
                    content="\n".join(buffer).strip(),
                )
            )

        return chunks

    @staticmethod
    def _make_anchor(text: str) -> str:
        import re

        anchor = text.lower()
        anchor = re.sub(r"[^\w\s-]", "", anchor)
        anchor = re.sub(r"\s+", "-", anchor).strip("-")
        return anchor

    @staticmethod
    def _update_path(path: list[str], level: int, text: str) -> list[str]:
        # Keep path up to level-1, then append current heading
        new_path = path[: level - 1]
        new_path.append(text)
        return new_path
