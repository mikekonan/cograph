from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from tree_sitter import Node

from backend.app.graph.languages import GraphLanguage
from backend.app.graph.parser import ParsedFile


class GraphNodeType(StrEnum):
    MODULE = "module"
    CLASS = "class"
    STRUCT = "struct"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    CONSTANT = "constant"
    TYPE_ALIAS = "type_alias"
    ATTRIBUTE = "attribute"


class GraphEdgeType(StrEnum):
    DECLARES = "declares"
    IMPORTS = "imports"
    INHERITS = "inherits"
    CALLS = "calls"


_SIGNATURE_TRUNCATION_LENGTH = 120
_GO_MODULE_QN_SUFFIX = "#module"


@dataclass(slots=True, kw_only=True)
class ExtractedNode:
    node_type: GraphNodeType
    name: str
    qualified_name: str
    file_path: str
    language: GraphLanguage
    start_line: int
    end_line: int
    start_byte: int | None
    end_byte: int | None
    content: str
    signature: str | None = None
    doc_comment: str | None = None
    parent_qualified_name: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    symbol_key: str = ""
    role: str | None = None


@dataclass(slots=True, kw_only=True)
class ExtractedEdge:
    edge_type: GraphEdgeType
    source: str
    target: str


@dataclass(slots=True, kw_only=True)
class ExtractedGraph:
    nodes: list[ExtractedNode]
    edges: list[ExtractedEdge]


def compute_symbol_key(
    *,
    language: GraphLanguage,
    qualified_name: str,
    signature: str | None,
) -> str:
    digest_input = (signature or "").encode("utf-8")
    sig_hash = hashlib.sha256(digest_input).hexdigest()[:8]
    return f"{language.value}:{qualified_name}:{sig_hash}"


class GraphExtractor:
    def extract(
        self,
        parsed_file: ParsedFile,
        *,
        go_module_path: str | None = None,
    ) -> ExtractedGraph:
        if parsed_file.language is GraphLanguage.PYTHON:
            extracted = _extract_python_graph(parsed_file)
        elif parsed_file.language is GraphLanguage.GO:
            extracted = _extract_go_graph(
                parsed_file,
                go_module_path=go_module_path,
            )
        else:
            raise ValueError(f"Unsupported graph language: {parsed_file.language}")

        for node in extracted.nodes:
            if not node.symbol_key:
                node.symbol_key = compute_symbol_key(
                    language=node.language,
                    qualified_name=node.qualified_name,
                    signature=node.signature,
                )
            node.role = _infer_role(node)

        return extracted


def _extract_python_graph(parsed_file: ParsedFile) -> ExtractedGraph:
    module_name = _module_qualified_name(parsed_file)
    _source_byte_len = len(parsed_file.source_bytes)
    nodes: list[ExtractedNode] = [
        ExtractedNode(
            node_type=GraphNodeType.MODULE,
            name=module_name.rsplit(".", 1)[-1],
            qualified_name=module_name,
            file_path=parsed_file.path.as_posix(),
            language=parsed_file.language,
            start_line=1,
            end_line=max(1, parsed_file.root_node.end_point.row + 1),
            start_byte=0 if _source_byte_len > 0 else None,
            end_byte=_source_byte_len if _source_byte_len > 0 else None,
            content=parsed_file.source_text,
            doc_comment=_extract_python_docstring(parsed_file, parsed_file.root_node),
        )
    ]
    edges: list[ExtractedEdge] = []

    for child in parsed_file.root_node.children:
        if child.type == "string":
            continue
        if child.type == "import_statement":
            edges.extend(_extract_import_edges(parsed_file, module_name, child))
            continue
        if child.type == "import_from_statement":
            edges.extend(_extract_from_import_edges(parsed_file, module_name, child))
            continue
        if child.type in ("assignment", "expression_statement"):
            assignment_nodes, assignment_edges = _extract_module_level_assignments(
                parsed_file=parsed_file,
                module_name=module_name,
                statement_node=child,
            )
            nodes.extend(assignment_nodes)
            edges.extend(assignment_edges)
            continue
        if child.type == "type_alias_statement":
            alias_node, alias_edge = _extract_pep695_type_alias(
                parsed_file=parsed_file,
                module_name=module_name,
                type_alias_node=child,
            )
            if alias_node is not None:
                nodes.append(alias_node)
                if alias_edge is not None:
                    edges.append(alias_edge)
            continue

        definition_node, decorators = _unwrap_definition(child)
        if definition_node is None:
            continue

        if definition_node.type == "class_definition":
            class_node, class_edges = _extract_class(
                parsed_file=parsed_file,
                module_name=module_name,
                class_node=definition_node,
                decorators=decorators,
            )
            nodes.extend(class_node)
            edges.extend(class_edges)
            continue

        if definition_node.type == "function_definition":
            function_node, function_edges = _extract_function(
                parsed_file=parsed_file,
                module_name=module_name,
                function_node=definition_node,
                decorators=decorators,
                parent_qualified_name=module_name,
                node_type=GraphNodeType.FUNCTION,
            )
            nodes.append(function_node)
            edges.extend(function_edges)

    return ExtractedGraph(nodes=nodes, edges=edges)


