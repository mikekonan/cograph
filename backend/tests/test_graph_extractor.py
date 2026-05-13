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
        "service.auth#module",
        "service.UserService",
        "service.UserService.Login",
        "service.Repo",
        "service.Repo.Save",
        "service.UserID",
        "service.Helper",
    }

    module_node = nodes["service.auth#module"]
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

    assert (GraphEdgeType.IMPORTS, "service.auth#module", "fmt") in edges
    assert (GraphEdgeType.IMPORTS, "service.auth#module", "pkg.utils as localutils") in edges
    assert (GraphEdgeType.DECLARES, "service.auth#module", "service.UserService") in edges
    assert (GraphEdgeType.DECLARES, "service.auth#module", "service.Repo") in edges
    assert (GraphEdgeType.DECLARES, "service.auth#module", "service.Helper") in edges
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

    assert "bcp47_language.bcp47_language#module" in nodes
    assert "bcp47_language.Language" in nodes
    assert "bcp47_language.Language.BaseISO639Language" in nodes

    module_node = nodes["bcp47_language.bcp47_language#module"]
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
        "bcp47_language.bcp47_language#module",
        "database.sql.driver",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "bcp47_language.bcp47_language#module",
        "encoding.json",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "bcp47_language.bcp47_language#module",
        "fmt",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "bcp47_language.bcp47_language#module",
        "language",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "bcp47_language.bcp47_language#module",
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

    assert "country.subdivision.code#module" in nodes
    assert "country.subdivision.Code" in nodes
    assert "country.subdivision.Code.ValidateForCountry" in nodes

    module_node = nodes["country.subdivision.code#module"]
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
        "country.subdivision.code#module",
        "country",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "country.subdivision.code#module",
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


def test_graph_extractor_disambiguates_go_init_with_file_stem():
    """Two `func init()` in different files of the same Go package each
    get their own QN suffixed with the file stem, so they survive the
    `UNIQUE(repository_id, qualified_name)` constraint and the build-
    variant collision guard. This is the goose-migrations pattern in
    real repos (e.g. svc/lavanderia).
    """
    file_a_source = """package migrations

import "github.com/pressly/goose/v3"

func init() {
    goose.AddMigrationNoTxContext(up_a, down_a)
}

func up_a()   {}
func down_a() {}
"""
    file_b_source = """package migrations

import "github.com/pressly/goose/v3"

func init() {
    goose.AddMigrationNoTxContext(up_b, down_b)
}

func up_b()   {}
func down_b() {}
"""
    parser = GraphParser()
    extractor = GraphExtractor()

    parsed_a = parser.parse_source(
        file_path="infrastructure/sql/migrations/20260327140002_add_idx_a.go",
        source_text=file_a_source,
    )
    parsed_b = parser.parse_source(
        file_path="infrastructure/sql/migrations/20260327140003_add_idx_b.go",
        source_text=file_b_source,
    )

    nodes_a = {n.qualified_name for n in extractor.extract(parsed_a).nodes}
    nodes_b = {n.qualified_name for n in extractor.extract(parsed_b).nodes}

    pkg = "infrastructure.sql.migrations"
    assert f"{pkg}.init@20260327140002_add_idx_a" in nodes_a
    assert f"{pkg}.init@20260327140003_add_idx_b" in nodes_b
    # The bare `<pkg>.init` form must not be emitted anymore — that was
    # the source of the collision.
    assert f"{pkg}.init" not in nodes_a
    assert f"{pkg}.init" not in nodes_b


def test_graph_extractor_disambiguates_go_blank_function_with_file_stem():
    """`func _()` is also legal in Go (compile-time side-effect helpers).
    Same treatment as init — file-stem suffix on the QN.
    """
    source = """package compiletime

func _() {
    _ = "registers something at link time"
}
"""
    parsed = GraphParser().parse_source(
        file_path="compiletime/register_a.go",
        source_text=source,
    )
    nodes = {n.qualified_name for n in GraphExtractor().extract(parsed).nodes}
    assert "compiletime._@register_a" in nodes
    assert "compiletime._" not in nodes


def test_graph_extractor_separates_go_external_test_package():
    """`package foo_test` (external test package) is a logically distinct
    Go package from `package foo` that happens to share a directory. They
    compile separately, so a lowercase helper like `func config(...)` in
    `checkout_test` must not share a QN with anything in `checkout`. The
    package-QN suffixes `_test` for the external test package — matching
    Go's own semantics.
    """
    prod_source = """package checkout

type Config struct{}

func (c Config) Validate() error { return nil }
"""
    test_source = """package checkout_test

func config(s string) []byte { return []byte(s) }
"""
    parser = GraphParser()
    extractor = GraphExtractor()

    parsed_prod = parser.parse_source(
        file_path="domain/processing/integrations/checkout/config.go",
        source_text=prod_source,
    )
    parsed_test = parser.parse_source(
        file_path="domain/processing/integrations/checkout/config_test.go",
        source_text=test_source,
    )

    prod_nodes = {n.qualified_name for n in extractor.extract(parsed_prod).nodes}
    test_nodes = {n.qualified_name for n in extractor.extract(parsed_test).nodes}

    prod_pkg = "domain.processing.integrations.checkout"
    test_pkg = "domain.processing.integrations.checkout_test"
    assert f"{prod_pkg}.Config" in prod_nodes
    assert f"{test_pkg}.config" in test_nodes
    # The two packages live in disjoint QN namespaces — no overlap.
    assert prod_nodes.isdisjoint(test_nodes)


def test_graph_extractor_separates_go_module_from_same_package_function_in_sibling_file():
    """Module node from `callback.go` and `func callback` declared in a
    sibling file (`responses.go`) of the same package must end up in
    disjoint QN namespaces. Same-package cross-file module-vs-symbol
    collision was the third Go-collision class hit on svc/walle.
    """
    callback_source = """package monetix

type CallbackOperation struct{}
"""
    responses_source = """package monetix

func callback() string { return "ok" }
"""
    parser = GraphParser()
    extractor = GraphExtractor()

    parsed_callback = parser.parse_source(
        file_path="domain/payments/monetix/callback.go",
        source_text=callback_source,
    )
    parsed_responses = parser.parse_source(
        file_path="domain/payments/monetix/responses.go",
        source_text=responses_source,
    )

    cb_nodes = {n.qualified_name for n in extractor.extract(parsed_callback).nodes}
    rs_nodes = {n.qualified_name for n in extractor.extract(parsed_responses).nodes}

    pkg = "domain.payments.monetix"
    assert f"{pkg}.callback#module" in cb_nodes
    assert f"{pkg}.callback" in rs_nodes
    # The unsuffixed `<pkg>.callback` from the module node was the
    # collision — must not be emitted anymore.
    assert f"{pkg}.callback" not in cb_nodes


def test_graph_extractor_always_suffixes_go_module_qn():
    """Symbolic invariant: every MODULE node from a Go file uses the
    `#module` suffix, regardless of whether the file stem collides with
    a same-package symbol. Removes a whole class of cross-file
    collision bugs by construction.
    """
    source = """package foo

type Bar struct{}
"""
    parsed = GraphParser().parse_source(
        file_path="some/pkg/foo.go",
        source_text=source,
    )
    extracted = GraphExtractor().extract(parsed)
    module_nodes = [n for n in extracted.nodes if n.node_type is GraphNodeType.MODULE]
    assert len(module_nodes) == 1
    assert module_nodes[0].qualified_name.endswith("#module")
