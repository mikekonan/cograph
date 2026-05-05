from __future__ import annotations

from backend.app.graph.extractor import GraphExtractor, GraphLanguage, compute_symbol_key
from backend.app.graph.parser import GraphParser


def test_symbol_key_is_deterministic_for_same_inputs():
    first = compute_symbol_key(
        language=GraphLanguage.PYTHON,
        qualified_name="app.auth.login",
        signature="def login(credentials: str) -> str",
    )
    second = compute_symbol_key(
        language=GraphLanguage.PYTHON,
        qualified_name="app.auth.login",
        signature="def login(credentials: str) -> str",
    )
    assert first == second


def test_symbol_key_changes_when_signature_changes():
    old_key = compute_symbol_key(
        language=GraphLanguage.PYTHON,
        qualified_name="app.auth.login",
        signature="def login(credentials: str) -> str",
    )
    new_key = compute_symbol_key(
        language=GraphLanguage.PYTHON,
        qualified_name="app.auth.login",
        signature="def login(credentials: str, mfa: bool) -> str",
    )
    assert old_key != new_key


def test_symbol_key_includes_language_and_qualified_name():
    key = compute_symbol_key(
        language=GraphLanguage.PYTHON,
        qualified_name="app.auth.login",
        signature=None,
    )
    assert key.startswith("python:app.auth.login:")


def test_extractor_produces_symbol_keys_for_all_nodes():
    source_text = "def helper(value: str) -> str:\n    return value\n"
    parsed = GraphParser().parse_source(file_path="service.py", source_text=source_text)
    extracted = GraphExtractor().extract(parsed)
    for node in extracted.nodes:
        assert node.symbol_key
        assert node.symbol_key.startswith("python:")


def test_extractor_preserves_symbol_key_when_body_changes_but_signature_stays():
    parser = GraphParser()
    extractor = GraphExtractor()
    first = extractor.extract(
        parser.parse_source(
            file_path="service.py",
            source_text="def helper(value: str) -> str:\n    return value\n",
        )
    )
    second = extractor.extract(
        parser.parse_source(
            file_path="service.py",
            source_text="def helper(value: str) -> str:\n    return value.strip()\n",
        )
    )
    first_keys = {node.qualified_name: node.symbol_key for node in first.nodes}
    second_keys = {node.qualified_name: node.symbol_key for node in second.nodes}
    assert first_keys["service.helper"] == second_keys["service.helper"]


def test_extractor_rotates_symbol_key_when_signature_changes():
    parser = GraphParser()
    extractor = GraphExtractor()
    first = extractor.extract(
        parser.parse_source(
            file_path="service.py",
            source_text="def helper(value: str) -> str:\n    return value\n",
        )
    )
    second = extractor.extract(
        parser.parse_source(
            file_path="service.py",
            source_text="def helper(value: str, strip: bool) -> str:\n    return value\n",
        )
    )
    first_keys = {node.qualified_name: node.symbol_key for node in first.nodes}
    second_keys = {node.qualified_name: node.symbol_key for node in second.nodes}
    assert first_keys["service.helper"] != second_keys["service.helper"]
