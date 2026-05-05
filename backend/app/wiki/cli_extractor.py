"""Stage 0 helper: extract CLI command surface from Go sources.

Scans Go files for the three CLI frameworks the ecosystem actually uses:

- `spf13/cobra` — `&cobra.Command{Use: "...", ...}` literals + nested
  `parent.AddCommand(child)` registration.
- `urfave/cli` — `cli.Command{Name: "...", ...}` literals + nested
  `Subcommands` slice.
- Standard library `flag` — `flag.String/Int/Bool(...)` calls. The
  binary itself is treated as the "command" (a single root with the
  `cmd/<bin>` directory name).

The output `list[CliCommand]` is consumed by `repo_signals` and used to
create / promote `TopicCandidate`s for actual user-facing commands.
This is critical for the salience problem: without CLI-AST evidence,
a cluster like `internal/validator/` (regression scaffolding) can
out-rank the real `validate` subcommand purely on file count.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from backend.app.wiki.schemas import CliCommand

_log = logging.getLogger(__name__)


_COBRA_TYPE_NAMES: Final = frozenset({"cobra.Command"})
_URFAVE_TYPE_NAMES: Final = frozenset({"cli.Command", "cli.App"})
_GO_FLAG_PACKAGE: Final = "flag"
_GO_FLAG_FUNCTIONS: Final = frozenset(
    {
        "String",
        "StringVar",
        "Int",
        "IntVar",
        "Int64",
        "Int64Var",
        "Bool",
        "BoolVar",
        "Float64",
        "Float64Var",
        "Duration",
        "DurationVar",
        "Var",
    }
)
_COBRA_FLAG_METHODS: Final = frozenset(
    {
        "String",
        "StringP",
        "StringVar",
        "StringVarP",
        "Int",
        "IntP",
        "IntVar",
        "IntVarP",
        "Bool",
        "BoolP",
        "BoolVar",
        "BoolVarP",
        "Float64",
        "Float64P",
        "Float64Var",
        "Float64VarP",
        "Duration",
        "DurationP",
        "DurationVar",
        "DurationVarP",
        "StringSlice",
        "StringSliceP",
        "StringSliceVar",
        "StringSliceVarP",
        "Var",
        "VarP",
    }
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_cli_commands(
    go_sources: list[tuple[str, str]],
) -> list[CliCommand]:
    """Extract CLI commands from a list of `(file_path, source_text)`.

    Pure function. The caller is responsible for filtering down to Go
    files and reading their text.
    """
    if not go_sources:
        return []

    parser = get_parser("go")  # type: ignore[arg-type]

    file_results: list[_FileScan] = []
    for path, source in go_sources:
        if not source:
            continue
        tree = parser.parse(source.encode("utf-8"))
        scan = _scan_file(tree.root_node, source.encode("utf-8"), path)
        file_results.append(scan)

    return _merge_scans(file_results)


# ---------------------------------------------------------------------------
# Internal scan types
# ---------------------------------------------------------------------------


@dataclass
class _CommandLiteral:
    """A Go composite literal that looks like a CLI command spec."""

    framework: str  # "cobra" | "urfave"
    name: str  # value of `Use:` (cobra) or `Name:` (urfave)
    flags: list[str] = field(default_factory=list)
    source_path: str = ""
    start_line: int | None = None
    end_line: int | None = None
    # Variable name the literal is assigned to, for AddCommand resolution.
    var_name: str = ""


@dataclass
class _AddCommandEdge:
    parent_var: str
    child_var: str


@dataclass
class _GoFlagSite:
    """A `flag.String(...)` call detected at package scope."""

    flag_name: str
    source_path: str
    start_line: int | None = None
    end_line: int | None = None


@dataclass
class _FileScan:
    path: str
    literals: list[_CommandLiteral]
    add_command_edges: list[_AddCommandEdge]
    flag_sites: list[_GoFlagSite]


# ---------------------------------------------------------------------------
# Per-file scanning
# ---------------------------------------------------------------------------


def _scan_file(root: Node, source: bytes, path: str) -> _FileScan:
    literals: list[_CommandLiteral] = []
    edges: list[_AddCommandEdge] = []
    flag_sites: list[_GoFlagSite] = []

    # Map from variable name -> literal so flag-attaching calls can find
    # their command (`cmd.Flags().StringVar(...)` style).
    by_var: dict[str, _CommandLiteral] = {}

    # First pass: find all command literals + record their var names.
    for node in _walk(root):
        if node.type == "composite_literal":
            literal = _try_command_literal(node, source, path)
            if literal is None:
                continue
            literals.append(literal)
            if literal.var_name:
                by_var[literal.var_name] = literal

    # Second pass: AddCommand edges + flag-method calls + flag-pkg calls.
    for node in _walk(root):
        if node.type != "call_expression":
            continue
        edge = _try_add_command(node, source)
        if edge is not None:
            edges.append(edge)
            continue
        flag_attach = _try_cobra_flag_call(node, source)
        if flag_attach is not None:
            var, flag_name = flag_attach
            target = by_var.get(var)
            if target is not None and flag_name:
                target.flags.append(flag_name)
            continue
        flag_site = _try_go_flag_call(node, source, path)
        if flag_site is not None:
            flag_sites.append(flag_site)

    return _FileScan(
        path=path,
        literals=literals,
        add_command_edges=edges,
        flag_sites=flag_sites,
    )


def _try_command_literal(
    node: Node, source: bytes, path: str
) -> _CommandLiteral | None:
    """Return a `_CommandLiteral` if this node looks like a Cobra or
    urfave Command struct literal."""
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return None
    type_text = _text(type_node, source).strip()
    framework: str | None = None
    name_field: str | None = None
    if type_text in _COBRA_TYPE_NAMES:
        framework = "cobra"
        name_field = "Use"
    elif type_text in _URFAVE_TYPE_NAMES:
        framework = "urfave"
        name_field = "Name"
    if framework is None or name_field is None:
        return None

    body = node.child_by_field_name("body")
    if body is None:
        return None
    name = _read_keyed_string(body, name_field, source)
    if not name:
        return None

    var_name = _enclosing_var_name(node, source)

    return _CommandLiteral(
        framework=framework,
        name=name,
        source_path=path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        var_name=var_name,
    )


def _read_keyed_string(body: Node, key: str, source: bytes) -> str | None:
    """Look for `key: "string"` inside a `literal_value`.

    In tree-sitter Go's grammar both the key and value sides of a
    `keyed_element` are wrapped in `literal_element` nodes. The key
    side resolves to an `identifier` (e.g. `Use`) and the value side
    holds an `interpreted_string_literal`. We peel the wrappers
    deterministically rather than relying on positional indexing.
    """
    for child in body.named_children:
        if child.type != "keyed_element":
            continue
        k_node = child.child_by_field_name("key")
        v_node = child.child_by_field_name("value")
        if k_node is None or v_node is None:
            continue
        key_text = _text(_unwrap_literal_element(k_node), source).strip()
        if key_text != key:
            continue
        v_unwrapped = _unwrap_literal_element(v_node)
        if v_unwrapped.type == "interpreted_string_literal":
            inner = v_unwrapped.named_children
            if inner and inner[0].type == "interpreted_string_literal_content":
                return _text(inner[0], source)
            return _strip_quotes(_text(v_unwrapped, source))
    return None


def _unwrap_literal_element(node: Node) -> Node:
    """Peel `literal_element` wrappers used inside Go composite
    literals so callers can pattern-match on the inner node type."""
    current = node
    while current.type == "literal_element" and current.named_child_count == 1:
        current = current.named_children[0]
    return current


def _enclosing_var_name(node: Node, source: bytes) -> str:
    """Walk up to find the variable this composite literal is assigned
    to, e.g. `var validateCmd = &cobra.Command{...}` → `validateCmd`."""
    current: Node | None = node.parent
    while current is not None:
        if current.type in {"var_spec", "const_spec", "short_var_declaration"}:
            kids = current.named_children
            if kids:
                first = kids[0]
                if first.type in {"identifier", "expression_list"}:
                    return _text(first, source).split(",")[0].strip()
        if current.type == "assignment_statement":
            left = current.child_by_field_name("left")
            if left is not None:
                return _text(left, source).split(",")[0].strip()
        current = current.parent
    return ""


def _try_add_command(node: Node, source: bytes) -> _AddCommandEdge | None:
    """Detect `parent.AddCommand(child1, child2, ...)`."""
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "selector_expression":
        return None
    operand = fn.child_by_field_name("operand")
    field_id = fn.child_by_field_name("field")
    if operand is None or field_id is None:
        return None
    if _text(field_id, source).strip() != "AddCommand":
        return None
    parent = _text(operand, source).strip()
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for arg in args.named_children:
        child = _text(arg, source).strip()
        if child:
            # Only need to record one edge to identify a parent for the
            # child; merge step deduplicates.
            return _AddCommandEdge(parent_var=parent, child_var=child)
    return None


def _try_cobra_flag_call(node: Node, source: bytes) -> tuple[str, str] | None:
    """Detect `<cmd>.Flags().<Method>("flag-name", ...)` →
    return (cmd_var, flag_name) or None."""
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "selector_expression":
        return None
    method_node = fn.child_by_field_name("field")
    operand = fn.child_by_field_name("operand")
    if method_node is None or operand is None:
        return None
    method = _text(method_node, source).strip()
    if method not in _COBRA_FLAG_METHODS:
        return None
    # operand should be a call_expression (...).Flags() or
    # (...).PersistentFlags()
    if operand.type != "call_expression":
        return None
    inner_fn = operand.child_by_field_name("function")
    if inner_fn is None or inner_fn.type != "selector_expression":
        return None
    inner_method = inner_fn.child_by_field_name("field")
    inner_operand = inner_fn.child_by_field_name("operand")
    if inner_method is None or inner_operand is None:
        return None
    flagger = _text(inner_method, source).strip()
    if flagger not in {"Flags", "PersistentFlags"}:
        return None
    var = _text(inner_operand, source).strip()
    flag_name = _flag_name_from_args(node, source, method=method)
    if not flag_name:
        return None
    return (var, flag_name)


def _try_go_flag_call(node: Node, source: bytes, path: str) -> _GoFlagSite | None:
    """Detect `flag.String("name", ...)` style calls from the standard
    library `flag` package."""
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "selector_expression":
        return None
    operand = fn.child_by_field_name("operand")
    field_id = fn.child_by_field_name("field")
    if operand is None or field_id is None:
        return None
    pkg = _text(operand, source).strip()
    method = _text(field_id, source).strip()
    if pkg != _GO_FLAG_PACKAGE:
        return None
    if method not in _GO_FLAG_FUNCTIONS:
        return None
    flag_name = _flag_name_from_args(node, source, method=method)
    if not flag_name:
        return None
    return _GoFlagSite(
        flag_name=flag_name,
        source_path=path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )


def _flag_name_from_args(node: Node, source: bytes, *, method: str) -> str | None:
    """Read the flag-name argument out of a flag-defining call.

    Cobra/`flag` shapes:
      - `Var(p Pointer, "name", ...)`         — name at index 1
      - `String("name", default, "usage")`    — name at index 0
      - `StringVar(p, "name", default, ...)`  — name at index 1
      - `StringP("name", "n", default, ...)`  — name at index 0
      - `StringVarP(p, "name", "n", ...)`     — name at index 1
    """
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    string_args = [
        a for a in args.named_children if a.type == "interpreted_string_literal"
    ]
    if not string_args:
        return None
    target = string_args[0]
    raw = _text(target, source)
    return _strip_quotes(raw)


# ---------------------------------------------------------------------------
# Merging across files
# ---------------------------------------------------------------------------


def _merge_scans(scans: list[_FileScan]) -> list[CliCommand]:
    # Resolve var → literal across all scans; the same var name in
    # different files would be ambiguous but in practice each command
    # var is package-local so this works for typical Cobra layouts.
    by_var: dict[str, _CommandLiteral] = {}
    for scan in scans:
        for lit in scan.literals:
            if lit.var_name:
                # Last-writer-wins; rare collisions don't change quality.
                by_var[lit.var_name] = lit

    # Build child → parent var map.
    parent_of: dict[str, str] = {}
    for scan in scans:
        for edge in scan.add_command_edges:
            parent_of.setdefault(edge.child_var, edge.parent_var)

    out: list[CliCommand] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for scan in scans:
        for lit in scan.literals:
            parent_path = _resolve_parent_path(lit.var_name, parent_of, by_var)
            key = (parent_path, lit.name, lit.source_path)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(
                CliCommand(
                    name=lit.name,
                    parent_path=parent_path,
                    flags=sorted(set(lit.flags)),
                    source_path=lit.source_path,
                    source_start_line=lit.start_line,
                    source_end_line=lit.end_line,
                )
            )

    # Synthesize a single `flag`-package "command" per cmd/<bin>/...
    # directory if a flag.* call was seen there.
    flag_root_by_dir: dict[str, list[_GoFlagSite]] = {}
    for scan in scans:
        for site in scan.flag_sites:
            cmd_root = _cmd_root_dir(site.source_path)
            if cmd_root is None:
                continue
            flag_root_by_dir.setdefault(cmd_root, []).append(site)

    for cmd_root, sites in flag_root_by_dir.items():
        if not sites:
            continue
        binary_name = cmd_root.rsplit("/", 1)[-1]
        out.append(
            CliCommand(
                name=binary_name,
                parent_path="",
                flags=sorted({s.flag_name for s in sites if s.flag_name}),
                source_path=sites[0].source_path,
                source_start_line=sites[0].start_line,
                source_end_line=sites[-1].end_line,
            )
        )

    out.sort(key=lambda c: (c.parent_path, c.name, c.source_path))
    return out


def _resolve_parent_path(
    var: str,
    parent_of: dict[str, str],
    by_var: dict[str, _CommandLiteral],
) -> str:
    """Walk parent links upward until no parent is registered.

    Returns the slash-joined chain of parent command names (excluding
    the leaf), e.g. `validate` under `rootCmd` returns `mytool` if
    `rootCmd.Use == "mytool"`.
    """
    if not var:
        return ""
    chain: list[str] = []
    seen: set[str] = set()
    current = parent_of.get(var)
    while current and current not in seen:
        seen.add(current)
        parent_lit = by_var.get(current)
        if parent_lit is not None:
            chain.append(parent_lit.name)
        current = parent_of.get(current)
    chain.reverse()
    return "/".join(chain)


def _cmd_root_dir(path: str) -> str | None:
    """For `cmd/foo/bar/main.go` return `cmd/foo`."""
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "cmd":
        return f"cmd/{parts[1]}"
    return None


# ---------------------------------------------------------------------------
# Tree-sitter helpers
# ---------------------------------------------------------------------------


def _walk(root: Node):
    """Yield every descendant node (depth-first, named only)."""
    cursor = root.walk()
    visited_children = False
    while True:
        if not visited_children:
            yield cursor.node
            if cursor.goto_first_child():
                continue
            visited_children = True
        elif cursor.goto_next_sibling():
            visited_children = False
        elif not cursor.goto_parent():
            break
        else:
            visited_children = True


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _strip_quotes(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and s[0] in {'"', "`"} and s[-1] == s[0]:
        return s[1:-1]
    return s


__all__ = ("extract_cli_commands",)
