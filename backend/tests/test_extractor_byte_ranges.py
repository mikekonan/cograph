from __future__ import annotations

from backend.app.graph.extractor import GraphExtractor
from backend.app.graph.parser import GraphParser


def _extract(source_text: str):
    parsed = GraphParser().parse_source(file_path="service.py", source_text=source_text)
    return GraphExtractor().extract(parsed), source_text.encode("utf-8")


def test_byte_range_slices_return_original_content_for_each_symbol():
    source_text = (
        '"""Module doc"""\n'
        "\n"
        "def greet(name: str) -> str:\n"
        '    return f"Hello, {name}"\n'
        "\n"
        "def farewell(name: str) -> str:\n"
        '    return f"Bye, {name}"\n'
    )
    extracted, source_bytes = _extract(source_text)
    for node in extracted.nodes:
        slice_bytes = source_bytes[node.start_byte:node.end_byte]
        assert slice_bytes.decode("utf-8") == node.content


def test_byte_range_handles_cyrillic_correctly():
    source_text = (
        "def приветствие(имя: str) -> str:\n"
        "    return f\"Привет, {имя}\"\n"
    )
    extracted, source_bytes = _extract(source_text)
    function_node = next(
        node for node in extracted.nodes if node.name == "приветствие"
    )
    slice_bytes = source_bytes[function_node.start_byte:function_node.end_byte]
    decoded = slice_bytes.decode("utf-8")
    assert "приветствие" in decoded
    assert decoded == function_node.content


def test_byte_range_handles_mixed_emoji_and_ascii():
    source_text = (
        'MESSAGE = "Привет 🎉 hi"\n'
        "def shout() -> str:\n"
        "    return MESSAGE\n"
    )
    extracted, source_bytes = _extract(source_text)
    function_node = next(node for node in extracted.nodes if node.name == "shout")
    slice_bytes = source_bytes[function_node.start_byte:function_node.end_byte]
    assert slice_bytes.decode("utf-8") == function_node.content


def test_byte_ranges_are_monotonic_within_file():
    source_text = (
        "def a() -> None: pass\n"
        "def b() -> None: pass\n"
        "def c() -> None: pass\n"
    )
    extracted, _ = _extract(source_text)
    definitions = sorted(
        (node for node in extracted.nodes if node.name in ("a", "b", "c")),
        key=lambda n: n.start_byte,
    )
    for previous, current in zip(definitions, definitions[1:], strict=False):
        assert previous.end_byte <= current.start_byte


def test_empty_init_module_has_none_byte_range():
    # Regression: a 0-byte __init__.py previously produced start_byte=0,
    # end_byte=0 which violates the DB constraint end_byte > start_byte.
    # The fix sets both fields to None for empty files.
    parsed = GraphParser().parse_source(file_path="pkg/__init__.py", source_text="")
    extracted = GraphExtractor().extract(parsed)

    module_node = next(node for node in extracted.nodes)
    assert module_node.start_byte is None
    assert module_node.end_byte is None
