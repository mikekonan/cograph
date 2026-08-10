from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(slots=True, kw_only=True)
class RepoDocumentChunkDraft:
    chunk_index: int
    heading_path: list[str]
    content: str


class RepoDocumentChunker:
    def __init__(
        self,
        *,
        max_words: int = 512,
        overlap_words: int = 64,
    ) -> None:
        self._max_words = max_words
        self._overlap_words = overlap_words

    def chunk(self, content: str) -> list[RepoDocumentChunkDraft]:
        normalized_content = content.strip()
        if not normalized_content:
            return []

        sections = self._split_sections(normalized_content)
        drafts: list[RepoDocumentChunkDraft] = []
        chunk_index = 0
        for heading_path, section_text in sections:
            section_chunks = self._window_section_text(section_text)
            for chunk_text in section_chunks:
                drafts.append(
                    RepoDocumentChunkDraft(
                        chunk_index=chunk_index,
                        heading_path=list(heading_path),
                        content=chunk_text,
                    )
                )
                chunk_index += 1
        return drafts

    def extract_title(self, file_path: str, content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped.removeprefix("# ").strip()
        return file_path.rsplit("/", 1)[-1]

    def _split_sections(self, content: str) -> list[tuple[list[str], str]]:
        lines = content.splitlines()
        sections: list[tuple[list[str], list[str]]] = []
        current_heading_path: list[str] = []
        current_lines: list[str] = []

        for line in lines:
            heading_match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
            if heading_match:
                if current_lines:
                    sections.append((list(current_heading_path), list(current_lines)))
                    current_lines = []
                level = len(heading_match.group(1))
                heading = heading_match.group(2).strip()
                current_heading_path = current_heading_path[: level - 1] + [heading]
                current_lines.append(line)
                continue
            current_lines.append(line)

        if current_lines:
            sections.append((list(current_heading_path), list(current_lines)))

        return [
            (heading_path, "\n".join(section_lines).strip())
            for heading_path, section_lines in sections
            if "\n".join(section_lines).strip()
        ]

    def _window_section_text(self, section_text: str) -> list[str]:
        words = section_text.split()
        if len(words) <= self._max_words:
            return [section_text]

        window = self._max_words
        step = max(1, self._max_words - self._overlap_words)
        chunks: list[str] = []
        for start in range(0, len(words), step):
            chunk_words = words[start : start + window]
            if not chunk_words:
                break
            chunks.append(" ".join(chunk_words))
            if start + window >= len(words):
                break
        return chunks
