"""Where a bare TS/JS import lands inside the checkout.

`@/components/Button` or, under `baseUrl: "src"`, `ui/Button` is local code,
not an npm package. The compiler knows that from tsconfig/jsconfig; this reads
the same two options (`baseUrl`, `paths`) so the extractor can resolve such
imports to module QNs. Stdlib only: ingest loads it, the extractor consumes it.

Repository content is untrusted: configs are read only inside the checkout,
and a config that does not parse is skipped, never fatal.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_STRING = r'("(?:\\.|[^"\\])*")'
_COMMENT = re.compile(_STRING + r"|//[^\n]*|/\*.*?\*/", re.S)
_TRAILING_COMMA = re.compile(_STRING + r"|,(\s*[}\]])")
_CONFIG_NAME = re.compile(r"tsconfig.*\.json|jsconfig\.json")


@dataclass(frozen=True, slots=True)
class TsScope:
    """Path mapping of the configs in one directory, checkout-relative."""

    base_url: str | None
    # (pattern, first target), exact patterns first, then longest prefix.
    paths: tuple[tuple[str, str], ...]
    # Top-level names under `baseUrl`: only these map through it, so `react`
    # stays external even when `baseUrl` is set.
    base_entries: frozenset[str]
    # Rules only. ponytail: a new top-level dir under `baseUrl` does not
    # re-extract its importers; they catch up when they next change.
    fingerprint: str

    def map(self, specifier: str) -> str | None:
        for pattern, target in self.paths:
            prefix, star, suffix = pattern.partition("*")
            if not star:
                if specifier == pattern:
                    return target
            elif (
                specifier.startswith(prefix)
                and specifier.endswith(suffix)
                and len(specifier) >= len(prefix) + len(suffix)
            ):
                return target.replace(
                    "*", specifier[len(prefix) : len(specifier) - len(suffix)], 1
                )
        if self.base_url is not None and specifier.split("/", 1)[0] in self.base_entries:
            return posixpath.join(self.base_url, specifier)
        return None


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
    config: TsConfig = {}
    for dirpath, dirnames, filenames in root.walk():
        dirnames[:] = [name for name in dirnames if name not in skip_dirs and name != ".git"]
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
        scope = _build_scope(root, options)
        # A directory without rules gets no scope, so a stray
        # `e2e/tsconfig.json` does not hide its parent's aliases.
        if scope is not None:
            config[dirpath.relative_to(root).as_posix()] = scope
    return config


def _loads_jsonc(text: str) -> object:
    text = _COMMENT.sub(lambda match: match.group(1) or "", text)
    return json.loads(_TRAILING_COMMA.sub(lambda match: match.group(1) or match.group(2), text))


def _read_compiler_options(path: Path, root: Path, seen: frozenset[Path]) -> dict[str, object]:
    """`baseUrl` / `paths` of one config, its relative `extends` chain applied.

    `baseUrl` comes back absolute, resolved against the config that sets it.
    `paths` travels with `_paths_dir`, which its targets resolve against when
    no `baseUrl` is set. A child's key replaces the parent's wholesale.
    """
    path = path.resolve()
    if path in seen or not path.is_relative_to(root) or not path.is_file():
        return {}
    try:
        config = _loads_jsonc(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
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


def _build_scope(root: Path, options: dict[str, object]) -> TsScope | None:
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
    paths.sort(key=lambda item: ("*" in item[0], -len(item[0].partition("*")[0])))

    base_url = None if base_dir is None else base_dir.relative_to(root).as_posix()
    if base_url == ".":
        base_url = ""
    base_entries = (
        frozenset(entry.name if entry.is_dir() else entry.stem for entry in base_dir.iterdir())
        if base_dir is not None
        else frozenset()
    )
    fingerprint = hashlib.sha256(json.dumps([base_url, paths]).encode()).hexdigest()[:12]
    return TsScope(
        base_url=base_url,
        paths=tuple(paths),
        base_entries=base_entries,
        fingerprint=fingerprint,
    )