def _extract_class(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    class_node: Node,
    decorators: list[str],
) -> tuple[list[ExtractedNode], list[ExtractedEdge]]:
    class_name = _node_text(parsed_file, class_node.child_by_field_name("name"))
    qualified_name = f"{module_name}.{class_name}"
    bases = _extract_superclasses(parsed_file, class_node)
    metadata: dict[str, object] = {}
    if bases:
        metadata["bases"] = bases
    if decorators:
        metadata["decorators"] = decorators

    nodes = [
        ExtractedNode(
            node_type=GraphNodeType.CLASS,
            name=class_name,
            qualified_name=qualified_name,
            file_path=parsed_file.path.as_posix(),
            language=parsed_file.language,
            start_line=_start_line(class_node),
            end_line=_end_line(class_node),
            start_byte=class_node.start_byte,
            end_byte=class_node.end_byte,
            content=_node_text(parsed_file, class_node),
            doc_comment=_extract_python_docstring(parsed_file, class_node),
            parent_qualified_name=module_name,
            metadata=metadata,
        )
    ]
    edges = [
        ExtractedEdge(
            edge_type=GraphEdgeType.DECLARES,
            source=module_name,
            target=qualified_name,
        )
    ]
    edges.extend(
        ExtractedEdge(
            edge_type=GraphEdgeType.INHERITS,
            source=qualified_name,
            target=base_name,
        )
        for base_name in bases
    )

    body = class_node.child_by_field_name("body")
    if body is None:
        return nodes, edges

    for child in body.children:
        if child.type == "string":
            continue
        if child.type in ("assignment", "expression_statement"):
            attribute_nodes, attribute_edges = _extract_class_attributes(
                parsed_file=parsed_file,
                class_qualified_name=qualified_name,
                statement_node=child,
            )
            nodes.extend(attribute_nodes)
            edges.extend(attribute_edges)
            continue

        definition_node, method_decorators = _unwrap_definition(child)
        if definition_node is None or definition_node.type != "function_definition":
            continue

        method_node, method_edges = _extract_function(
            parsed_file=parsed_file,
            module_name=module_name,
            function_node=definition_node,
            decorators=method_decorators,
            parent_qualified_name=qualified_name,
            node_type=GraphNodeType.METHOD,
        )
        nodes.append(method_node)
        edges.extend(method_edges)

    return nodes, edges


def _extract_function(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    function_node: Node,
    decorators: list[str],
    parent_qualified_name: str,
    node_type: GraphNodeType,
) -> tuple[ExtractedNode, list[ExtractedEdge]]:
    function_name = _node_text(parsed_file, function_node.child_by_field_name("name"))
    qualified_name = f"{parent_qualified_name}.{function_name}"
    metadata: dict[str, object] = {}
    if decorators:
        metadata["decorators"] = decorators
    if _is_async_function(function_node):
        metadata["async"] = True

    node = ExtractedNode(
        node_type=node_type,
        name=function_name,
        qualified_name=qualified_name,
        file_path=parsed_file.path.as_posix(),
        language=parsed_file.language,
        start_line=_start_line(function_node),
        end_line=_end_line(function_node),
        start_byte=function_node.start_byte,
        end_byte=function_node.end_byte,
        content=_node_text(parsed_file, function_node),
        signature=_extract_signature(parsed_file, function_node),
        doc_comment=_extract_python_docstring(parsed_file, function_node),
        parent_qualified_name=parent_qualified_name,
        metadata=metadata,
    )
    edges = [
        ExtractedEdge(
            edge_type=GraphEdgeType.DECLARES,
            source=parent_qualified_name,
            target=qualified_name,
        )
    ]
    edges.extend(_extract_call_edges(parsed_file, function_node, qualified_name))
    return node, edges


def _extract_go_graph(
    parsed_file: ParsedFile,
    *,
    go_module_path: str | None = None,
) -> ExtractedGraph:
    package_name = _go_package_name(parsed_file)
    package_qualified_name = _go_package_qualified_name(parsed_file, package_name=package_name)
    raw_module_name = _module_qualified_name(parsed_file)
    module_name = (
        f"{raw_module_name}{_GO_MODULE_QN_SUFFIX}"
        if _go_module_name_collides(
            parsed_file=parsed_file,
            module_name=raw_module_name,
            package_qualified_name=package_qualified_name,
        )
        else raw_module_name
    )
    source_byte_len = len(parsed_file.source_bytes)
    module_metadata = {
        "package_name": package_name,
        "package_qualified_name": package_qualified_name,
    }
    nodes: list[ExtractedNode] = [
        ExtractedNode(
            node_type=GraphNodeType.MODULE,
            name=parsed_file.path.stem,
            qualified_name=module_name,
            file_path=parsed_file.path.as_posix(),
            language=parsed_file.language,
            start_line=1,
            end_line=max(1, parsed_file.root_node.end_point.row + 1),
            start_byte=0 if source_byte_len > 0 else None,
            end_byte=source_byte_len if source_byte_len > 0 else None,
            content=parsed_file.source_text,
            metadata=module_metadata,
        )
    ]
    edges: list[ExtractedEdge] = []

    for child in parsed_file.root_node.named_children:
        if child.type == "package_clause":
            continue
        if child.type == "import_declaration":
            edges.extend(
                _extract_go_import_edges(
                    parsed_file=parsed_file,
                    module_name=module_name,
                    import_node=child,
                    go_module_path=go_module_path,
                )
            )
            continue
        if child.type == "function_declaration":
            function_node, function_edges = _extract_go_function(
                parsed_file=parsed_file,
                module_name=module_name,
                package_qualified_name=package_qualified_name,
                function_node=child,
            )
            nodes.append(function_node)
            edges.extend(function_edges)
            continue
        if child.type == "method_declaration":
            method_node, method_edges = _extract_go_method(
                parsed_file=parsed_file,
                package_qualified_name=package_qualified_name,
                method_node=child,
            )
            if method_node is not None:
                nodes.append(method_node)
                edges.extend(method_edges)
            continue
        if child.type != "type_declaration":
            continue

        for declaration in child.named_children:
            declaration_nodes, declaration_edges = _extract_go_type_declaration(
                parsed_file=parsed_file,
                module_name=module_name,
                package_qualified_name=package_qualified_name,
                declaration=declaration,
            )
            nodes.extend(declaration_nodes)
            edges.extend(declaration_edges)

    return ExtractedGraph(nodes=nodes, edges=edges)


