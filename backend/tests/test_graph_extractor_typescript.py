"""TypeScript/JavaScript extraction — the parity surface with Go.

The contract pinned here:

  * every node records `metadata["exported"]` from SYNTAX (the `export`
    keyword / CommonJS `module.exports`), never from name heuristics —
    manifests gate public_api on it;
  * import targets arrive dot-canonical and pre-resolved against the
    importing file (`./foo` → `src.foo`), riding the existing
    `canonical as alias` builder machinery;
  * `.ts`/`.tsx`/`.js` all flow through one walker (the grammars are
    supersets of each other).
"""

from __future__ import annotations

from backend.app.graph.extractor import GraphEdgeType, GraphExtractor, GraphNodeType
from backend.app.graph.parser import GraphParser


def _extract(file_path: str, source_text: str):
    parsed = GraphParser().parse_source(file_path=file_path, source_text=source_text)
    extracted = GraphExtractor().extract(parsed)
    nodes = {node.qualified_name: node for node in extracted.nodes}
    edges = {(edge.edge_type, edge.source, edge.target) for edge in extracted.edges}
    return extracted, nodes, edges


def test_typescript_service_class_symbols_edges_and_export_flags():
    source_text = """\
import { Repo, helper as h } from "./repo";
import * as ns from "../lib";

/** Service docs */
export class UserService extends Repo implements Contract {
  private secret = "x";
  readonly label: string;
  onClick = (e: Event) => { this.audit(e); };
  public async login(id: string): Promise<boolean> {
    h(id);
    this.repo.save(id);
    return true;
  }
  #hidden() {}
}

const internal = (v: string) => ns.normalize(v);
export const MAX_RETRIES = 3;
"""
    _, nodes, edges = _extract("src/services/user.ts", source_text)

    assert set(nodes) == {
        "src.services.user",
        "src.services.user.UserService",
        "src.services.user.UserService.secret",
        "src.services.user.UserService.label",
        "src.services.user.UserService.onClick",
        "src.services.user.UserService.login",
        "src.services.user.UserService.#hidden",
        "src.services.user.internal",
        "src.services.user.MAX_RETRIES",
    }

    service = nodes["src.services.user.UserService"]
    assert service.node_type is GraphNodeType.CLASS
    assert service.metadata["exported"] is True
    assert service.metadata["bases"] == ["Repo", "Contract"]
    assert service.doc_comment == "/** Service docs */"
    assert service.language.value == "typescript"

    login = nodes["src.services.user.UserService.login"]
    assert login.node_type is GraphNodeType.METHOD
    assert login.metadata["exported"] is True
    assert login.metadata["async"] is True
    assert login.signature == "public async login(id: string): Promise<boolean>"

    # The export gate: syntax, not names. No underscores anywhere, yet:
    assert nodes["src.services.user.UserService.secret"].metadata["exported"] is False
    assert nodes["src.services.user.UserService.#hidden"].metadata["exported"] is False
    assert nodes["src.services.user.internal"].metadata["exported"] is False
    assert nodes["src.services.user.MAX_RETRIES"].metadata["exported"] is True
    assert nodes["src.services.user.MAX_RETRIES"].node_type is GraphNodeType.CONSTANT

    on_click = nodes["src.services.user.UserService.onClick"]
    assert on_click.node_type is GraphNodeType.METHOD  # arrow field = method

    assert (
        GraphEdgeType.IMPORTS,
        "src.services.user",
        "src.services.repo.Repo",
    ) in edges
    assert (
        GraphEdgeType.IMPORTS,
        "src.services.user",
        "src.services.repo.helper as h",
    ) in edges
    assert (GraphEdgeType.IMPORTS, "src.services.user", "src.lib as ns") in edges
    assert (
        GraphEdgeType.INHERITS,
        "src.services.user.UserService",
        "Repo",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "src.services.user.UserService.login",
        "h",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "src.services.user.UserService.login",
        "this.repo.save",
    ) in edges
    # Calls inside arrow callbacks attribute to the enclosing named symbol.
    assert (
        GraphEdgeType.CALLS,
        "src.services.user.UserService.onClick",
        "this.audit",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "src.services.user.internal",
        "ns.normalize",
    ) in edges


