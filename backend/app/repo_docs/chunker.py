from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(slots=True, kw_only=True)
class RepoDocumentChunkDraft:
    chunk_index: int
    heading_path: list[str]
    content: str
    # 1-indexed, inclusive. Markdown chunks are re-flowed words: no lines.
    start_line: int | None = None
    end_line: int | None = None


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


# `key:` or `"quoted key":` followed by a space or the line end.
_YAML_KEY_RE = re.compile(r"""^("[^"]*"|'[^']*'|[^\s"'#{\[][^#]*?)[ \t]*:(?:[ \t]|$)""")
# A list item that names itself on its own line: `- name: pick`.
_YAML_ITEM_NAME_RE = re.compile(r"^(?:name|id|key|title)[ \t]*:[ \t]+(.+)$")
# `key: |` or `- >-`: the lines below are text, not YAML.
_YAML_BLOCK_SCALAR_RE = re.compile(r"(?:^-|:)[ \t]+[|>][-+0-9]*[ \t]*(?:#.*)?$")
_YAML_MAX_LABEL = 80


class YamlDocumentChunker:
    """Cut a YAML file along its own indentation, no YAML parser.

    A parser rejects templated files (`{{ ... }}`). Here the file is first
    laid out as atoms: a block that fits, or a block too big cut by its
    next level down to blocks that fit or windows of lines. Then adjacent
    atoms are merged while they fit, across levels, so the tail of a big
    block joins its next neighbour. The chunks cover every line of the file
    exactly once, in order.
    """

    def __init__(self, *, max_chars: int = 3000, max_line_chars: int = 400) -> None:
        # The embedder reads the first 4096 chars of heading path + text.
        self._max_chars = max_chars
        self._max_line_chars = max_line_chars

    def chunk(self, file_path: str, content: str) -> list[RepoDocumentChunkDraft]:
        if not content.strip():
            return []
        lines = content.splitlines()
        shown = [
            line
            if len(line) <= self._max_line_chars
            else line[: self._max_line_chars] + " …"
            for line in lines
        ]
        cut = _YamlCut(lines, [len(line) + 1 for line in shown], self._max_chars)
        if cut.size(0, len(lines)) <= self._max_chars:
            pieces = [(0, len(lines), [])]
        else:
            cut.region(0, len(lines), [], scan_from=0, docs=True)
            pieces = cut.merged()
        return [
            RepoDocumentChunkDraft(
                chunk_index=index,
                heading_path=[label[:_YAML_MAX_LABEL] for label in path],
                content="\n".join([f"# {file_path}", *shown[start:end]]),
                start_line=start + 1,
                end_line=end,
            )
            for index, (start, end, path) in enumerate(pieces)
        ]