def _go_module_name_collides(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    package_qualified_name: str,
) -> bool:
    for child in parsed_file.root_node.named_children:
        if child.type == "function_declaration":
            function_name = _node_text(parsed_file, child.child_by_field_name("name")).strip()
            if function_name and f"{package_qualified_name}.{function_name}" == module_name:
                return True
            continue
        if child.type != "type_declaration":
            continue
        for declaration in child.named_children:
            declared_name = _node_text(parsed_file, declaration.child_by_field_name("name")).strip()
            if declared_name and f"{package_qualified_name}.{declared_name}" == module_name:
                return True
    return False


def _extract_go_function(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    package_qualified_name: str,
    function_node: Node,
) -> tuple[ExtractedNode, list[ExtractedEdge]]:
    function_name = _node_text(parsed_file, function_node.child_by_field_name("name")).strip()
    qualified_name = f"{package_qualified_name}.{function_name}"
    node = ExtractedNode(
        node_type=GraphNodeType.FUNCTION,
        name=function_name,
        qualified_name=qualified_name,
        file_path=parsed_file.path.as_posix(),
        language=parsed_file.language,
        start_line=_start_line(function_node),
        end_line=_end_line(function_node),
        start_byte=function_node.start_byte,
        end_byte=function_node.end_byte,
        content=_node_text(parsed_file, function_node),
        signature=_extract_go_signature(parsed_file, function_node),
        parent_qualified_name=module_name,
    )
    edges = [
        ExtractedEdge(
            edge_type=GraphEdgeType.DECLARES,
            source=module_name,
            target=qualified_name,
        )
    ]
    edges.extend(
        _extract_go_call_edges(
            parsed_file=parsed_file,
            function_node=function_node,
            qualified_name=qualified_name,
        )
    )
    return node, edges


def _extract_go_method(
    *,
    parsed_file: ParsedFile,
    package_qualified_name: str,
    method_node: Node,
) -> tuple[ExtractedNode | None, list[ExtractedEdge]]:
    receiver_name, receiver_type = _go_receiver_details(parsed_file, method_node)
    method_name = _node_text(parsed_file, method_node.child_by_field_name("name")).strip()
    if not receiver_type or not method_name:
        return None, []

    parent_qualified_name = f"{package_qualified_name}.{receiver_type}"
    qualified_name = f"{parent_qualified_name}.{method_name}"
    metadata: dict[str, object] = {}
    if receiver_name:
        metadata["receiver_name"] = receiver_name
    metadata["receiver_type"] = receiver_type

    node = ExtractedNode(
        node_type=GraphNodeType.METHOD,
        name=method_name,
        qualified_name=qualified_name,
        file_path=parsed_file.path.as_posix(),
        language=parsed_file.language,
        start_line=_start_line(method_node),
        end_line=_end_line(method_node),
        start_byte=method_node.start_byte,
        end_byte=method_node.end_byte,
        content=_node_text(parsed_file, method_node),
        signature=_extract_go_signature(parsed_file, method_node),
        parent_qualified_name=parent_qualified_name,
        metadata=metadata,
    )
    edges = [
        ExtractedEdge(
            edge_type=GraphEdgeType.DECLARES,
            source=parent_qualified_name,
            target=qualified_name,
        )
    ]
    edges.extend(
        _extract_go_call_edges(
            parsed_file=parsed_file,
            function_node=method_node,
            qualified_name=qualified_name,
        )
    )
    return node, edges


def _extract_go_type_declaration(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    package_qualified_name: str,
    declaration: Node,
) -> tuple[list[ExtractedNode], list[ExtractedEdge]]:
    if declaration.type == "type_alias":
        alias_node, alias_edge = _extract_go_type_alias(
            parsed_file=parsed_file,
            module_name=module_name,
            package_qualified_name=package_qualified_name,
            alias_node=declaration,
        )
        if alias_node is None or alias_edge is None:
            return [], []
        return [alias_node], [alias_edge]

    if declaration.type != "type_spec":
        return [], []

    name = _node_text(parsed_file, declaration.child_by_field_name("name")).strip()
    if not name:
        return [], []

    declared_type = _go_declared_type_node(declaration)
    if declared_type is None:
        return [], []

    if declared_type.type == "struct_type":
        return _extract_go_struct(
            parsed_file=parsed_file,
            module_name=module_name,
            package_qualified_name=package_qualified_name,
            type_name=name,
            type_node=declaration,
            struct_node=declared_type,
        )

    if declared_type.type == "interface_type":
        return _extract_go_interface(
            parsed_file=parsed_file,
            module_name=module_name,
            package_qualified_name=package_qualified_name,
            type_name=name,
            type_node=declaration,
            interface_node=declared_type,
        )

    named_node, named_edge = _extract_go_named_type(
        parsed_file=parsed_file,
        module_name=module_name,
        package_qualified_name=package_qualified_name,
        type_name=name,
        type_node=declaration,
    )
    if named_node is None or named_edge is None:
        return [], []
    return [named_node], [named_edge]


