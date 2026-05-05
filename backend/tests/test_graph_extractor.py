from __future__ import annotations

from pathlib import Path

from backend.app.graph.extractor import GraphEdgeType, GraphExtractor, GraphNodeType
from backend.app.graph.parser import GraphParser


def _parse_go_fixture(
    *,
    fixture_root: Path,
    relative_path: str,
):
    source_path = fixture_root / relative_path
    return GraphParser().parse_source(
        file_path=relative_path,
        source_text=source_path.read_text(encoding="utf-8"),
    )


def test_graph_extractor_builds_python_symbols_and_edges():
    source_text = '''"""Module docs"""
import os
from pkg import mod as alias, util

class UserService(BaseService):
    """Service docs"""

    @router.post("/login")
    async def login(self, user_id: str) -> bool:
        """Login docs"""
        helper(user_id)
        self.audit(user_id)
        client.auth.login(user_id)
        return True


def helper(value: str) -> str:
    """Helper docs"""
    return normalize(value)
'''
    parsed = GraphParser().parse_source(
        file_path="service.py",
        source_text=source_text,
    )

    extracted = GraphExtractor().extract(parsed)

    nodes = {node.qualified_name: node for node in extracted.nodes}
    edges = {(edge.edge_type, edge.source, edge.target) for edge in extracted.edges}

    assert set(nodes) == {
        "service",
        "service.UserService",
        "service.UserService.login",
        "service.helper",
    }

    module_node = nodes["service"]
    assert module_node.node_type is GraphNodeType.MODULE
    assert module_node.doc_comment == "Module docs"

    class_node = nodes["service.UserService"]
    assert class_node.node_type is GraphNodeType.CLASS
    assert class_node.doc_comment == "Service docs"
    assert class_node.metadata["bases"] == ["BaseService"]

    method_node = nodes["service.UserService.login"]
    assert method_node.node_type is GraphNodeType.METHOD
    assert method_node.signature == "async def login(self, user_id: str) -> bool"
    assert method_node.doc_comment == "Login docs"
    assert method_node.metadata["async"] is True
    assert method_node.metadata["decorators"] == ['@router.post("/login")']
    assert method_node.parent_qualified_name == "service.UserService"

    helper_node = nodes["service.helper"]
    assert helper_node.node_type is GraphNodeType.FUNCTION
    assert helper_node.signature == "def helper(value: str) -> str"
    assert helper_node.doc_comment == "Helper docs"

    assert (GraphEdgeType.IMPORTS, "service", "os") in edges
    # Aliased imports carry `as <local>` so the builder can resolve call-sites
    # that use the alias (see test_graph_builder_resolves_aliased_from_import).
    assert (GraphEdgeType.IMPORTS, "service", "pkg.mod as alias") in edges
    assert (GraphEdgeType.IMPORTS, "service", "pkg.util") in edges
    assert (GraphEdgeType.DECLARES, "service", "service.UserService") in edges
    assert (GraphEdgeType.DECLARES, "service", "service.helper") in edges
    assert (
        GraphEdgeType.DECLARES,
        "service.UserService",
        "service.UserService.login",
    ) in edges
    assert (
        GraphEdgeType.INHERITS,
        "service.UserService",
        "BaseService",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "service.UserService.login",
        "helper",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "service.UserService.login",
        "self.audit",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "service.UserService.login",
        "client.auth.login",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "service.helper",
        "normalize",
    ) in edges


# Keep interface coverage inline: the checked-in go-types fixture slice is
# rich in real package shape, but it does not include an interface declaration.
def test_graph_extractor_builds_go_inline_interface_symbols_and_edges():
    source_text = """package service

import (
    "fmt"
    localutils "pkg/utils"
)

type UserService struct {
    BaseService
}

type Repo interface {
    Save(userID string) error
}

type UserID = string

func (s *UserService) Login(userID string) error {
    Helper(userID)
    s.audit(userID)
    localutils.Normalize(userID)
    fmt.Println(userID)
    return nil
}

func Helper(value string) string {
    return value
}
"""
    parsed = GraphParser().parse_source(
        file_path="service/auth.go",
        source_text=source_text,
    )

    extracted = GraphExtractor().extract(parsed)

    nodes = {node.qualified_name: node for node in extracted.nodes}
    edges = {(edge.edge_type, edge.source, edge.target) for edge in extracted.edges}

    assert set(nodes) == {
        "service.auth",
        "service.UserService",
        "service.UserService.Login",
        "service.Repo",
        "service.Repo.Save",
        "service.UserID",
        "service.Helper",
    }

    module_node = nodes["service.auth"]
    assert module_node.node_type is GraphNodeType.MODULE
    assert module_node.metadata["package_name"] == "service"
    assert module_node.metadata["package_qualified_name"] == "service"

    struct_node = nodes["service.UserService"]
    assert struct_node.node_type is GraphNodeType.STRUCT
    assert struct_node.metadata["embeds"] == ["service.BaseService"]

    interface_node = nodes["service.Repo"]
    assert interface_node.node_type is GraphNodeType.INTERFACE

    interface_method = nodes["service.Repo.Save"]
    assert interface_method.node_type is GraphNodeType.METHOD
    assert interface_method.parent_qualified_name == "service.Repo"

    login_node = nodes["service.UserService.Login"]
    assert login_node.node_type is GraphNodeType.METHOD
    assert login_node.signature == "func (s *UserService) Login(userID string) error"
    assert login_node.metadata["receiver_name"] == "s"
    assert login_node.metadata["receiver_type"] == "UserService"

    alias_node = nodes["service.UserID"]
    assert alias_node.node_type is GraphNodeType.TYPE_ALIAS

    helper_node = nodes["service.Helper"]
    assert helper_node.node_type is GraphNodeType.FUNCTION
    assert helper_node.signature == "func Helper(value string) string"

    assert (GraphEdgeType.IMPORTS, "service.auth", "fmt") in edges
    assert (GraphEdgeType.IMPORTS, "service.auth", "pkg.utils as localutils") in edges
    assert (GraphEdgeType.DECLARES, "service.auth", "service.UserService") in edges
    assert (GraphEdgeType.DECLARES, "service.auth", "service.Repo") in edges
    assert (GraphEdgeType.DECLARES, "service.auth", "service.Helper") in edges
    assert (
        GraphEdgeType.DECLARES,
        "service.UserService",
        "service.UserService.Login",
    ) in edges
    assert (
        GraphEdgeType.DECLARES,
        "service.Repo",
        "service.Repo.Save",
    ) in edges
    assert (
        GraphEdgeType.INHERITS,
        "service.UserService",
        "service.BaseService",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "service.UserService.Login",
        "Helper",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "service.UserService.Login",
        "s.audit",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "service.UserService.Login",
        "localutils.Normalize",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "service.UserService.Login",
        "fmt.Println",
    ) in edges