def test_tsx_arrow_component_extracts_as_function():
    source_text = """\
import { useState } from "react";

export const Button = ({ label }: Props) => {
  const [busy, setBusy] = useState(false);
  return <button disabled={busy}>{label}</button>;
};
"""
    _, nodes, edges = _extract("src/components/Button.tsx", source_text)

    button = nodes["src.components.Button.Button"]
    assert button.node_type is GraphNodeType.FUNCTION
    assert button.metadata["exported"] is True
    assert (
        GraphEdgeType.IMPORTS,
        "src.components.Button",
        "react.useState",
    ) in edges
    assert (
        GraphEdgeType.CALLS,
        "src.components.Button.Button",
        "useState",
    ) in edges


def test_typescript_interface_alias_and_enum():
    source_text = """\
export interface Handler extends Base {
  handle(event: string): void;
  name: string;
}

export type Result = { ok: boolean } | null;

export enum Color {
  Red,
  Green,
}

interface Hidden {
  x: number;
}
"""
    _, nodes, edges = _extract("src/types.ts", source_text)

    handler = nodes["src.types.Handler"]
    assert handler.node_type is GraphNodeType.INTERFACE
    assert handler.metadata["exported"] is True
    assert (GraphEdgeType.INHERITS, "src.types.Handler", "Base") in edges

    handle = nodes["src.types.Handler.handle"]
    assert handle.node_type is GraphNodeType.METHOD
    assert nodes["src.types.Handler.name"].node_type is GraphNodeType.ATTRIBUTE

    assert nodes["src.types.Result"].node_type is GraphNodeType.TYPE_ALIAS

    color = nodes["src.types.Color"]
    assert color.node_type is GraphNodeType.CLASS  # no DB enum migration
    assert color.metadata["ts_kind"] == "enum"

    assert nodes["src.types.Hidden"].metadata["exported"] is False


def test_javascript_commonjs_module_exports_mark_exported():
    source_text = """\
const path = require("node:path");
const helper = require("./helper");

function make(name) {
  return helper.build(path.join("/tmp", name));
}

function internalOnly() {
  return 1;
}

class Tool extends Base {
  run() {
    make("x");
  }
}

module.exports = { make, Tool };
exports.extra = internalOnly;
"""
    _, nodes, edges = _extract("lib/factory.js", source_text)

    assert nodes["lib.factory.make"].metadata["exported"] is True
    assert nodes["lib.factory.Tool"].metadata["exported"] is True
    # `exports.extra = internalOnly` exports it under another name — the
    # local symbol still counts as exported surface.
    assert nodes["lib.factory.internalOnly"].metadata["exported"] is True
    assert nodes["lib.factory.make"].language.value == "javascript"

    # require() is an import edge, not a variable node. `node:path` binds to
    # the same local name — no alias suffix needed.
    assert "lib.factory.path" not in nodes
    assert (GraphEdgeType.IMPORTS, "lib.factory", "path") in edges
    assert (GraphEdgeType.IMPORTS, "lib.factory", "lib.helper as helper") in edges
    assert (GraphEdgeType.INHERITS, "lib.factory.Tool", "Base") in edges
    assert (GraphEdgeType.CALLS, "lib.factory.Tool.run", "make") in edges


def test_index_ts_strips_trailing_segment_like_python_init():
    source_text = 'export { UserService } from "./services/user";\n'
    _, nodes, edges = _extract("src/index.ts", source_text)

    assert "src" in nodes  # not src.index
    assert (
        GraphEdgeType.IMPORTS,
        "src",
        "src.services.user.UserService",
    ) in edges


def test_relative_import_of_directory_index_resolves_to_directory_qn():
    source_text = 'import { Button } from "./components";\n'
    _, _, edges = _extract("src/App.tsx", source_text)
    # `src/components/index.tsx` also canonicalizes to `src.components` —
    # the two ends of the edge meet without filesystem knowledge.
    assert (GraphEdgeType.IMPORTS, "src.App", "src.components.Button") in edges


def test_function_overloads_share_a_qualified_name():
    source_text = """\
export function pick(a: string): string;
export function pick(a: number): number;
export function pick(a: unknown): unknown {
  return a;
}
"""
    extracted, _, _ = _extract("src/pick.ts", source_text)
    overloads = [
        node for node in extracted.nodes if node.qualified_name == "src.pick.pick"
    ]
    # Three declarations, one QN — the builder folds them into one node with
    # metadata["overloads"], same as Go build variants.
    assert len(overloads) == 3
    assert all(node.metadata["exported"] is True for node in overloads)