def _extract_go_struct(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    package_qualified_name: str,
    type_name: str,
    type_node: Node,
    struct_node: Node,
) -> tuple[list[ExtractedNode], list[ExtractedEdge]]:
    qualified_name = f"{package_qualified_name}.{type_name}"
    embedded_types = _extract_go_embedded_types(
        parsed_file=parsed_file,
        declarations_node=struct_node.named_children[0] if struct_node.named_children else struct_node,
        package_qualified_name=package_qualified_name,
    )
    metadata: dict[str, object] = {}
    if embedded_types:
        metadata["embeds"] = embedded_types

    nodes = [
        ExtractedNode(
            node_type=GraphNodeType.STRUCT,
            name=type_name,
            qualified_name=qualified_name,
            file_path=parsed_file.path.as_posix(),
            language=parsed_file.language,
            start_line=_start_line(type_node),
            end_line=_end_line(type_node),
            start_byte=type_node.start_byte,
            end_byte=type_node.end_byte,
            content=_node_text(parsed_file, type_node),
            signature=f"type {type_name} struct",
            parent_qualified_name=module_name,
            metadata=metadata,
        )
    ]
    edges = [
        ExtractedEdge(
            edge_type=GraphEdgeType.DECLARES,
            source=module_name,
            target=qualified_name,
        )
    ]
    edges.extend(
        ExtractedEdge(
            edge_type=GraphEdgeType.INHERITS,
            source=qualified_name,
            target=embedded_type,
        )
        for embedded_type in embedded_types
    )
    return nodes, edges


def _extract_go_interface(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    package_qualified_name: str,
    type_name: str,
    type_node: Node,
    interface_node: Node,
) -> tuple[list[ExtractedNode], list[ExtractedEdge]]:
    qualified_name = f"{package_qualified_name}.{type_name}"
    nodes = [
        ExtractedNode(
            node_type=GraphNodeType.INTERFACE,
            name=type_name,
            qualified_name=qualified_name,
            file_path=parsed_file.path.as_posix(),
            language=parsed_file.language,
            start_line=_start_line(type_node),
            end_line=_end_line(type_node),
            start_byte=type_node.start_byte,
            end_byte=type_node.end_byte,
            content=_node_text(parsed_file, type_node),
            signature=f"type {type_name} interface",
            parent_qualified_name=module_name,
        )
    ]
    edges = [
        ExtractedEdge(
            edge_type=GraphEdgeType.DECLARES,
            source=module_name,
            target=qualified_name,
        )
    ]

    for child in interface_node.named_children:
        if child.type == "method_elem":
            method_name = _node_text(parsed_file, child.child_by_field_name("name")).strip()
            if not method_name:
                continue
            method_qualified_name = f"{qualified_name}.{method_name}"
            nodes.append(
                ExtractedNode(
                    node_type=GraphNodeType.METHOD,
                    name=method_name,
                    qualified_name=method_qualified_name,
                    file_path=parsed_file.path.as_posix(),
                    language=parsed_file.language,
                    start_line=_start_line(child),
                    end_line=_end_line(child),
                    start_byte=child.start_byte,
                    end_byte=child.end_byte,
                    content=_node_text(parsed_file, child),
                    signature=_truncate(_node_text(parsed_file, child).replace("\n", " ").strip()),
                    parent_qualified_name=qualified_name,
                )
            )
            edges.append(
                ExtractedEdge(
                    edge_type=GraphEdgeType.DECLARES,
                    source=qualified_name,
                    target=method_qualified_name,
                )
            )
            continue

        if child.type == "type_elem":
            embedded_type = _go_embedded_type_target(
                parsed_file=parsed_file,
                node=child.named_children[0] if child.named_children else None,
                package_qualified_name=package_qualified_name,
            )
            if embedded_type:
                edges.append(
                    ExtractedEdge(
                        edge_type=GraphEdgeType.INHERITS,
                        source=qualified_name,
                        target=embedded_type,
                    )
                )

    return nodes, edges


def _extract_go_named_type(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    package_qualified_name: str,
    type_name: str,
    type_node: Node,
) -> tuple[ExtractedNode | None, ExtractedEdge | None]:
    qualified_name = f"{package_qualified_name}.{type_name}"
    node = ExtractedNode(
        node_type=GraphNodeType.TYPE_ALIAS,
        name=type_name,
        qualified_name=qualified_name,
        file_path=parsed_file.path.as_posix(),
        language=parsed_file.language,
        start_line=_start_line(type_node),
        end_line=_end_line(type_node),
        start_byte=type_node.start_byte,
        end_byte=type_node.end_byte,
        content=_node_text(parsed_file, type_node),
        signature=_truncate(_node_text(parsed_file, type_node).replace("\n", " ").strip()),
        parent_qualified_name=module_name,
    )
    edge = ExtractedEdge(
        edge_type=GraphEdgeType.DECLARES,
        source=module_name,
        target=qualified_name,
    )
    return node, edge


