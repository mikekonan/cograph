"""Where a bare TS/JS import lands inside the checkout.

`@/components/Button` or, under `baseUrl: "src"`, `ui/Button` is local code,
not an npm package. The compiler knows that from tsconfig/jsconfig; this reads
the same two options (`baseUrl`, `paths`) so the extractor can resolve such
imports to module QNs. In a monorepo `@acme/ui` is local too: every
`package.json` in the checkout names a package the others can import.
Stdlib only: ingest loads it, the extractor consumes it.

Repository content is untrusted: configs are read only inside the checkout,
and a config that does not parse is skipped, never fatal.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

_STRING = r'("(?:\\.|[^"\\])*")'
_COMMENT = re.compile(_STRING + r"|//[^\n]*|/\*.*?\*/", re.S)
_TRAILING_COMMA = re.compile(_STRING + r"|,(\s*[}\]])")
_CONFIG_NAME = re.compile(r"tsconfig.*\.json|jsconfig\.json")
_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
_ENTRY_SUFFIXES = (".ts", ".tsx", ".mts", ".js", ".jsx", ".mjs", ".cjs")

# (pattern, target): `@/*` -> `src/*`, or an exact specifier -> one file.
_Rules = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class TsScope:
    """Path mapping of the configs in one directory, checkout-relative."""

    base_url: str | None
    paths: _Rules
    # Top-level names under `baseUrl`: only these map through it, so `react`
    # stays external even when `baseUrl` is set.
    base_entries: frozenset[str]
    # Workspace packages, the same for every scope of a checkout.
    packages: _Rules = ()
    # Rules only. ponytail: a new top-level dir under `baseUrl` does not
    # re-extract its importers; they catch up when they next change.
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        rules = json.dumps([self.base_url, self.paths, self.packages])
        object.__setattr__(
            self, "fingerprint", hashlib.sha256(rules.encode()).hexdigest()[:12]
        )

    def map(self, specifier: str) -> str | None:
        # The compiler's order: `paths`, then `baseUrl`, then node_modules,
        # which is where a workspace package lives once installed.
        target = _match(self.paths, specifier)
        if target is None and self.base_url is not None:
            if specifier.split("/", 1)[0] in self.base_entries:
                target = posixpath.join(self.base_url, specifier)
        return target if target is not None else _match(self.packages, specifier)


# Keyed by config directory, checkout-relative ("." for the root).
TsConfig = dict[str, TsScope]


def ts_scope_for(config: TsConfig | None, relative_path: str | os.PathLike[str]) -> TsScope | None:
    """The scope of the nearest ancestor directory that has one."""
    if not config:
        return None
    for parent in PurePosixPath(Path(relative_path).as_posix()).parents:
        scope = config.get(parent.as_posix())
        if scope is not None:
            return scope
    return None


def load_ts_config(root: Path, skip_dirs: frozenset[str]) -> TsConfig:
    root = root.resolve()
    options_by_dir: dict[Path, dict[str, object]] = {}
    package_dirs: list[Path] = []
    for dirpath, dirnames, filenames in root.walk():
        dirnames[:] = [name for name in dirnames if name not in skip_dirs and name != ".git"]
        dirnames.sort()
        if "package.json" in filenames:
            package_dirs.append(dirpath)
        # Configs in one directory merge: a solution-style `tsconfig.json`
        # holds only `references`, its `tsconfig.app.json` holds the paths.
        # `tsconfig.json` wins a conflict.
        options: dict[str, object] = {}
        for name in sorted(
            (name for name in filenames if _CONFIG_NAME.fullmatch(name)),
            key=lambda name: (name != "tsconfig.json", name),
        ):
            for key, value in _read_compiler_options(dirpath / name, root, frozenset()).items():
                options.setdefault(key, value)
        if options:
            options_by_dir[dirpath] = options

    packages = _load_packages(root, package_dirs, skip_dirs)
    config: TsConfig = {}
    for dirpath, options in options_by_dir.items():
        scope = _build_scope(root, options, packages)
        # A directory without rules gets no scope, so a stray
        # `e2e/tsconfig.json` does not hide its parent's aliases.
        if scope is not None:
            config[dirpath.relative_to(root).as_posix()] = scope
    if packages and "." not in config:
        config["."] = TsScope(base_url=None, paths=(), base_entries=frozenset(), packages=packages)
    return config


def _match(rules: _Rules, specifier: str) -> str | None:
    for pattern, target in rules:
        prefix, star, suffix = pattern.partition("*")
        if not star:
            if specifier == pattern:
                return target
        elif (
            specifier.startswith(prefix)
            and specifier.endswith(suffix)
            and len(specifier) >= len(prefix) + len(suffix)
        ):
            return target.replace("*", specifier[len(prefix) : len(specifier) - len(suffix)], 1)
    return None


def _by_specificity(rule: tuple[str, str]) -> tuple[bool, int]:
    """Exact patterns first, then the longest prefix before the `*`."""
    return "*" in rule[0], -len(rule[0].partition("*")[0])


def _loads_jsonc(text: str) -> object:
    text = _COMMENT.sub(lambda match: match.group(1) or "", text)
    return json.loads(_TRAILING_COMMA.sub(lambda match: match.group(1) or match.group(2), text))


def _read_json(path: Path, root: Path) -> object:
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    try:
        return _loads_jsonc(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _read_compiler_options(path: Path, root: Path, seen: frozenset[Path]) -> dict[str, object]:
    """`baseUrl` / `paths` of one config, its relative `extends` chain applied.

    `baseUrl` comes back absolute, resolved against the config that sets it.
    `paths` travels with `_paths_dir`, which its targets resolve against when
    no `baseUrl` is set. A child's key replaces the parent's wholesale.
    """
    path = path.resolve()
    config = None if path in seen else _read_json(path, root)
    if not isinstance(config, dict):
        return {}

    options: dict[str, object] = {}
    extends = config.get("extends")
    for parent in extends if isinstance(extends, list) else [extends]:
        # npm presets (`@tsconfig/node20`) carry no local paths.
        if isinstance(parent, str) and parent.startswith("."):
            parent = parent if parent.endswith(".json") else f"{parent}.json"
            options.update(_read_compiler_options(path.parent / parent, root, seen | {path}))

    own = config.get("compilerOptions")
    if isinstance(own, dict):
        if isinstance(own.get("baseUrl"), str):
            options["baseUrl"] = (path.parent / own["baseUrl"]).resolve()
        if isinstance(own.get("paths"), dict):
            options["paths"] = own["paths"]
            options["_paths_dir"] = path.parent
    return options


def _build_scope(root: Path, options: dict[str, object], packages: _Rules) -> TsScope | None:
    base_dir = options.get("baseUrl")
    if not (isinstance(base_dir, Path) and base_dir.is_relative_to(root) and base_dir.is_dir()):
        base_dir = None
    targets_dir = base_dir or options.get("_paths_dir")

    paths: list[tuple[str, str]] = []
    raw_paths = options.get("paths")
    if isinstance(raw_paths, dict) and isinstance(targets_dir, Path):
        for pattern, targets in raw_paths.items():
            # ponytail: the catch-all `"*"` is skipped, it would send every
            # npm import (`react`) into the repo. `baseUrl` covers that case.
            if pattern == "*" or pattern.count("*") > 1:
                continue
            if not (isinstance(targets, list) and targets and isinstance(targets[0], str)):
                continue
            target = Path(os.path.normpath(targets_dir / targets[0]))
            if target.is_relative_to(root):
                paths.append((pattern, target.relative_to(root).as_posix()))
    if base_dir is None and not paths:
        return None
    paths.sort(key=_by_specificity)

    base_url = None if base_dir is None else base_dir.relative_to(root).as_posix()
    if base_url == ".":
        base_url = ""
    base_entries = (
        frozenset(entry.name if entry.is_dir() else entry.stem for entry in base_dir.iterdir())
        if base_dir is not None
        else frozenset()
    )
    return TsScope(
        base_url=base_url,
        paths=tuple(paths),
        base_entries=base_entries,
        packages=packages,
    )


def _load_packages(root: Path, package_dirs: list[Path], skip_dirs: frozenset[str]) -> _Rules:
    """Import rules for every named `package.json` in the checkout.

    Taken from every manifest rather than the `workspaces` globs: pnpm keeps
    those in YAML, and a package nobody imports costs nothing. A name two
    manifests claim is ambiguous and stays external.
    """
    by_name: dict[str, list[tuple[str, str]] | None] = {}
    for package_dir in package_dirs:
        manifest = _read_json(package_dir / "package.json", root)
        name = manifest.get("name") if isinstance(manifest, dict) else None
        if isinstance(name, str) and name:
            by_name[name] = (
                None
                if name in by_name
                else _package_rules(root, package_dir, name, manifest, skip_dirs)
            )
    rules = [rule for package_rules in by_name.values() if package_rules for rule in package_rules]
    return tuple(sorted(rules, key=_by_specificity))


def _package_rules(
    root: Path,
    package_dir: Path,
    name: str,
    manifest: dict[str, object],
    skip_dirs: frozenset[str],
) -> list[tuple[str, str]]:
    exports = manifest.get("exports")
    if not isinstance(exports, dict) or not any(key.startswith(".") for key in exports):
        # `"exports": "./x.js"` or a bare conditions object means `{".": ...}`.
        exports = {} if exports is None else {".": exports}

    rules: list[tuple[str, str]] = []
    for key, value in exports.items():
        if key.startswith(".") and key.count("*") <= 1:
            target = _pick_target(root, package_dir, value, skip_dirs)
            if target is not None:
                rules.append((name + key[1:], target))
    if not exports:
        rules.append((f"{name}/*", (package_dir / "*").relative_to(root).as_posix()))
    if not any(pattern == name for pattern, _ in rules):
        # `exports`/`main` usually point at build output, which is not in
        # the checkout; the source entry is.
        legacy = [manifest.get(key) for key in ("module", "main", "types")]
        conventional = [
            f"./{stem}{suffix}" for stem in ("src/index", "index") for suffix in _ENTRY_SUFFIXES
        ]
        target = _pick_target(root, package_dir, [*legacy, *conventional], skip_dirs)
        if target is not None:
            rules.append((name, target))
    return rules


def _pick_target(
    root: Path, package_dir: Path, value: object, skip_dirs: frozenset[str]
) -> str | None:
    """The first export target that is source in the checkout.

    Conditions are tried in manifest order, but a declaration file loses to
    an implementation: `{"types": "./index.d.ts", "default": "./index.js"}`
    lands on the code. A `*` target counts when the directory it expands in
    exists.
    """
    candidates = [target for target in _flatten(value) if target.count("*") <= 1]
    for candidate in sorted(candidates, key=lambda target: target.endswith(_DECLARATION_SUFFIXES)):
        prefix, star, _ = candidate.partition("*")
        path = Path(os.path.normpath(package_dir / (prefix if star else candidate)))
        if star and not prefix.endswith("/"):
            path = path.parent
        if not path.is_relative_to(root):
            continue
        relative = path.relative_to(root)
        if any(part in skip_dirs for part in relative.parts):
            continue
        if path.is_dir() if star else path.is_file():
            return Path(os.path.normpath(package_dir / candidate)).relative_to(root).as_posix()
    return None


def _flatten(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [target for item in value for target in _flatten(item)]
    if isinstance(value, dict):
        return [target for item in value.values() for target in _flatten(item)]
    return []