class _YamlCut:
    """One file's lines and the atoms laid out so far: (start, end, path),
    0-indexed, end exclusive, `path` the full path down to the atom."""

    def __init__(self, lines: list[str], sizes: list[int], max_chars: int) -> None:
        self.lines = lines
        self.sizes = sizes
        self.max_chars = max_chars
        self.atoms: list[tuple[int, int, list[str]]] = []

    def size(self, start: int, end: int) -> int:
        return sum(self.sizes[start:end])

    def region(
        self, start: int, end: int, path: list[str], *, scan_from: int, docs: bool
    ) -> None:
        """Lay out lines start..end as atoms; blocks are looked for from `scan_from` on."""
        blocks = self.blocks(start, end, scan_from=scan_from, docs=docs)
        if not blocks:
            self.windows(start, end, path)
            return
        for block_start, block_end, label, head in blocks:
            if self.size(block_start, block_end) > self.max_chars:
                self.block(block_start, block_end, [*path, label], head)
            else:
                self.atoms.append((block_start, block_end, [*path, label]))

    def merged(self) -> list[tuple[int, int, list[str]]]:
        """Adjacent atoms joined while they fit."""
        groups: list[list[tuple[int, int, list[str]]]] = []
        for atom in self.atoms:
            if groups and self.size(groups[-1][0][0], atom[1]) <= self.max_chars:
                groups[-1].append(atom)
            else:
                groups.append([atom])
        return [
            (group[0][0], group[-1][1], _span_path(group[0][2], group[-1][2]))
            for group in groups
        ]

    def block(self, start: int, end: int, path: list[str], head: int | None) -> None:
        """Cut one block too big for a chunk by its next level."""
        if head is None:  # a whole document of a multi-document file
            self.region(start, end, path, scan_from=start, docs=False)
        elif _YAML_BLOCK_SCALAR_RE.search(self.lines[head].strip()):
            self.windows(start, end, path)
        else:
            # The head line and the comments above it stay in the first part.
            self.region(start, end, path, scan_from=head + 1, docs=False)

    def windows(self, start: int, end: int, path: list[str]) -> None:
        """No structure left: consecutive runs of lines that fit."""
        while start < end:
            stop, total = start + 1, self.sizes[start]
            while stop < end and total + self.sizes[stop] <= self.max_chars:
                total += self.sizes[stop]
                stop += 1
            self.atoms.append((start, stop, [*path, f"lines {start + 1}-{stop}"]))
            start = stop

    def blocks(
        self, start: int, end: int, *, scan_from: int, docs: bool
    ) -> list[tuple[int, int, str, int | None]]:
        """Blocks of lines start..end as (start, end, label, head line).

        The first block also takes the lines before it, and every block the
        comments and blank lines just above its head.
        """
        lines = self.lines
        if docs:
            separators = [
                i
                for i in range(scan_from + 1, end)
                if lines[i].rstrip() == "---" or lines[i].startswith("--- ")
            ]
            bounds = [start, *separators, end]
            parts = list(zip(bounds, bounds[1:]))
            if len(parts) > 1 and not any(map(_is_significant, lines[slice(*parts[0])])):
                # Only comments before the first `---`: they open document 1.
                parts[:2] = [(parts[0][0], parts[1][1])]
            if len(parts) > 1:
                return [(s, e, f"doc {n}", None) for n, (s, e) in enumerate(parts, 1)]

        significant = [i for i in range(scan_from, end) if _is_significant(lines[i])]
        if not significant:
            return []
        level = min(_indent(lines[i]) for i in significant)
        at_level = [i for i in significant if _indent(lines[i]) == level]
        heads = [i for i in at_level if _key_label(lines[i]) is not None]
        if not heads:
            # A list under a key at the same indent belongs to that key; only
            # a level with no keys is cut by its items.
            heads = [i for i in at_level if _is_item(lines[i])]
        if not heads:
            return []

        starts = [start]
        for head in heads[1:]:
            block_start = head
            while not _is_significant(lines[block_start - 1]):
                block_start -= 1
            starts.append(block_start)
        ends = [*starts[1:], end]
        return [
            (s, e, _key_label(lines[h]) or _item_label(lines[h], n), h)
            for n, (s, e, h) in enumerate(zip(starts, ends, heads))
        ]


def _span_path(first: list[str], last: list[str]) -> list[str]:
    """Path of a chunk from atom `first` to atom `last`: the head they share,
    then "A … B" for where they part."""
    if first == last:
        return first
    shared = 0
    while shared < min(len(first), len(last)) - 1 and first[shared] == last[shared]:
        shared += 1
    a, b = first[shared], last[shared]
    return [*first[:shared], a if a == b else f"{a} … {b}"]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _is_significant(line: str) -> bool:
    """Not blank, a comment, a template line or a document marker."""
    text = line.strip()
    if not text or text.startswith(("#", "{{", "{%")):
        return False
    return not (line.rstrip() in ("---", "...") or line.startswith("--- "))


def _is_item(line: str) -> bool:
    text = line.strip()
    return text == "-" or text.startswith("- ")


def _key_label(line: str) -> str | None:
    if _is_item(line):
        return None
    match = _YAML_KEY_RE.match(line.strip())
    return match.group(1).strip("\"'") if match else None


def _item_label(line: str, index: int) -> str:
    match = _YAML_ITEM_NAME_RE.match(line.strip()[1:].strip())
    if match:
        value = match.group(1).split(" #", 1)[0].strip().strip("\"'")
        if value:
            return value
    return f"[{index}]"