def _extract_go_type_alias(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    package_qualified_name: str,
    alias_node: Node,
) -> tuple[ExtractedNode | None, ExtractedEdge | None]:
    name = _node_text(parsed_file, alias_node.child_by_field_name("name")).strip()
    if not name:
        return None, None

    qualified_name = f"{package_qualified_name}.{name}"
    node = ExtractedNode(
        node_type=GraphNodeType.TYPE_ALIAS,
        name=name,
        qualified_name=qualified_name,
        file_path=parsed_file.path.as_posix(),
        language=parsed_file.language,
        start_line=_start_line(alias_node),
        end_line=_end_line(alias_node),
        start_byte=alias_node.start_byte,
        end_byte=alias_node.end_byte,
        content=_node_text(parsed_file, alias_node),
        signature=_truncate(_node_text(parsed_file, alias_node).replace("\n", " ").strip()),
        parent_qualified_name=module_name,
    )
    edge = ExtractedEdge(
        edge_type=GraphEdgeType.DECLARES,
        source=module_name,
        target=qualified_name,
    )
    return node, edge


def _extract_go_import_edges(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    import_node: Node,
    go_module_path: str | None,
) -> list[ExtractedEdge]:
    targets: list[str] = []
    import_specs: list[Node] = []
    for child in import_node.named_children:
        if child.type == "import_spec":
            import_specs.append(child)
            continue
        if child.type == "import_spec_list":
            import_specs.extend(
                grandchild for grandchild in child.named_children if grandchild.type == "import_spec"
            )

    for spec in import_specs:
        target = _go_import_target_text(
            parsed_file=parsed_file,
            import_node=spec,
            go_module_path=go_module_path,
        )
        if target:
            targets.append(target)

    return [
        ExtractedEdge(
            edge_type=GraphEdgeType.IMPORTS,
            source=module_name,
            target=target,
        )
        for target in targets
    ]


def _extract_go_call_edges(
    *,
    parsed_file: ParsedFile,
    function_node: Node,
    qualified_name: str,
) -> list[ExtractedEdge]:
    body = function_node.child_by_field_name("body")
    if body is None:
        return []

    edges: list[ExtractedEdge] = []
    stack = list(reversed(body.children))
    while stack:
        node = stack.pop()
        if node.type == "function_literal":
            continue
        if node.type == "call_expression":
            function_expr = node.child_by_field_name("function")
            if function_expr is not None:
                callee = _symbol_text(parsed_file, function_expr)
                if callee:
                    edges.append(
                        ExtractedEdge(
                            edge_type=GraphEdgeType.CALLS,
                            source=qualified_name,
                            target=callee,
                        )
                    )
        stack.extend(reversed(node.children))

    return edges


def _extract_module_level_assignments(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    statement_node: Node,
) -> tuple[list[ExtractedNode], list[ExtractedEdge]]:
    assignment: Node | None
    if statement_node.type == "assignment":
        assignment = statement_node
    else:
        assignment = _first_child_of_type(statement_node, "assignment")
    if assignment is None:
        return [], []

    target = _simple_assignment_target(parsed_file, assignment)
    if target is None:
        return [], []

    name, name_node = target
    qualified_name = f"{module_name}.{name}"
    signature = _truncate(_node_text(parsed_file, assignment).replace("\n", " ").strip())
    metadata: dict[str, object] = {}

    node_type = _classify_module_assignment(parsed_file, assignment, name)

    node = ExtractedNode(
        node_type=node_type,
        name=name,
        qualified_name=qualified_name,
        file_path=parsed_file.path.as_posix(),
        language=parsed_file.language,
        start_line=_start_line(statement_node),
        end_line=_end_line(statement_node),
        start_byte=statement_node.start_byte,
        end_byte=statement_node.end_byte,
        content=_node_text(parsed_file, statement_node),
        signature=signature,
        parent_qualified_name=module_name,
        metadata=metadata,
    )
    edges = [
        ExtractedEdge(
            edge_type=GraphEdgeType.DECLARES,
            source=module_name,
            target=qualified_name,
        )
    ]
    del name_node  # reserved for future per-target resolution
    return [node], edges


def _extract_class_attributes(
    *,
    parsed_file: ParsedFile,
    class_qualified_name: str,
    statement_node: Node,
) -> tuple[list[ExtractedNode], list[ExtractedEdge]]:
    assignment: Node | None
    if statement_node.type == "assignment":
        assignment = statement_node
    else:
        assignment = _first_child_of_type(statement_node, "assignment")
    if assignment is None:
        return [], []

    target = _simple_assignment_target(parsed_file, assignment)
    if target is None:
        return [], []

    name, _ = target
    qualified_name = f"{class_qualified_name}.{name}"
    signature = _truncate(_node_text(parsed_file, assignment).replace("\n", " ").strip())

    node = ExtractedNode(
        node_type=GraphNodeType.ATTRIBUTE,
        name=name,
        qualified_name=qualified_name,
        file_path=parsed_file.path.as_posix(),
        language=parsed_file.language,
        start_line=_start_line(statement_node),
        end_line=_end_line(statement_node),
        start_byte=statement_node.start_byte,
        end_byte=statement_node.end_byte,
        content=_node_text(parsed_file, statement_node),
        signature=signature,
        parent_qualified_name=class_qualified_name,
    )
    edges = [
        ExtractedEdge(
            edge_type=GraphEdgeType.DECLARES,
            source=class_qualified_name,
            target=qualified_name,
        )
    ]
    return [node], edges


