from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.graph.languages import GraphLanguage, detect_graph_language
from backend.app.graph.parser import GraphParser, UnsupportedLanguageError


def test_detect_graph_language_for_python_paths():
    assert detect_graph_language("service.py") is GraphLanguage.PYTHON
    assert detect_graph_language("service.pyi") is GraphLanguage.PYTHON
    assert detect_graph_language("service.go") is GraphLanguage.GO
    assert detect_graph_language("service.ts") is None


def test_graph_parser_parses_python_source():
    parsed = GraphParser().parse_source(
        file_path="service.py",
        source_text="def helper(value: str) -> str:\n    return value\n",
    )

    assert parsed.language is GraphLanguage.PYTHON
    assert parsed.root_node.type == "module"
    assert parsed.path.as_posix() == "service.py"


def test_graph_parser_parses_go_source():
    parsed = GraphParser().parse_source(
        file_path="service.go",
        source_text='package service\n\nfunc Helper() string { return "ok" }\n',
    )

    assert parsed.language is GraphLanguage.GO
    assert parsed.root_node.type == "source_file"
    assert parsed.path.as_posix() == "service.go"


def test_graph_parser_parses_go_types_fixture_source(go_types_fixture_root: Path):
    fixture_path = go_types_fixture_root / "bcp47_language" / "bcp47_language.go"

    parsed = GraphParser().parse_source(
        file_path=fixture_path.relative_to(go_types_fixture_root),
        source_text=fixture_path.read_text(encoding="utf-8"),
    )

    assert parsed.language is GraphLanguage.GO
    assert parsed.root_node.type == "source_file"
    assert parsed.path.as_posix() == "bcp47_language/bcp47_language.go"


def test_graph_parser_rejects_unsupported_extensions():
    with pytest.raises(UnsupportedLanguageError):
        GraphParser().parse_source(
            file_path="service.ts",
            source_text="export const value = 1;\n",
        )