def test_export_default_and_deferred_export_statement():
    source_text = """\
function main() {
  run();
}

const helper = () => 1;

export default main;
export { helper };
"""
    _, nodes, _ = _extract("src/cli.ts", source_text)

    assert nodes["src.cli.main"].metadata["exported"] is True
    assert nodes["src.cli.helper"].metadata["exported"] is True


def test_export_default_anonymous_function_gets_default_name():
    source_text = "export default function (): void {}\n"
    _, nodes, _ = _extract("src/anon.ts", source_text)

    anon = nodes["src.anon.default"]
    assert anon.node_type is GraphNodeType.FUNCTION
    assert anon.metadata["exported"] is True
    assert anon.metadata["default_export"] is True


def test_nestjs_decorators_infer_roles():
    source_text = """\
import { Controller, Injectable } from "@nestjs/common";

@Controller("users")
export class UsersController {}

@Injectable()
export class UsersService {}
"""
    _, nodes, _ = _extract("src/users.ts", source_text)

    assert nodes["src.users.UsersController"].role == "entry_point"
    assert nodes["src.users.UsersService"].role == "service"


def test_export_default_anonymous_class_is_extracted():
    # Codex-debate F2: `export default class {...}` arrives via the `value`
    # field with node type `class` — it must not vanish from the graph.
    source_text = """\
export default class extends Base {
  run() {}
}
"""
    _, nodes, edges = _extract("src/page.ts", source_text)

    page = nodes["src.page.default"]
    assert page.node_type is GraphNodeType.CLASS
    assert page.metadata["exported"] is True
    assert page.metadata["default_export"] is True
    assert (GraphEdgeType.INHERITS, "src.page.default", "Base") in edges
    assert nodes["src.page.default.run"].node_type is GraphNodeType.METHOD


def test_javascript_class_fields_use_property_field_name():
    # Codex-debate F3: the `javascript` grammar calls class fields
    # `field_definition` and names them via `property`, not `name`.
    source_text = """\
export class C {
  x = 1;
  run = () => this.go();
  go() {}
}
"""
    _, nodes, edges = _extract("lib/c.js", source_text)

    assert nodes["lib.c.C.x"].node_type is GraphNodeType.ATTRIBUTE
    assert nodes["lib.c.C.run"].node_type is GraphNodeType.METHOD
    assert (GraphEdgeType.CALLS, "lib.c.C.run", "this.go") in edges


def test_declare_class_methods_are_extracted():
    # Codex-debate F3: ambient `declare class` bodies use `method_signature`.
    source_text = """\
export declare class Client {
  fetch(url: string): Promise<string>;
}
"""
    _, nodes, _ = _extract("src/client.d.ts", source_text)

    # `.d.ts` keeps its own `client.d` QN — never colliding with (or stealing
    # imports from) a runtime `client.ts`/`client.js` sibling.
    assert nodes["src.client.d.Client.fetch"].node_type is GraphNodeType.METHOD


def test_dotted_module_filenames_resolve_consistently():
    # Codex-debate F9: `./user.service` is a module name, not an extension —
    # NestJS/Angular naming must round-trip between module QN and imports.
    _, service_nodes, _ = _extract(
        "src/user.service.ts", "export class UserService {}\n"
    )
    assert "src.user.service.UserService" in service_nodes

    _, _, edges = _extract(
        "src/app.ts", 'import { UserService } from "./user.service";\n'
    )
    assert (
        GraphEdgeType.IMPORTS,
        "src.app",
        "src.user.service.UserService",
    ) in edges

    # Real runtime extensions are still stripped.
    _, _, js_edges = _extract("src/main.ts", 'import { x } from "./legacy.js";\n')
    assert (GraphEdgeType.IMPORTS, "src.main", "src.legacy.x") in js_edges


def test_commonjs_chained_export_assignment():
    # Codex-debate F4: `module.exports = exports = f` walks the chain.
    source_text = """\
function f() {}
module.exports = exports = f;
"""
    _, nodes, _ = _extract("lib/chain.js", source_text)
    assert nodes["lib.chain.f"].metadata["exported"] is True