def _extract_pep695_type_alias(
    *,
    parsed_file: ParsedFile,
    module_name: str,
    type_alias_node: Node,
) -> tuple[ExtractedNode | None, ExtractedEdge | None]:
    name_node = type_alias_node.child_by_field_name("name")
    if name_node is None:
        for child in type_alias_node.named_children:
            if child.type == "type":
                name_node = child
                break
    if name_node is None:
        return None, None

    # For `type Box[T] = list[T]`, the LHS subtree embeds type_parameters;
    # we need the bare identifier (`Box`), not `Box[T]`, so consumers' plain
    # `from types import Box` resolves.
    identifier_node = _first_identifier(name_node) or name_node
    name = _node_text(parsed_file, identifier_node).strip()
    if not name:
        return None, None

    qualified_name = f"{module_name}.{name}"
    signature = _truncate(_node_text(parsed_file, type_alias_node).replace("\n", " ").strip())

    node = ExtractedNode(
        node_type=GraphNodeType.TYPE_ALIAS,
        name=name,
        qualified_name=qualified_name,
        file_path=parsed_file.path.as_posix(),
        language=parsed_file.language,
        start_line=_start_line(type_alias_node),
        end_line=_end_line(type_alias_node),
        start_byte=type_alias_node.start_byte,
        end_byte=type_alias_node.end_byte,
        content=_node_text(parsed_file, type_alias_node),
        signature=signature,
        parent_qualified_name=module_name,
    )
    edge = ExtractedEdge(
        edge_type=GraphEdgeType.DECLARES,
        source=module_name,
        target=qualified_name,
    )
    return node, edge


def _classify_module_assignment(
    parsed_file: ParsedFile,
    assignment_node: Node,
    name: str,
) -> GraphNodeType:
    right = assignment_node.child_by_field_name("right")
    if right is not None and right.type == "call":
        function_expr = right.child_by_field_name("function")
        if function_expr is not None:
            called = _symbol_text(parsed_file, function_expr)
            if called in {"TypeVar", "NewType", "TypeAlias", "ParamSpec", "TypeVarTuple"}:
                return GraphNodeType.TYPE_ALIAS
    if name.isupper() and name[:1].isalpha():
        return GraphNodeType.CONSTANT
    return GraphNodeType.VARIABLE


def _simple_assignment_target(
    parsed_file: ParsedFile,
    assignment_node: Node,
) -> tuple[str, Node] | None:
    left = assignment_node.child_by_field_name("left")
    if left is None:
        return None
    if left.type != "identifier":
        return None
    text = _node_text(parsed_file, left).strip()
    if not text or not text.isidentifier():
        return None
    return text, left


def _first_identifier(node: Node) -> Node | None:
    if node.type == "identifier":
        return node
    for child in node.named_children:
        found = _first_identifier(child)
        if found is not None:
            return found
    return None


def _first_child_of_type(node: Node, node_type: str) -> Node | None:
    for child in node.children:
        if child.type == node_type:
            return child
    return None


def _truncate(text: str, limit: int = _SIGNATURE_TRUNCATION_LENGTH) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _go_package_name(parsed_file: ParsedFile) -> str:
    for child in parsed_file.root_node.named_children:
        if child.type != "package_clause":
            continue
        identifier = child.named_children[0] if child.named_children else None
        if identifier is not None:
            return _node_text(parsed_file, identifier).strip()
    return parsed_file.path.stem


def _go_package_qualified_name(
    parsed_file: ParsedFile,
    *,
    package_name: str,
) -> str:
    parts = [part for part in parsed_file.path.parent.parts if part]
    if parts:
        return ".".join(parts)
    return package_name or parsed_file.path.stem


def _go_declared_type_node(type_spec_node: Node) -> Node | None:
    name_node = type_spec_node.child_by_field_name("name")
    for child in type_spec_node.named_children:
        if (
            name_node is not None
            and child.start_byte == name_node.start_byte
            and child.end_byte == name_node.end_byte
        ):
            continue
        return child
    return None


def _go_receiver_details(parsed_file: ParsedFile, method_node: Node) -> tuple[str | None, str | None]:
    receiver = method_node.child_by_field_name("receiver")
    if receiver is None:
        return None, None

    declaration = next(
        (child for child in receiver.named_children if child.type == "parameter_declaration"),
        None,
    )
    if declaration is None or not declaration.named_children:
        return None, None

    receiver_name: str | None = None
    receiver_type_node: Node | None = None
    for child in declaration.named_children:
        if child.type == "identifier" and receiver_name is None:
            receiver_name = _node_text(parsed_file, child).strip()
            continue
        receiver_type_node = child

    return receiver_name, _go_type_name(parsed_file, receiver_type_node)


