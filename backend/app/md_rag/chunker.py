"""Heading-aware markdown chunker.

Chunks respect heading boundaries — a chunk never splits a heading section.
Each chunk carries its heading_path, heading_level, and section_anchor.

Invariants:

- Tables (consecutive ``|`` rows) and fenced code blocks (``` … ```) are
  treated as atomic units; the chunker never cuts through them.
- When the trailing buffer is below ``min_chars`` it is merged into the
  previous chunk instead of being emitted as a tail micro-chunk.
- When sections are merged into one chunk, ``heading_path`` is the deepest
  common prefix of all merged section paths (falling back to the first
  merged section's path when no prefix is shared).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True, kw_only=True)
class MdChunkDraft:
    chunk_index: int
    heading_path: list[str]
    heading_level: int | None
    section_anchor: str | None
    content: str


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


class MdChunker:
    """Chunk markdown by heading boundaries with a char budget.

    ``max_chars`` is the soft ceiling per chunk — exceeded only when a single
    atomic block (table or code fence) is itself larger.

    ``min_chars`` is the floor used to merge trailing buffers into the
    previous chunk so we don't emit tail micro-chunks at end-of-doc.
    """

    def __init__(
        self,
        max_chars: int = 4000,
        max_chunks: int = 512,
        min_chars: int = 400,
    ) -> None:
        self.max_chars = max_chars
        self.max_chunks = max_chunks
        self.min_chars = min_chars

    def chunk(self, text: str) -> list[MdChunkDraft]:
        lines = text.splitlines()
        sections = self._split_by_headings(lines)

        chunks: list[MdChunkDraft] = []
        current_buffer: list[str] = []
        current_paths: list[list[str]] = []
        current_levels: list[int | None] = []
        current_anchors: list[str | None] = []
        current_chars = 0

        def flush_buffer() -> None:
            nonlocal current_buffer, current_paths, current_levels, current_anchors, current_chars
            if not current_buffer:
                return
            path, level, anchor = _merge_metadata(
                current_paths, current_levels, current_anchors
            )
            chunks.append(
                MdChunkDraft(
                    chunk_index=len(chunks),
                    heading_path=path,
                    heading_level=level,
                    section_anchor=anchor,
                    content="\n".join(current_buffer).strip(),
                )
            )
            current_buffer = []
            current_paths = []
            current_levels = []
            current_anchors = []
            current_chars = 0

        for section_lines, path, level, anchor in sections:
            section_text = "\n".join(section_lines)
            section_len = len(section_text)

            if section_len > self.max_chars:
                flush_buffer()
                sub_chunks = self._split_section(section_lines, path, level, anchor)
                for sub in sub_chunks:
                    sub.chunk_index = len(chunks)
                    chunks.append(sub)
                continue

            if current_chars + section_len > self.max_chars and current_buffer:
                flush_buffer()

            current_buffer.extend(section_lines)
            current_paths.append(path)
            current_levels.append(level)
            current_anchors.append(anchor)
            current_chars += section_len + 1

        if current_buffer:
            if (
                current_chars < self.min_chars
                and chunks
                and len(chunks[-1].content) + current_chars <= self.max_chars * 2
            ):
                prev = chunks[-1]
                tail_text = "\n".join(current_buffer).strip()
                prev.content = f"{prev.content}\n{tail_text}".strip() if tail_text else prev.content
            else:
                flush_buffer()

        return chunks[: self.max_chunks]

    def _split_by_headings(
        self, lines: list[str]
    ) -> list[tuple[list[str], list[str], int | None, str | None]]:
        """Split lines into (section_lines, heading_path, level, anchor)."""

        sections: list[tuple[list[str], list[str], int | None, str | None]] = []
        current_lines: list[str] = []
        current_path: list[str] = []
        current_level: int | None = None
        current_anchor: str | None = None

        for line in lines:
            match = _HEADING_RE.match(line)
            if match:
                if current_lines:
                    sections.append(
                        (
                            current_lines,
                            list(current_path),
                            current_level,
                            current_anchor,
                        )
                    )
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
        """Split an oversized section without cutting through tables or code fences."""

        blocks = _group_atomic_blocks(lines)
        chunks: list[MdChunkDraft] = []
        buffer: list[str] = []
        buf_chars = 0

        for block in blocks:
            block_text = "\n".join(block)
            block_len = len(block_text)

            if block_len > self.max_chars:
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
                    buffer = []
                    buf_chars = 0
                chunks.append(
                    MdChunkDraft(
                        chunk_index=0,
                        heading_path=list(path),
                        heading_level=level,
                        section_anchor=anchor,
                        content=block_text.strip(),
                    )
                )
                continue

            if buf_chars + block_len + 1 > self.max_chars and buffer:
                chunks.append(
                    MdChunkDraft(
                        chunk_index=0,
                        heading_path=list(path),
                        heading_level=level,
                        section_anchor=anchor,
                        content="\n".join(buffer).strip(),
                    )
                )
                buffer = []
                buf_chars = 0

            buffer.extend(block)
            buf_chars += block_len + 1

        if buffer:
            if (
                buf_chars < self.min_chars
                and chunks
                and len(chunks[-1].content) + buf_chars <= self.max_chars * 2
            ):
                prev = chunks[-1]
                tail_text = "\n".join(buffer).strip()
                prev.content = f"{prev.content}\n{tail_text}".strip() if tail_text else prev.content
            else:
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
        anchor = text.lower()
        anchor = re.sub(r"[^\w\s-]", "", anchor)
        anchor = re.sub(r"\s+", "-", anchor).strip("-")
        return anchor

    @staticmethod
    def _update_path(path: list[str], level: int, text: str) -> list[str]:
        new_path = path[: level - 1]
        new_path.append(text)
        return new_path


def _merge_metadata(
    paths: list[list[str]],
    levels: list[int | None],
    anchors: list[str | None],
) -> tuple[list[str], int | None, str | None]:
    """Merge metadata across sections sharing one chunk.

    Returns the deepest common prefix of all paths (or the first non-empty
    path if no prefix is shared). The level matches the prefix depth; the
    anchor is the first section's anchor only when that section's path equals
    the chosen prefix, otherwise it is dropped (no single anchor represents a
    merged chunk).
    """

    if not paths:
        return [], None, None

    if len(paths) == 1:
        return list(paths[0]), levels[0], anchors[0]

    common: list[str] = []
    for parts in zip(*paths, strict=False):
        first = parts[0]
        if all(p == first for p in parts):
            common.append(first)
        else:
            break

    if not common:
        for path, level, anchor in zip(paths, levels, anchors, strict=False):
            if path:
                return list(path), level, anchor
        return [], None, None

    level = len(common)
    anchor = anchors[0] if paths[0] == common else None
    return common, level, anchor


def _group_atomic_blocks(lines: list[str]) -> list[list[str]]:
    """Group lines into atomic blocks: tables, fenced code, and paragraphs.

    A paragraph is a run of lines separated by blank lines. A table is a run
    of pipe-delimited lines. A fenced code block runs from one ``` line to
    the next.
    """

    blocks: list[list[str]] = []
    current: list[str] = []
    in_code_fence = False

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append(current)
            current = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.lstrip()

        if not in_code_fence and stripped.startswith("```"):
            flush()
            fence: list[str] = [line]
            i += 1
            while i < n:
                fence.append(lines[i])
                if lines[i].lstrip().startswith("```"):
                    i += 1
                    break
                i += 1
            blocks.append(fence)
            continue

        if not in_code_fence and stripped.startswith("|"):
            flush()
            table: list[str] = []
            while i < n and lines[i].lstrip().startswith("|"):
                table.append(lines[i])
                i += 1
            blocks.append(table)
            continue

        if line.strip() == "":
            flush()
            blocks.append([line])
            i += 1
            continue

        current.append(line)
        i += 1

    flush()
    return blocks
