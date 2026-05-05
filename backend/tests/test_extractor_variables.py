from __future__ import annotations

from backend.app.graph.extractor import GraphExtractor, GraphNodeType
from backend.app.graph.parser import GraphParser


def _extract(source_text: str, file_path: str = "service.py"):
    parsed = GraphParser().parse_source(file_path=file_path, source_text=source_text)
    return {node.qualified_name: node for node in GraphExtractor().extract(parsed).nodes}


def test_extractor_captures_module_level_constant():
    nodes = _extract('API_VERSION = "v1"\n')
    assert "service.API_VERSION" in nodes
    constant = nodes["service.API_VERSION"]
    assert constant.node_type is GraphNodeType.CONSTANT
    assert constant.role == "constant"
    assert constant.signature is not None
    assert "API_VERSION" in constant.signature
    assert constant.parent_qualified_name == "service"


def test_extractor_truncates_long_signature_to_120_chars():
    long_value = "x" * 500
    nodes = _extract(f'LONG = "{long_value}"\n')
    constant = nodes["service.LONG"]
    assert constant.signature is not None
    assert len(constant.signature) <= 120


def test_extractor_classifies_type_var_as_type_alias():
    nodes = _extract(
        'from typing import TypeVar\n'
        'T = TypeVar("T", bound=object)\n'
    )
    assert "service.T" in nodes
    alias = nodes["service.T"]
    assert alias.node_type is GraphNodeType.TYPE_ALIAS
    assert alias.role == "type_alias"


def test_extractor_classifies_new_type_as_type_alias():
    nodes = _extract(
        'from typing import NewType\n'
        'UserId = NewType("UserId", int)\n'
    )
    alias = nodes["service.UserId"]
    assert alias.node_type is GraphNodeType.TYPE_ALIAS


def test_extractor_captures_class_attribute():
    nodes = _extract(
        "class User:\n"
        '    name: str = ""\n'
        "    email: str = \"\"\n"
    )
    assert "service.User.name" in nodes
    assert "service.User.email" in nodes
    name_attr = nodes["service.User.name"]
    assert name_attr.node_type is GraphNodeType.ATTRIBUTE
    assert name_attr.role == "attribute"
    assert name_attr.parent_qualified_name == "service.User"


def test_extractor_skips_local_variables_in_functions():
    nodes = _extract(
        "def helper() -> int:\n"
        "    x = 1\n"
        "    y = 2\n"
        "    return x + y\n"
    )
    assert "service.helper.x" not in nodes
    assert "service.helper.y" not in nodes


def test_extractor_skips_loop_variables():
    nodes = _extract(
        "def iterate(items):\n"
        "    for item in items:\n"
        "        pass\n"
    )
    assert "service.iterate.item" not in nodes


def test_extractor_captures_snake_case_module_variable():
    nodes = _extract('logger = get_logger(__name__)\n')
    assert "service.logger" in nodes
    variable = nodes["service.logger"]
    assert variable.node_type is GraphNodeType.VARIABLE
    assert variable.role == "helper"


def test_extractor_classifies_settings_as_config_role():
    nodes = _extract('settings = Settings()\n')
    variable = nodes["service.settings"]
    assert variable.role == "config"


def test_module_node_covers_full_file_byte_range():
    nodes = _extract("API_VERSION = \"v1\"\n")
    module = nodes["service"]
    assert module.start_byte == 0
    assert module.end_byte > 0


def test_extractor_pep695_plain_type_alias_uses_bare_identifier():
    nodes = _extract("type UserId = int\n")
    assert "service.UserId" in nodes
    alias = nodes["service.UserId"]
    assert alias.node_type is GraphNodeType.TYPE_ALIAS


def test_extractor_pep695_generic_type_alias_strips_type_parameters():
    # Regression for H4: `type Box[T] = list[T]` must store qualified_name as
    # `service.Box`, not `service.Box[T]`, so `from service import Box`
    # resolves against the consumer's plain identifier.
    nodes = _extract("type Box[T] = list[T]\n")
    assert "service.Box" in nodes
    assert "service.Box[T]" not in nodes
    alias = nodes["service.Box"]
    assert alias.node_type is GraphNodeType.TYPE_ALIAS
    assert alias.name == "Box"