def _go_type_name(parsed_file: ParsedFile, node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type in {"type_identifier", "identifier", "field_identifier"}:
        return _node_text(parsed_file, node).strip()
    if node.type == "pointer_type":
        inner = node.named_children[0] if node.named_children else None
        return _go_type_name(parsed_file, inner)
    if node.type == "generic_type":
        inner = node.named_children[0] if node.named_children else None
        return _go_type_name(parsed_file, inner)
    return _symbol_text(parsed_file, node).strip() or _node_text(parsed_file, node).strip() or None


def _extract_go_embedded_types(
    *,
    parsed_file: ParsedFile,
    declarations_node: Node,
    package_qualified_name: str,
) -> list[str]:
    embedded_types: list[str] = []
    for child in declarations_node.named_children:
        if child.type != "field_declaration":
            continue
        if any(grandchild.type in {"field_identifier", "identifier"} for grandchild in child.named_children[:-1]):
            continue
        target_node = child.named_children[-1] if child.named_children else None
        target = _go_embedded_type_target(
            parsed_file=parsed_file,
            node=target_node,
            package_qualified_name=package_qualified_name,
        )
        if target and target not in embedded_types:
            embedded_types.append(target)
    return embedded_types


def _go_embedded_type_target(
    *,
    parsed_file: ParsedFile,
    node: Node | None,
    package_qualified_name: str,
) -> str:
    if node is None:
        return ""
    if node.type == "pointer_type":
        inner = node.named_children[0] if node.named_children else None
        return _go_embedded_type_target(
            parsed_file=parsed_file,
            node=inner,
            package_qualified_name=package_qualified_name,
        )
    if node.type in {"type_identifier", "identifier"}:
        name = _node_text(parsed_file, node).strip()
        return f"{package_qualified_name}.{name}" if name else ""
    return _symbol_text(parsed_file, node).strip()


def _go_import_target_text(
    *,
    parsed_file: ParsedFile,
    import_node: Node,
    go_module_path: str | None,
) -> str:
    if not import_node.named_children:
        return ""

    alias_node = import_node.named_children[0] if len(import_node.named_children) > 1 else None
    path_node = import_node.named_children[-1]
    import_path = _go_import_path_text(parsed_file, path_node)
    normalized_import = _normalize_go_import_path(import_path, go_module_path=go_module_path)
    if not normalized_import:
        return ""
    if alias_node is None:
        return normalized_import

    alias = _node_text(parsed_file, alias_node).strip()
    if not alias:
        return normalized_import
    default_alias = normalized_import.rsplit(".", 1)[-1]
    if alias == default_alias:
        return normalized_import
    return f"{normalized_import} as {alias}"


def _go_import_path_text(parsed_file: ParsedFile, node: Node) -> str:
    if node.type == "interpreted_string_literal" and node.named_children:
        return _node_text(parsed_file, node.named_children[0]).strip()
    return _node_text(parsed_file, node).strip().strip("\"`")


def _normalize_go_import_path(import_path: str, *, go_module_path: str | None) -> str:
    normalized = import_path.strip().strip("/")
    if not normalized:
        return ""

    if go_module_path:
        stripped_module = go_module_path.strip().strip("/")
        if normalized == stripped_module:
            normalized = stripped_module.rsplit("/", 1)[-1]
        else:
            prefix = f"{stripped_module}/"
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]

    return normalized.replace("/", ".")


def _infer_role(node: ExtractedNode) -> str | None:
    metadata = node.metadata
    decorators = metadata.get("decorators") if isinstance(metadata, dict) else None
    if isinstance(decorators, list):
        for decorator in decorators:
            if not isinstance(decorator, str):
                continue
            lowered = decorator.lstrip("@").lower()
            if lowered.startswith(("app.", "router.", "blueprint.", "api.", "web.")):
                return "entry_point"
            if lowered.startswith(("pytest.", "unittest.")):
                return "test"
            if lowered in {"app.route", "router.post", "router.get", "router.put", "router.delete"}:
                return "entry_point"

    name = node.name
    if node.node_type is GraphNodeType.TYPE_ALIAS:
        return "type_alias"
    if node.node_type is GraphNodeType.ATTRIBUTE:
        return "attribute"
    if node.node_type is GraphNodeType.CONSTANT:
        return "constant"
    if node.node_type is GraphNodeType.VARIABLE:
        if name in {"logger", "log", "tracer"}:
            return "helper"
        if name == "settings" or name.endswith("_config") or name == "config":
            return "config"
        if name.isupper():
            return "constant"
        return "other"

    if node.node_type in (GraphNodeType.CLASS, GraphNodeType.STRUCT, GraphNodeType.INTERFACE):
        if name.endswith("Service"):
            return "service"
        if name.endswith(("Repository", "Dao", "Repo")):
            return "repository"
        if name.endswith(("Model", "Entity", "Schema")):
            return "model"
        bases = metadata.get("bases") if isinstance(metadata, dict) else None
        if isinstance(bases, list) and any(
            isinstance(base, str) and ("BaseModel" in base or "Schema" in base)
            for base in bases
        ):
            return "model"
        if name.startswith("Test") or name.endswith("Test"):
            return "test"
        return "other"

    if node.node_type in (GraphNodeType.FUNCTION, GraphNodeType.METHOD):
        if name.startswith(("test_", "Test")) or name == "test":
            return "test"
        if name.startswith("_"):
            return "helper"
        return "other"

    if node.node_type is GraphNodeType.MODULE:
        if name in {"config", "settings"}:
            return "config"
        if name.startswith("test_") or name == "tests":
            return "test"
        return "other"

    return "other"


def _extract_import_edges(
    parsed_file: ParsedFile,
    module_name: str,
    import_node: Node,
) -> list[ExtractedEdge]:
    targets: list[str] = []
    for index, child in enumerate(import_node.children):
        if import_node.field_name_for_child(index) != "name":
            continue
        targets.append(_import_target_text(parsed_file, child))

    return [
        ExtractedEdge(
            edge_type=GraphEdgeType.IMPORTS,
            source=module_name,
            target=target,
        )
        for target in targets
        if target
    ]


