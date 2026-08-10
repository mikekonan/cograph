from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class GraphLanguage(StrEnum):
    PYTHON = "python"
    GO = "go"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"


@dataclass(slots=True, frozen=True, kw_only=True)
class LanguageDefinition:
    language: GraphLanguage
    parser_name: str
    file_extensions: tuple[str, ...]
    # Extension-specific grammar overrides. TypeScript needs two grammars:
    # `typescript` cannot parse JSX and `tsx` mis-parses legacy `<T>expr`
    # casts, so `.tsx` files get the `tsx` grammar while `.ts` keeps
    # `typescript`. The DB/API `language` string stays the enum value.
    parser_name_by_extension: dict[str, str] = field(default_factory=dict)

    def parser_name_for(self, file_path: str | Path) -> str:
        suffix = Path(file_path).suffix.lower()
        return self.parser_name_by_extension.get(suffix, self.parser_name)

    def parser_names(self) -> tuple[str, ...]:
        return (self.parser_name, *self.parser_name_by_extension.values())


PYTHON = LanguageDefinition(
    language=GraphLanguage.PYTHON,
    parser_name="python",
    file_extensions=(".py", ".pyi"),
)

GO = LanguageDefinition(
    language=GraphLanguage.GO,
    parser_name="go",
    file_extensions=(".go",),
)

TYPESCRIPT = LanguageDefinition(
    language=GraphLanguage.TYPESCRIPT,
    parser_name="typescript",
    file_extensions=(".ts", ".tsx", ".mts", ".cts"),
    parser_name_by_extension={".tsx": "tsx"},
)

JAVASCRIPT = LanguageDefinition(
    language=GraphLanguage.JAVASCRIPT,
    parser_name="javascript",
    file_extensions=(".js", ".jsx", ".mjs", ".cjs"),
)

_LANGUAGES: dict[GraphLanguage, LanguageDefinition] = {
    GraphLanguage.PYTHON: PYTHON,
    GraphLanguage.GO: GO,
    GraphLanguage.TYPESCRIPT: TYPESCRIPT,
    GraphLanguage.JAVASCRIPT: JAVASCRIPT,
}
_EXTENSION_TO_LANGUAGE = {
    extension: definition.language
    for definition in _LANGUAGES.values()
    for extension in definition.file_extensions
}


def detect_graph_language(file_path: str | Path) -> GraphLanguage | None:
    suffix = Path(file_path).suffix.lower()
    return _EXTENSION_TO_LANGUAGE.get(suffix)


def get_language_definition(language: GraphLanguage) -> LanguageDefinition:
    return _LANGUAGES[language]


def iter_language_definitions() -> tuple[LanguageDefinition, ...]:
    return tuple(_LANGUAGES.values())
