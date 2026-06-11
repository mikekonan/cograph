from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Tree
from tree_sitter_language_pack import get_parser

from backend.app.graph.languages import (
    GraphLanguage,
    detect_graph_language,
    get_language_definition,
    iter_language_definitions,
)


class UnsupportedLanguageError(ValueError):
    """Raised when a file path does not map to a supported graph language."""


def missing_grammars() -> tuple[str, ...]:
    """Parser names whose grammar binaries are absent from the local cache.

    tree-sitter-language-pack v1.6+ ships NO grammars in the wheel —
    `get_parser` downloads them from GitHub releases on first use, with
    no timeout. A stalled CDN connection froze a prod sync for 10+
    minutes inside an open DB transaction (2026-06-11). Grammars are
    baked into the Docker image at build time; this check lets the
    worker fail loudly at startup if they're missing instead of hanging
    mid-job.
    """
    from tree_sitter_language_pack import downloaded_languages

    have = set(downloaded_languages())
    required = sorted(
        definition.parser_name for definition in iter_language_definitions()
    )
    return tuple(name for name in required if name not in have)


def download_missing_grammars() -> None:
    """Fetch any missing grammars into the local cache (blocking, network).

    Called from the Docker build (bakes the cache into the image) and
    from worker startup as a dev-environment fallback — always wrap in a
    timeout: the underlying downloader can stall indefinitely.
    """
    from tree_sitter_language_pack import download

    names = missing_grammars()
    if names:
        download(list(names))


@dataclass(slots=True, kw_only=True)
class ParsedFile:
    path: Path
    language: GraphLanguage
    source_text: str
    tree: Tree

    @property
    def root_node(self):
        return self.tree.root_node

    @property
    def source_bytes(self) -> bytes:
        return self.source_text.encode("utf-8")


class GraphParser:
    def __init__(self) -> None:
        self._parsers: dict[GraphLanguage, object] = {}

    def parse_source(self, *, file_path: str | Path, source_text: str) -> ParsedFile:
        path = Path(file_path)
        language = detect_graph_language(path)
        if language is None:
            raise UnsupportedLanguageError(f"Unsupported graph language for path: {path}")

        parser = self._get_parser(language)
        tree = parser.parse(source_text.encode("utf-8"))
        return ParsedFile(
            path=path,
            language=language,
            source_text=source_text,
            tree=tree,
        )

    def _get_parser(self, language: GraphLanguage):
        parser = self._parsers.get(language)
        if parser is None:
            parser_name = get_language_definition(language).parser_name
            parser = get_parser(parser_name)  # type: ignore[arg-type]
            self._parsers[language] = parser
        return parser