def _extract_from_import_edges(
    parsed_file: ParsedFile,
    module_name: str,
    import_node: Node,
) -> list[ExtractedEdge]:
    module_part_node = import_node.child_by_field_name("module_name")
    module_part = _node_text(parsed_file, module_part_node) if module_part_node else ""
    targets: list[str] = []
    for index, child in enumerate(import_node.children):
        if import_node.field_name_for_child(index) != "name":
            continue

        imported_name = _import_target_text(parsed_file, child)
        if imported_name == "*":
            targets.append(f"{module_part}.*" if module_part else "*")
            continue
        targets.append(f"{module_part}.{imported_name}" if module_part else imported_name)

    return [
        ExtractedEdge(
            edge_type=GraphEdgeType.IMPORTS,
            source=module_name,
            target=target,
        )
        for target in targets
        if target
    ]


def _extract_superclasses(parsed_file: ParsedFile, class_node: Node) -> list[str]:
    superclasses = class_node.child_by_field_name("superclasses")
    if superclasses is None:
        return []

    return [
        _symbol_text(parsed_file, child)
        for child in superclasses.named_children
        if _symbol_text(parsed_file, child)
    ]


def _extract_call_edges(
    parsed_file: ParsedFile,
    function_node: Node,
    qualified_name: str,
) -> list[ExtractedEdge]:
    body = function_node.child_by_field_name("body")
    if body is None:
        return []

    edges: list[ExtractedEdge] = []
    stack = list(reversed(body.children))
    while stack:
        node = stack.pop()
        if node.type in {"function_definition", "class_definition", "decorated_definition"}:
            continue
        if node.type == "call":
            function_expr = node.child_by_field_name("function")
            if function_expr is not None:
                callee = _symbol_text(parsed_file, function_expr)
                if callee:
                    edges.append(
                        ExtractedEdge(
                            edge_type=GraphEdgeType.CALLS,
                            source=qualified_name,
                            target=callee,
                        )
                    )
        stack.extend(reversed(node.children))

    return edges


def _extract_signature(parsed_file: ParsedFile, node: Node) -> str | None:
    body = node.child_by_field_name("body")
    if body is None:
        return None

    signature = parsed_file.source_bytes[node.start_byte:body.start_byte].decode("utf-8").rstrip()
    return signature.removesuffix(":")


def _extract_go_signature(parsed_file: ParsedFile, node: Node) -> str | None:
    body = node.child_by_field_name("body")
    if body is None:
        return _truncate(_node_text(parsed_file, node).replace("\n", " ").strip())

    return parsed_file.source_bytes[node.start_byte:body.start_byte].decode("utf-8").rstrip()


def _extract_python_docstring(parsed_file: ParsedFile, node: Node) -> str | None:
    body = node.child_by_field_name("body")
    target = body if body is not None else node
    if not target.children:
        return None

    first_child = target.children[0]
    if first_child.type != "string":
        return None

    raw_docstring = _node_text(parsed_file, first_child)
    try:
        parsed_docstring = ast.literal_eval(raw_docstring)
    except (SyntaxError, ValueError):
        return raw_docstring

    return parsed_docstring if isinstance(parsed_docstring, str) else raw_docstring


def _unwrap_definition(node: Node) -> tuple[Node | None, list[str]]:
    if node.type == "decorated_definition":
        decorators = [
            (node_text.text or b"").decode("utf-8")
            for node_text in node.children
            if node_text.type == "decorator"
        ]
        definition = node.child_by_field_name("definition")
        return definition, decorators
    if node.type in {"class_definition", "function_definition"}:
        return node, []
    return None, []


def _import_target_text(parsed_file: ParsedFile, node: Node) -> str:
    if node.type == "aliased_import":
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ""
        canonical = _node_text(parsed_file, name_node)
        alias_node = node.child_by_field_name("alias")
        if alias_node is None:
            return canonical
        alias = _node_text(parsed_file, alias_node).strip()
        if not alias or alias == canonical.rsplit(".", 1)[-1]:
            return canonical
        # Encode alias alongside canonical path so the builder can resolve
        # call-sites that use the local (aliased) name. Format is kept
        # pipe-free / delimiter-free so `rsplit('.', 1)` in older code paths
        # still yields a reasonable fallback local name.
        return f"{canonical} as {alias}"
    if node.type == "wildcard_import":
        return "*"
    return _node_text(parsed_file, node)


def _symbol_text(parsed_file: ParsedFile, node: Node) -> str:
    if node.type == "identifier":
        return _node_text(parsed_file, node)
    if node.type in {"attribute", "selector_expression", "qualified_type"}:
        parts = [_symbol_text(parsed_file, child) for child in node.named_children]
        compact = [part for part in parts if part]
        if compact:
            return ".".join(compact)
    return _node_text(parsed_file, node)


def _is_async_function(node: Node) -> bool:
    return any(child.type == "async" for child in node.children)


def _module_qualified_name(parsed_file: ParsedFile) -> str:
    parts = list(parsed_file.path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else parsed_file.path.stem


def _node_text(parsed_file: ParsedFile, node: Node | None) -> str:
    if node is None:
        return ""
    return parsed_file.source_bytes[node.start_byte:node.end_byte].decode("utf-8")


def _start_line(node: Node) -> int:
    return node.start_point.row + 1


def _end_line(node: Node) -> int:
    return node.end_point.row + 1
