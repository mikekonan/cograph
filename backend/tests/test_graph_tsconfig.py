"""tsconfig/jsconfig path mapping: what `load_ts_config` reads from a checkout.

Configs are repository content, so a broken or hostile one must degrade to
"no mapping", never fail the ingest.
"""

from __future__ import annotations

from pathlib import Path

from backend.app.graph.ingest import _JS_TS_SKIP_DIR_NAMES as _SKIP
from backend.app.graph.ts_resolve import load_ts_config, ts_scope_for


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_solution_style_config_merges_sibling_app_config(tmp_path):
    _write(tmp_path, "app/tsconfig.json", '{"files": [], "references": [{"path": "./tsconfig.app.json"}]}')
    _write(
        tmp_path,
        "app/tsconfig.app.json",
        """\
{
  // "paths": {"nope/*": ["x/*"]} stays a comment
  "compilerOptions": {
    "paths": {"@/*": ["./src/*"], "@env": ["./src/env.ts"], "*": ["./types/*"]},
  },
}
""",
    )
    _write(tmp_path, "node_modules/pkg/tsconfig.json", '{"compilerOptions": {"baseUrl": "."}}')

    config = load_ts_config(tmp_path, _SKIP)

    assert set(config) == {"app"}
    scope = ts_scope_for(config, "app/src/pages/Home.tsx")
    assert scope is not None
    assert scope.paths == (("@env", "app/src/env.ts"), ("@/*", "app/src/*"))
    assert scope.map("@/ui/Button") == "app/src/ui/Button"
    assert scope.map("@env") == "app/src/env.ts"
    assert scope.map("react") is None
    assert ts_scope_for(config, "tools/build.ts") is None


def test_base_url_through_extends_maps_only_existing_entries(tmp_path):
    _write(tmp_path, "tsconfig.base.json", '{"compilerOptions": {"baseUrl": "./src"}}')
    _write(tmp_path, "tsconfig.json", '{"extends": "./tsconfig.base"}')
    _write(tmp_path, "src/ui/Button.tsx", "export const Button = 1;\n")
    _write(tmp_path, "src/api.ts", "export {};\n")
    # A stray config without rules must not hide the root's mapping.
    _write(tmp_path, "e2e/tsconfig.json", '{"compilerOptions": {"strict": true}}')
    # Broken and escaping configs are skipped, not fatal.
    _write(tmp_path, "broken/tsconfig.json", '{"compilerOptions": ')
    _write(tmp_path, "escape/tsconfig.json", '{"extends": "../../outside/tsconfig.json"}')
    _write(tmp_path, "cycle/tsconfig.json", '{"extends": "./tsconfig.json"}')

    config = load_ts_config(tmp_path, _SKIP)

    scope = ts_scope_for(config, "e2e/login.spec.ts")
    assert scope is not None and scope.base_url == "src"
    assert scope.map("ui/Button") == "src/ui/Button"
    assert scope.map("api") == "src/api"
    assert scope.map("react") is None
    assert set(config) == {"."}


def test_workspace_packages_resolve_to_their_source(tmp_path):
    _write(
        tmp_path,
        "packages/ui/package.json",
        """{"name": "@acme/ui", "exports": {
            ".": {"types": "./index.d.ts", "default": "./index.js"},
            "./button": "./src/button.tsx",
            "./icons/*": {"import": "./dist/icons/*.js", "types": "./src/icons/*.ts"},
            "./internal": "./dist/internal.js"
        }}""",
    )
    for relative in ("index.d.ts", "index.js", "src/button.tsx", "src/icons/add.ts"):
        _write(tmp_path, f"packages/ui/{relative}", "export {};\n")
    # `exports` only names build output; the source entry is what we index.
    _write(tmp_path, "packages/core/package.json", '{"name": "@acme/core", "exports": "./dist/index.js"}')
    _write(tmp_path, "packages/core/dist/index.js", "")
    _write(tmp_path, "packages/core/src/index.ts", "export {};\n")
    # No `exports`: `main` is build output, subpaths map straight through.
    _write(tmp_path, "packages/legacy/package.json", '{"name": "legacy-utils", "main": "dist/index.js"}')
    _write(tmp_path, "packages/legacy/index.js", "")
    # Two manifests claim one name: ambiguous, stays external.
    _write(tmp_path, "packages/a/package.json", '{"name": "@acme/dup"}')
    _write(tmp_path, "examples/a/package.json", '{"name": "@acme/dup"}')
    _write(tmp_path, "node_modules/react/package.json", '{"name": "react", "main": "index.js"}')
    _write(tmp_path, "node_modules/react/index.js", "")
    # `paths` outranks a workspace package, as in the compiler.
    _write(tmp_path, "app/tsconfig.json", '{"compilerOptions": {"paths": {"@acme/core": ["./shim.ts"]}}}')

    config = load_ts_config(tmp_path, _SKIP)

    root = ts_scope_for(config, "tools/build.ts")
    assert root is not None
    assert root.map("@acme/ui") == "packages/ui/index.js"
    assert root.map("@acme/ui/button") == "packages/ui/src/button.tsx"
    assert root.map("@acme/ui/icons/add") == "packages/ui/src/icons/add.ts"
    assert root.map("@acme/ui/internal") is None
    assert root.map("@acme/core") == "packages/core/src/index.ts"
    assert root.map("@acme/core/util") is None
    assert root.map("legacy-utils") == "packages/legacy/index.js"
    assert root.map("legacy-utils/fp/pad") == "packages/legacy/fp/pad"
    assert root.map("@acme/dup") is None
    assert root.map("react") is None

    app = ts_scope_for(config, "app/main.ts")
    assert app is not None
    assert app.map("@acme/core") == "app/shim.ts"
    assert app.map("@acme/ui/button") == "packages/ui/src/button.tsx"
