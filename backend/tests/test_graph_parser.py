from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.graph.languages import GraphLanguage, detect_graph_language
from backend.app.graph.parser import GraphParser, UnsupportedLanguageError


def test_detect_graph_language_for_known_paths():
    assert detect_graph_language("service.py") is GraphLanguage.PYTHON
    assert detect_graph_language("service.pyi") is GraphLanguage.PYTHON
    assert detect_graph_language("service.go") is GraphLanguage.GO
    assert detect_graph_language("service.ts") is GraphLanguage.TYPESCRIPT
    assert detect_graph_language("Button.tsx") is GraphLanguage.TYPESCRIPT
    assert detect_graph_language("service.mts") is GraphLanguage.TYPESCRIPT
    assert detect_graph_language("service.cts") is GraphLanguage.TYPESCRIPT
    assert detect_graph_language("legacy.js") is GraphLanguage.JAVASCRIPT
    assert detect_graph_language("Widget.jsx") is GraphLanguage.JAVASCRIPT
    assert detect_graph_language("esm.mjs") is GraphLanguage.JAVASCRIPT
    assert detect_graph_language("cjs.cjs") is GraphLanguage.JAVASCRIPT
    assert detect_graph_language("service.rb") is None


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
            file_path="service.rb",
            source_text="def helper; end\n",
        )


def test_graph_parser_parses_typescript_and_tsx_with_distinct_grammars():
    # `.ts` must use the `typescript` grammar (legacy `<T>expr` casts parse),
    # `.tsx` must use `tsx` (JSX parses) — a single grammar can't do both.
    ts = GraphParser().parse_source(
        file_path="cast.ts",
        source_text="const n = <number>window.value;\n",
    )
    assert ts.language is GraphLanguage.TYPESCRIPT
    assert not ts.root_node.has_error

    tsx = GraphParser().parse_source(
        file_path="Button.tsx",
        source_text="export const Button = () => <button>ok</button>;\n",
    )
    assert tsx.language is GraphLanguage.TYPESCRIPT
    assert not tsx.root_node.has_error


def test_graph_parser_parses_javascript_with_jsx():
    parsed = GraphParser().parse_source(
        file_path="Widget.jsx",
        source_text="export function Widget() { return <div/>; }\n",
    )
    assert parsed.language is GraphLanguage.JAVASCRIPT
    assert not parsed.root_node.has_error