def test_graph_extractor_builds_go_types_fixture_symbols_and_edges(
    go_types_fixture_root: Path,
    go_types_fixture_module_path: str,
):
    parsed = _parse_go_fixture(
        fixture_root=go_types_fixture_root,
        relative_path="bcp47_language/bcp47_language.go",
    )

    extracted = GraphExtractor().extract(
        parsed,
        go_module_path=go_types_fixture_module_path,
    )

    nodes = {node.qualified_name: node for node in extracted.nodes}
    edges = {(edge.edge_type, edge.source, edge.target) for edge in extracted.edges}

    assert "bcp47_language.bcp47_language" in nodes
    assert "bcp47_language.Language" in nodes
    assert "bcp47_language.Language.BaseISO639Language" in nodes

    module_node = nodes["bcp47_language.bcp47_language"]
    assert module_node.node_type is GraphNodeType.MODULE
    assert module_node.metadata["package_name"] == "bcp47_language"
    assert module_node.metadata["package_qualified_name"] == "bcp47_language"

    struct_node = nodes["bcp47_language.Language"]
    assert struct_node.node_type is GraphNodeType.STRUCT

    method_node = nodes["bcp47_language.Language.BaseISO639Language"]
    assert method_node.node_type is GraphNodeType.METHOD
    assert (
        method_node.signature
        == "func (l Language) BaseISO639Language() (language.Language, error)"
    )
    assert method_node.parent_qualified_name == "bcp47_language.Language"
    assert method_node.metadata["receiver_name"] == "l"
    assert method_node.metadata["receiver_type"] == "Language"

    assert (
        GraphEdgeType.IMPORTS,
        "bcp47_language.bcp47_language",
        "database.sql.driver",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "bcp47_language.bcp47_language",
        "encoding.json",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "bcp47_language.bcp47_language",
        "fmt",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "bcp47_language.bcp47_language",
        "language",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "bcp47_language.bcp47_language",
        "golang.org.x.text.language as stdLanguage",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "bcp47_language.Language.BaseISO639Language",
        "language.ByAlpha2CodeStrErr",
    ) in edges


def test_graph_extractor_builds_go_types_fixture_type_alias_calls_and_imports(
    go_types_fixture_root: Path,
    go_types_fixture_module_path: str,
):
    parsed = _parse_go_fixture(
        fixture_root=go_types_fixture_root,
        relative_path="country/subdivision/code.go",
    )

    extracted = GraphExtractor().extract(
        parsed,
        go_module_path=go_types_fixture_module_path,
    )

    nodes = {node.qualified_name: node for node in extracted.nodes}
    edges = {(edge.edge_type, edge.source, edge.target) for edge in extracted.edges}

    assert "country.subdivision.code" in nodes
    assert "country.subdivision.Code" in nodes
    assert "country.subdivision.Code.ValidateForCountry" in nodes

    module_node = nodes["country.subdivision.code"]
    assert module_node.node_type is GraphNodeType.MODULE
    assert module_node.metadata["package_name"] == "subdivision"
    assert module_node.metadata["package_qualified_name"] == "country.subdivision"

    alias_node = nodes["country.subdivision.Code"]
    assert alias_node.node_type is GraphNodeType.TYPE_ALIAS

    validate_node = nodes["country.subdivision.Code.ValidateForCountry"]
    assert validate_node.node_type is GraphNodeType.METHOD
    assert validate_node.metadata["receiver_name"] == "code"
    assert validate_node.metadata["receiver_type"] == "Code"

    assert (
        GraphEdgeType.IMPORTS,
        "country.subdivision.code",
        "country",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "country.subdivision.code",
        "internal.utils",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "country.subdivision.Code.UnmarshalJSON",
        "utils.UnsafeStringFromJson",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "country.subdivision.Code.ValidateForCountry",
        "country.ByAlpha2CodeErr",
    ) in edges
