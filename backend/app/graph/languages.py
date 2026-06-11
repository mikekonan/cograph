from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class GraphLanguage(StrEnum):
    PYTHON = "python"
    GO = "go"


@dataclass(slots=True, frozen=True, kw_only=True)
class LanguageDefinition:
    language: GraphLanguage
    parser_name: str
    file_extensions: tuple[str, ...]


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

_LANGUAGES: dict[GraphLanguage, LanguageDefinition] = {
    GraphLanguage.PYTHON: PYTHON,
    GraphLanguage.GO: GO,
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
