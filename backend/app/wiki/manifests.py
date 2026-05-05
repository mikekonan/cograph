"""Pre-LLM extraction of grounded facts from a checkout + indexed DB state.

Walks the checkout once per generation run and produces a typed
`RepoManifests` snapshot the planner and writer prompts can cite verbatim.
Zero LLM calls; the grammar is "fact + (file path, lines, snippet)" so the
LLM has nothing to hallucinate from.

Eight extractors, each capped at 30 entries, each fault-isolated (errors
logged, fields left empty on failure):

  - runtimes        — go.mod / pyproject.toml / package.json / Dockerfile FROM
  - run_commands    — Makefile targets / npm scripts / docker-compose services
  - config_keys     — env-var reads in code (regex-based; well-bounded false
                      positives surfaced via evidence snippets)
  - dependencies    — go.mod require / package.json deps / pyproject deps /
                      requirements.txt / Cargo.toml
  - public_api      — exported code_nodes from the DB (uppercase first char for
                      Go, no leading underscore for Python)
  - exported_types  — exported struct/interface/class/type_alias nodes plus
                      their public fields and methods (DB, parent_id walk)
  - error_types     — Go *Error structs / Python Exception subclasses
  - use_cases       — examples/ tree contents + README "Usage"/"Examples" section

Manifest entries carry `(file_path, lines, snippet)` so the writer can drop
`path/to/Makefile:12-15` next to a claim — the writer prompt enforces that
unsupported claims be dropped or moved to "Open questions".
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode

logger = logging.getLogger(__name__)


# Per-extractor cap. Past ~30, the LLM's context budget is the bottleneck
# rather than recall, so an extra dependency in the list rarely earns its
# tokens. Tuneable but conservative.
_ENTRY_CAP: int = 30

# Snippet excerpt length kept tight so 30×6 entries fit inside one cached
# context block without crowding out the file tree.
_SNIPPET_CAP_CHARS: int = 200

# Hard cap on file size we read from disk. Large lock files / generated SQL
# dumps would blow tokens for no signal.
_FILE_READ_CAP_BYTES: int = 256 * 1024


class ManifestEvidence(BaseModel):
    """Where a manifest fact came from. Cite-friendly: writers reference
    `source_file_path` directly in prose; the line range scopes the claim."""

    source_file_path: str
    source_lines: tuple[int, int] | None = None
    snippet: str = ""


class Runtime(BaseModel):
    name: str
    version: str | None = None
    evidence: ManifestEvidence


class RunCommand(BaseModel):
    label: str
    kind: str
    evidence: ManifestEvidence


class ConfigKey(BaseModel):
    key: str
    kind: str
    evidence: ManifestEvidence


class Dependency(BaseModel):
    name: str
    version: str | None = None
    ecosystem: str
    evidence: ManifestEvidence


class PublicApiEntry(BaseModel):
    qualified_name: str
    kind: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None


class TypeField(BaseModel):
    """A single field on an `ExportedType`. Captured via `parent_id` walk
    over `code_nodes` — `name` is the leaf identifier, `type_signature` is
    the raw declared type as parsed (Go `*Validator`, Python `Optional[int]`,
    TS `Map<string, Schema>`)."""

    name: str
    type_signature: str | None = None
    file_path: str
    start_line: int | None = None


class ExportedType(BaseModel):
    """A struct / interface / class / type alias on the public surface.

    `fields` and `methods` are populated by walking `code_nodes.parent_id`;
    `methods` carries qualified names only (the writer wires them up against
    `<retrieved_code_chunks>` separately)."""

    qualified_name: str
    kind: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    doc_comment: str | None = None
    fields: list[TypeField] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)


class ErrorType(BaseModel):
    """A user-defined error / exception type on the public surface.

    Go convention: struct whose name ends in `Error` (e.g. `ValidationError`).
    Python convention: class that inherits from `Exception` / `BaseException`
    or any leaf whose name ends in `Error`."""

    qualified_name: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    language: str
    doc_comment: str | None = None


class UseCase(BaseModel):
    label: str
    evidence: ManifestEvidence


class RepoManifests(BaseModel):
    """Snapshot of grounded facts the planner + writer prompts use as ground
    truth. All lists capped; missing extractors degrade to empty lists, never
    raise."""

    runtimes: list[Runtime] = Field(default_factory=list)
    run_commands: list[RunCommand] = Field(default_factory=list)
    config_keys: list[ConfigKey] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    public_api: list[PublicApiEntry] = Field(default_factory=list)
    exported_types: list[ExportedType] = Field(default_factory=list)
    error_types: list[ErrorType] = Field(default_factory=list)
    use_cases: list[UseCase] = Field(default_factory=list)


# Files that drive runtime / dependency detection. Lowercased filename match
# is sufficient — checkouts are case-sensitive on Linux but git normalises
# README/Dockerfile etc. to canonical capitalisation.
_RUNTIME_FILE_NAMES: tuple[str, ...] = (
    "go.mod",
    "pyproject.toml",
    "package.json",
    "Dockerfile",
    "Cargo.toml",
)


def _read_text(path: Path) -> str | None:
    """Read a file as UTF-8, capped, returning None on any I/O failure."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > _FILE_READ_CAP_BYTES:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _trim_snippet(text: str) -> str:
    text = text.strip()
    if len(text) <= _SNIPPET_CAP_CHARS:
        return text
    return text[: _SNIPPET_CAP_CHARS - 1].rstrip() + "…"


def _relpath(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _line_of(text: str, needle: str, start: int = 0) -> int | None:
    """Return the 1-indexed line number where `needle` first appears at
    `start` or later, else None."""
    idx = text.find(needle, start)
    if idx < 0:
        return None
    return text.count("\n", 0, idx) + 1


# ---------------------------------------------------------------------------
# Runtimes
# ---------------------------------------------------------------------------


_GO_MOD_GO_LINE_RE = re.compile(r"^go\s+(\d+(?:\.\d+){1,2})\b", re.MULTILINE)


def _extract_go_runtime(checkout: Path) -> Runtime | None:
    path = checkout / "go.mod"
    text = _read_text(path)
    if not text:
        return None
    match = _GO_MOD_GO_LINE_RE.search(text)
    if not match:
        return None
    line = text.count("\n", 0, match.start()) + 1
    return Runtime(
        name="go",
        version=match.group(1),
        evidence=ManifestEvidence(
            source_file_path=_relpath(path, checkout),
            source_lines=(line, line),
            snippet=_trim_snippet(match.group(0)),
        ),
    )


def _extract_python_runtime(checkout: Path) -> Runtime | None:
    path = checkout / "pyproject.toml"
    text = _read_text(path)
    if not text:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    version: str | None = None
    project = data.get("project") or {}
    if isinstance(project, dict):
        rp = project.get("requires-python")
        if isinstance(rp, str):
            version = rp.strip()
    if version is None:
        tool = data.get("tool") or {}
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        deps = poetry.get("dependencies") if isinstance(poetry, dict) else None
        if isinstance(deps, dict):
            py = deps.get("python")
            if isinstance(py, str):
                version = py.strip()
    if version is None:
        return None
    line = _line_of(text, "requires-python") or _line_of(text, "python") or 1
    return Runtime(
        name="python",
        version=version,
        evidence=ManifestEvidence(
            source_file_path=_relpath(path, checkout),
            source_lines=(line, line),
            snippet=_trim_snippet(f"requires-python = {version!r}"),
        ),
    )


def _extract_node_runtime(checkout: Path) -> Runtime | None:
    path = checkout / "package.json"
    text = _read_text(path)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    engines = data.get("engines") if isinstance(data, dict) else None
    node = engines.get("node") if isinstance(engines, dict) else None
    if not isinstance(node, str) or not node.strip():
        return None
    line = _line_of(text, '"node"') or 1
    return Runtime(
        name="node",
        version=node.strip(),
        evidence=ManifestEvidence(
            source_file_path=_relpath(path, checkout),
            source_lines=(line, line),
            snippet=_trim_snippet(f'"node": {node!r}'),
        ),
    )


_DOCKERFILE_FROM_RE = re.compile(
    r"^\s*FROM\s+(?P<image>[^\s]+)", re.IGNORECASE | re.MULTILINE
)


def _extract_dockerfile_runtimes(checkout: Path) -> list[Runtime]:
    results: list[Runtime] = []
    candidates = [
        checkout / "Dockerfile",
        *sorted(checkout.glob("Dockerfile.*")),
    ]
    for path in candidates:
        text = _read_text(path)
        if not text:
            continue
        for match in _DOCKERFILE_FROM_RE.finditer(text):
            image = match.group("image")
            if ":" in image:
                name, _, version = image.partition(":")
            else:
                name, version = image, None
            line = text.count("\n", 0, match.start()) + 1
            results.append(
                Runtime(
                    name=name.lower(),
                    version=version,
                    evidence=ManifestEvidence(
                        source_file_path=_relpath(path, checkout),
                        source_lines=(line, line),
                        snippet=_trim_snippet(match.group(0)),
                    ),
                )
            )
    return results


def extract_runtimes(checkout: Path) -> list[Runtime]:
    runtimes: list[Runtime] = []
    for fn in (_extract_go_runtime, _extract_python_runtime, _extract_node_runtime):
        try:
            entry = fn(checkout)
        except Exception:
            logger.exception("manifests: runtime extractor %s failed", fn.__name__)
            entry = None
        if entry is not None:
            runtimes.append(entry)
    try:
        runtimes.extend(_extract_dockerfile_runtimes(checkout))
    except Exception:
        logger.exception("manifests: Dockerfile runtime extraction failed")
    return runtimes[:_ENTRY_CAP]


# ---------------------------------------------------------------------------
# Run commands
# ---------------------------------------------------------------------------


_MAKEFILE_TARGET_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_\-]*)\s*:(?!=)", re.MULTILINE)


def _extract_makefile_targets(checkout: Path) -> list[RunCommand]:
    results: list[RunCommand] = []
    for filename in ("Makefile", "makefile", "GNUmakefile"):
        path = checkout / filename
        text = _read_text(path)
        if not text:
            continue
        for match in _MAKEFILE_TARGET_RE.finditer(text):
            target = match.group(1)
            if target.startswith(".") or target in {"PHONY", "DEFAULT", "SUFFIXES"}:
                continue
            line = text.count("\n", 0, match.start()) + 1
            results.append(
                RunCommand(
                    label=f"make {target}",
                    kind="make",
                    evidence=ManifestEvidence(
                        source_file_path=_relpath(path, checkout),
                        source_lines=(line, line),
                        snippet=_trim_snippet(match.group(0)),
                    ),
                )
            )
        # Only consider one Makefile variant.
        if results:
            break
    return results


def _extract_npm_scripts(checkout: Path) -> list[RunCommand]:
    path = checkout / "package.json"
    text = _read_text(path)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    results: list[RunCommand] = []
    for name, body in scripts.items():
        if not isinstance(name, str) or not isinstance(body, str):
            continue
        line = _line_of(text, f'"{name}"') or 1
        results.append(
            RunCommand(
                label=f"npm run {name}",
                kind="npm-script",
                evidence=ManifestEvidence(
                    source_file_path=_relpath(path, checkout),
                    source_lines=(line, line),
                    snippet=_trim_snippet(f'"{name}": {body!r}'),
                ),
            )
        )
    return results


_COMPOSE_SERVICE_RE = re.compile(
    r"^(?P<indent>\s{2,4})(?P<name>[a-zA-Z][\w\-]*):\s*$",
    re.MULTILINE,
)


def _extract_compose_services(checkout: Path) -> list[RunCommand]:
    results: list[RunCommand] = []
    for filename in (
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    ):
        path = checkout / filename
        text = _read_text(path)
        if not text:
            continue
        # Only collect services under a top-level `services:` key. We don't
        # parse YAML to keep the scanner dependency-free; the indent-based
        # heuristic below is "good enough" for the canonical layout.
        services_idx = text.find("\nservices:")
        if services_idx < 0 and not text.startswith("services:"):
            continue
        section_start = max(services_idx, 0)
        section_end = len(text)
        # Find the next top-level (col-0) section to bound the search.
        for top_match in re.finditer(r"^[a-zA-Z]", text):
            if top_match.start() <= section_start:
                continue
            section_end = top_match.start()
            break
        section = text[section_start:section_end]
        for match in _COMPOSE_SERVICE_RE.finditer(section):
            indent = match.group("indent")
            if len(indent) != 2:
                # Service names sit two spaces in under `services:`. Deeper
                # indent is a nested key (volumes:, environment:, etc.).
                continue
            name = match.group("name")
            line_in_file = text.count("\n", 0, section_start + match.start()) + 1
            results.append(
                RunCommand(
                    label=f"docker compose up {name}",
                    kind="docker-compose-service",
                    evidence=ManifestEvidence(
                        source_file_path=_relpath(path, checkout),
                        source_lines=(line_in_file, line_in_file),
                        snippet=_trim_snippet(f"services > {name}"),
                    ),
                )
            )
        if results:
            break
    return results


def _extract_go_cmd_mains(checkout: Path) -> list[RunCommand]:
    cmd_dir = checkout / "cmd"
    if not cmd_dir.is_dir():
        return []
    results: list[RunCommand] = []
    for child in sorted(cmd_dir.iterdir()):
        if not child.is_dir():
            continue
        main_go = child / "main.go"
        if not main_go.is_file():
            continue
        results.append(
            RunCommand(
                label=f"go run ./cmd/{child.name}",
                kind="go-cmd",
                evidence=ManifestEvidence(
                    source_file_path=_relpath(main_go, checkout),
                    source_lines=(1, 1),
                    snippet=_trim_snippet(f"package main in cmd/{child.name}/"),
                ),
            )
        )
    return results


def extract_run_commands(checkout: Path) -> list[RunCommand]:
    commands: list[RunCommand] = []
    for fn in (
        _extract_makefile_targets,
        _extract_npm_scripts,
        _extract_compose_services,
        _extract_go_cmd_mains,
    ):
        try:
            commands.extend(fn(checkout))
        except Exception:
            logger.exception("manifests: run-command extractor %s failed", fn.__name__)
    return commands[:_ENTRY_CAP]


# ---------------------------------------------------------------------------
# Config keys
# ---------------------------------------------------------------------------


# `os.getenv("FOO")` / `os.environ.get("FOO")` / `os.environ["FOO"]`
_PY_ENV_RE = re.compile(
    r"\bos\.(?:getenv|environ\.get|environ\[)\(?\s*['\"]([A-Z_][A-Z0-9_]+)['\"]"
)
# Go: `os.Getenv("FOO")`, `viper.GetString("foo.bar")`
_GO_ENV_RE = re.compile(r"\bos\.(?:Getenv|LookupEnv)\(\s*\"([A-Z_][A-Z0-9_]+)\"")
_GO_VIPER_RE = re.compile(r"\bviper\.Get[A-Za-z]+\(\s*\"([a-zA-Z][a-zA-Z0-9_.\-]+)\"")
# Node: `process.env.FOO` / `process.env["FOO"]`
_NODE_ENV_RE = re.compile(r"\bprocess\.env(?:\.|\[\s*['\"])([A-Z_][A-Z0-9_]+)\b")


_CONFIG_KEY_PATTERNS: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = (
    (_PY_ENV_RE, "env-var", (".py",)),
    (_GO_ENV_RE, "env-var", (".go",)),
    (_GO_VIPER_RE, "config-key", (".go",)),
    (_NODE_ENV_RE, "env-var", (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")),
)


def extract_config_keys(checkout: Path) -> list[ConfigKey]:
    """Regex-scan source files for env-var / viper / process.env reads.

    Each match yields a `ConfigKey` with an evidence snippet. Walk skips the
    same vendored / generated dirs as `language_scanner._SKIP_DIR_NAMES` —
    re-imported here would create a cyclic dep, so the lookup table is
    duplicated.
    """
    skip_dirs = _SKIP_DIRS
    seen: dict[tuple[str, str], ConfigKey] = {}
    for path in _walk_source_files(checkout, skip_dirs):
        if len(seen) >= _ENTRY_CAP:
            break
        suffix = path.suffix.lower()
        text: str | None = None
        for pattern, kind, suffixes in _CONFIG_KEY_PATTERNS:
            if suffix not in suffixes:
                continue
            if text is None:
                text = _read_text(path)
                if text is None:
                    break
            for match in pattern.finditer(text):
                key = match.group(1)
                dedupe_key = (key, kind)
                if dedupe_key in seen:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                line_text = (
                    text.splitlines()[line - 1]
                    if line - 1 < len(text.splitlines())
                    else match.group(0)
                )
                seen[dedupe_key] = ConfigKey(
                    key=key,
                    kind=kind,
                    evidence=ManifestEvidence(
                        source_file_path=_relpath(path, checkout),
                        source_lines=(line, line),
                        snippet=_trim_snippet(line_text),
                    ),
                )
                if len(seen) >= _ENTRY_CAP:
                    break
            if len(seen) >= _ENTRY_CAP:
                break
    return list(seen.values())[:_ENTRY_CAP]


# Mirror of `language_scanner._SKIP_DIR_NAMES`, kept local to avoid the
# cross-module import cycle (language_scanner doesn't depend on wiki, and
# we want to keep it that way).
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        "bower_components",
        "vendor",
        "third_party",
        "third-party",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".output",
        ".turbo",
        ".cache",
        "coverage",
        ".coverage",
        ".terraform",
        "Pods",
        "DerivedData",
    }
)


def _walk_source_files(checkout: Path, skip_dirs: frozenset[str]):
    """Stack-based walk that skips vendored dirs and symlinks."""
    stack: list[Path] = [checkout]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name in skip_dirs:
                        continue
                    stack.append(entry)
                elif entry.is_file():
                    yield entry
            except OSError:
                continue


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


_GO_MOD_REQUIRE_RE = re.compile(
    r"^\s*(?P<module>[^\s]+)\s+(?P<version>v[^\s]+)",
    re.MULTILINE,
)


def _extract_go_dependencies(checkout: Path) -> list[Dependency]:
    path = checkout / "go.mod"
    text = _read_text(path)
    if not text:
        return []
    results: list[Dependency] = []
    # Iterate over `require ( ... )` blocks AND single-line `require`s.
    block_re = re.compile(r"require\s*\((?P<body>.*?)\)", re.DOTALL)
    body_segments: list[tuple[int, str]] = []
    for block in block_re.finditer(text):
        line_offset = text.count("\n", 0, block.start("body")) + 1
        body_segments.append((line_offset, block.group("body")))
    # Single-line `require module v1.2.3` outside any block.
    single_re = re.compile(r"^\s*require\s+(\S+)\s+(v\S+)", re.MULTILINE)
    for match in single_re.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        results.append(
            Dependency(
                name=match.group(1),
                version=match.group(2),
                ecosystem="go",
                evidence=ManifestEvidence(
                    source_file_path=_relpath(path, checkout),
                    source_lines=(line, line),
                    snippet=_trim_snippet(match.group(0)),
                ),
            )
        )
    for line_offset, body in body_segments:
        for match in _GO_MOD_REQUIRE_RE.finditer(body):
            module = match.group("module")
            if module.startswith("//") or module == "":
                continue
            line = line_offset + body.count("\n", 0, match.start())
            results.append(
                Dependency(
                    name=module,
                    version=match.group("version"),
                    ecosystem="go",
                    evidence=ManifestEvidence(
                        source_file_path=_relpath(path, checkout),
                        source_lines=(line, line),
                        snippet=_trim_snippet(match.group(0)),
                    ),
                )
            )
    return results


def _extract_npm_dependencies(checkout: Path) -> list[Dependency]:
    path = checkout / "package.json"
    text = _read_text(path)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    results: list[Dependency] = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(section) if isinstance(data, dict) else None
        if not isinstance(deps, dict):
            continue
        for name, version in deps.items():
            if not isinstance(name, str):
                continue
            line = _line_of(text, f'"{name}"') or 1
            results.append(
                Dependency(
                    name=name,
                    version=version if isinstance(version, str) else None,
                    ecosystem="npm",
                    evidence=ManifestEvidence(
                        source_file_path=_relpath(path, checkout),
                        source_lines=(line, line),
                        snippet=_trim_snippet(f'"{name}": {version!r}'),
                    ),
                )
            )
    return results


def _extract_python_dependencies(checkout: Path) -> list[Dependency]:
    results: list[Dependency] = []
    pyproject = checkout / "pyproject.toml"
    text = _read_text(pyproject)
    if text:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            data = {}
        rel = _relpath(pyproject, checkout)
        project = data.get("project") or {}
        deps = project.get("dependencies") if isinstance(project, dict) else None
        if isinstance(deps, list):
            for raw in deps:
                if not isinstance(raw, str):
                    continue
                name, version = _split_pep508(raw)
                line = _line_of(text, raw) or 1
                results.append(
                    Dependency(
                        name=name,
                        version=version,
                        ecosystem="pypi",
                        evidence=ManifestEvidence(
                            source_file_path=rel,
                            source_lines=(line, line),
                            snippet=_trim_snippet(raw),
                        ),
                    )
                )
        tool = data.get("tool") or {}
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        poetry_deps = poetry.get("dependencies") if isinstance(poetry, dict) else None
        if isinstance(poetry_deps, dict):
            for name, spec in poetry_deps.items():
                if name == "python":
                    continue
                version = spec if isinstance(spec, str) else None
                line = _line_of(text, name) or 1
                results.append(
                    Dependency(
                        name=name,
                        version=version,
                        ecosystem="pypi",
                        evidence=ManifestEvidence(
                            source_file_path=rel,
                            source_lines=(line, line),
                            snippet=_trim_snippet(f"{name} = {spec!r}"),
                        ),
                    )
                )
    for filename in ("requirements.txt", "requirements-dev.txt"):
        req_path = checkout / filename
        req_text = _read_text(req_path)
        if not req_text:
            continue
        rel = _relpath(req_path, checkout)
        for line_no, raw in enumerate(req_text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            name, version = _split_pep508(stripped)
            results.append(
                Dependency(
                    name=name,
                    version=version,
                    ecosystem="pypi",
                    evidence=ManifestEvidence(
                        source_file_path=rel,
                        source_lines=(line_no, line_no),
                        snippet=_trim_snippet(stripped),
                    ),
                )
            )
    return results


def _extract_cargo_dependencies(checkout: Path) -> list[Dependency]:
    path = checkout / "Cargo.toml"
    text = _read_text(path)
    if not text:
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    rel = _relpath(path, checkout)
    results: list[Dependency] = []
    deps = data.get("dependencies")
    if isinstance(deps, dict):
        for name, spec in deps.items():
            if isinstance(spec, str):
                version = spec
            elif isinstance(spec, dict):
                v = spec.get("version")
                version = v if isinstance(v, str) else None
            else:
                version = None
            line = _line_of(text, name) or 1
            results.append(
                Dependency(
                    name=name,
                    version=version,
                    ecosystem="cargo",
                    evidence=ManifestEvidence(
                        source_file_path=rel,
                        source_lines=(line, line),
                        snippet=_trim_snippet(f"{name} = {spec!r}"),
                    ),
                )
            )
    return results


_PEP508_NAME_RE = re.compile(r"^([A-Za-z0-9_.\-]+)")
_PEP508_VERSION_RE = re.compile(r"([<>=!~]=?\s*[^,;\s]+)")


def _split_pep508(spec: str) -> tuple[str, str | None]:
    """Split a PEP 508-ish requirement into (name, version-spec)."""
    match = _PEP508_NAME_RE.match(spec)
    if not match:
        return spec.strip(), None
    name = match.group(1)
    rest = spec[match.end() :].strip()
    versions = _PEP508_VERSION_RE.findall(rest)
    version = ",".join(v.strip() for v in versions) or None
    return name, version


def extract_dependencies(checkout: Path) -> list[Dependency]:
    deps: list[Dependency] = []
    for fn in (
        _extract_go_dependencies,
        _extract_npm_dependencies,
        _extract_python_dependencies,
        _extract_cargo_dependencies,
    ):
        try:
            deps.extend(fn(checkout))
        except Exception:
            logger.exception("manifests: dependency extractor %s failed", fn.__name__)
    return deps[:_ENTRY_CAP]


# ---------------------------------------------------------------------------
# Public API (DB-backed)
# ---------------------------------------------------------------------------


_FUNCTION_NODE_TYPES: tuple[str, ...] = (
    "function",
    "method",
    "class",
    "struct",
    "interface",
)


async def extract_public_api(
    *,
    session: AsyncSession,
    repository_id: UUID,
    cap: int = _ENTRY_CAP,
) -> list[PublicApiEntry]:
    """Pull exported code nodes from the DB. Exported = uppercase first
    char (Go convention) OR no leading underscore (Python convention).

    We can't tell language at the SQL level cheaply, so we filter both
    `qualified_name LIKE` patterns and let the post-filter remove the
    rest. Cap inclusive.
    """
    stmt = (
        select(
            CodeNode.qualified_name,
            CodeNode.node_type,
            CodeNode.file_path,
            CodeNode.start_line,
            CodeNode.end_line,
            CodeNode.language,
        )
        .where(CodeNode.repository_id == repository_id)
        .where(CodeNode.node_type.in_(_FUNCTION_NODE_TYPES))
        .order_by(CodeNode.qualified_name.asc())
        .limit(cap * 4)
    )
    rows = (await session.execute(stmt)).all()
    out: list[PublicApiEntry] = []
    for row in rows:
        if not _is_exported(row.qualified_name, row.language):
            continue
        out.append(
            PublicApiEntry(
                qualified_name=row.qualified_name,
                kind=row.node_type,
                file_path=row.file_path,
                start_line=int(row.start_line) if row.start_line is not None else None,
                end_line=int(row.end_line) if row.end_line is not None else None,
            )
        )
        if len(out) >= cap:
            break
    return out


def _is_exported(qualified_name: str, language: str) -> bool:
    if not qualified_name:
        return False
    leaf = qualified_name.rsplit(".", 1)[-1]
    leaf = leaf.split("(", 1)[0]
    if not leaf:
        return False
    if language == "go":
        return leaf[0].isupper()
    if language == "python":
        return not leaf.startswith("_")
    # Default: treat anything not underscore-prefixed as exported.
    return not leaf.startswith("_")


# ---------------------------------------------------------------------------
# Exported types (DB-backed, parent_id walk)
# ---------------------------------------------------------------------------


_TYPE_NODE_TYPES: tuple[str, ...] = (
    "struct",
    "interface",
    "class",
    "type_alias",
)

_FIELD_NODE_TYPES: tuple[str, ...] = ("attribute", "variable")

# Per-type caps. Beyond these, the writer can pull from the broader public-API
# manifest — these are scoped to keep one type's footprint inside a single
# context block.
_FIELDS_PER_TYPE_CAP: int = 12
_METHODS_PER_TYPE_CAP: int = 8


# Parse a leading type signature to its type expression.
#   Go:     `Name string` / `Name *Validator` / `Name map[string]Schema`
#   Python: `name: int` / `name: Optional[Foo]`
#   TS:     `name: Map<string, Schema>`
# We accept the right-hand side after the first space (Go) or the colon
# (Python/TS). Conservative — when unparseable, return None.
_GO_FIELD_SIG_RE = re.compile(r"^\s*[A-Za-z_]\w*\s+(?P<type>[^/\n]+?)(?:\s+`|$)")
_COLON_TYPE_RE = re.compile(r":\s*(?P<type>[^=#\n]+?)\s*(?:=|$)")


def _parse_field_type(signature: str | None, language: str) -> str | None:
    if not signature:
        return None
    sig = signature.strip()
    if language == "go":
        match = _GO_FIELD_SIG_RE.match(sig)
        if match:
            return match.group("type").strip().rstrip(",")
        return None
    if language in ("python", "typescript", "javascript"):
        match = _COLON_TYPE_RE.search(sig)
        if match:
            return match.group("type").strip().rstrip(",")
    return None


def _trim_doc_comment(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if len(cleaned) <= _SNIPPET_CAP_CHARS:
        return cleaned
    return cleaned[: _SNIPPET_CAP_CHARS - 1].rstrip() + "…"


async def extract_exported_types(
    *,
    session: AsyncSession,
    repository_id: UUID,
    cap: int = _ENTRY_CAP,
) -> list[ExportedType]:
    """Pull exported struct / interface / class / type_alias nodes plus their
    public fields (parent_id-children with `attribute` / `variable` kind) and
    method names (parent_id-children with `method` kind).

    Two queries — one for parent types, one batched fetch for children of
    every parent in the page. We don't issue N+1 queries per type.
    """
    parent_stmt = (
        select(
            CodeNode.id,
            CodeNode.qualified_name,
            CodeNode.node_type,
            CodeNode.file_path,
            CodeNode.start_line,
            CodeNode.end_line,
            CodeNode.language,
            CodeNode.doc_comment,
        )
        .where(CodeNode.repository_id == repository_id)
        .where(CodeNode.node_type.in_(_TYPE_NODE_TYPES))
        .order_by(CodeNode.qualified_name.asc())
        .limit(cap * 4)
    )
    parent_rows = (await session.execute(parent_stmt)).all()

    keepers: list[tuple[UUID, str, str, str, int | None, int | None, str | None]] = []
    for row in parent_rows:
        if not _is_exported(row.qualified_name, row.language):
            continue
        keepers.append(
            (
                row.id,
                row.qualified_name,
                row.node_type,
                row.file_path,
                int(row.start_line) if row.start_line is not None else None,
                int(row.end_line) if row.end_line is not None else None,
                row.doc_comment,
            )
        )
        if len(keepers) >= cap:
            break

    if not keepers:
        return []

    parent_ids = [item[0] for item in keepers]
    children_stmt = (
        select(
            CodeNode.parent_id,
            CodeNode.name,
            CodeNode.qualified_name,
            CodeNode.node_type,
            CodeNode.signature,
            CodeNode.file_path,
            CodeNode.start_line,
            CodeNode.language,
        )
        .where(CodeNode.repository_id == repository_id)
        .where(CodeNode.parent_id.in_(parent_ids))
        .order_by(CodeNode.start_line.asc().nulls_last())
    )
    children_rows = (await session.execute(children_stmt)).all()

    fields_by_parent: dict[UUID, list[TypeField]] = {pid: [] for pid in parent_ids}
    methods_by_parent: dict[UUID, list[str]] = {pid: [] for pid in parent_ids}
    for child in children_rows:
        if child.parent_id is None:
            continue
        if not _is_exported(child.qualified_name, child.language):
            continue
        if child.node_type in _FIELD_NODE_TYPES:
            field_list = fields_by_parent.get(child.parent_id)
            if field_list is None or len(field_list) >= _FIELDS_PER_TYPE_CAP:
                continue
            field_list.append(
                TypeField(
                    name=child.name,
                    type_signature=_parse_field_type(child.signature, child.language),
                    file_path=child.file_path,
                    start_line=int(child.start_line)
                    if child.start_line is not None
                    else None,
                )
            )
        elif child.node_type == "method":
            method_list = methods_by_parent.get(child.parent_id)
            if method_list is None or len(method_list) >= _METHODS_PER_TYPE_CAP:
                continue
            method_list.append(child.qualified_name)

    out: list[ExportedType] = []
    for parent_id, qn, kind, fp, sl, el, doc in keepers:
        out.append(
            ExportedType(
                qualified_name=qn,
                kind=kind,
                file_path=fp,
                start_line=sl,
                end_line=el,
                doc_comment=_trim_doc_comment(doc),
                fields=fields_by_parent.get(parent_id, []),
                methods=methods_by_parent.get(parent_id, []),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Error types (DB-backed)
# ---------------------------------------------------------------------------


# Match a Python class header that inherits from Exception / BaseException /
# any *Error class. We deliberately scan `signature` (or the first line of
# `content`) rather than keeping a full inheritance graph here — the wiki
# extractor wants a coarse "is this an error" signal, not perfect classification.
_PY_ERROR_BASE_RE = re.compile(
    r"class\s+\w+\s*\(\s*[^)]*?(?:BaseException|Exception|Error)\b"
)


def _is_error_type(node_type: str, name: str, signature: str | None, language: str) -> bool:
    leaf = name.rsplit(".", 1)[-1]
    if language == "go":
        # Go convention: error types are structs whose name ends in `Error`.
        if node_type != "struct":
            return False
        return leaf.endswith("Error") and leaf != "Error"
    if language == "python":
        if node_type != "class":
            return False
        if leaf.endswith("Error") or leaf.endswith("Exception"):
            return True
        if signature and _PY_ERROR_BASE_RE.search(signature):
            return True
        return False
    return False


async def extract_error_types(
    *,
    session: AsyncSession,
    repository_id: UUID,
    cap: int = _ENTRY_CAP,
) -> list[ErrorType]:
    """Pull user-defined error / exception types from `code_nodes`.

    Cheap heuristic — Go: exported struct whose leaf name ends in `Error`.
    Python: exported class that inherits from Exception / *Error / *Exception
    by name pattern or signature scan.
    """
    stmt = (
        select(
            CodeNode.qualified_name,
            CodeNode.name,
            CodeNode.node_type,
            CodeNode.file_path,
            CodeNode.start_line,
            CodeNode.end_line,
            CodeNode.language,
            CodeNode.signature,
            CodeNode.doc_comment,
        )
        .where(CodeNode.repository_id == repository_id)
        .where(CodeNode.node_type.in_(("struct", "class")))
        .order_by(CodeNode.qualified_name.asc())
        .limit(cap * 8)
    )
    rows = (await session.execute(stmt)).all()
    out: list[ErrorType] = []
    for row in rows:
        if not _is_exported(row.qualified_name, row.language):
            continue
        if not _is_error_type(row.node_type, row.name, row.signature, row.language):
            continue
        out.append(
            ErrorType(
                qualified_name=row.qualified_name,
                file_path=row.file_path,
                start_line=int(row.start_line) if row.start_line is not None else None,
                end_line=int(row.end_line) if row.end_line is not None else None,
                language=row.language,
                doc_comment=_trim_doc_comment(row.doc_comment),
            )
        )
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


_USAGE_HEADING_RE = re.compile(
    r"^#+\s*(usage|examples?|getting started|quick ?start)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_examples_dir(checkout: Path) -> list[UseCase]:
    examples = checkout / "examples"
    if not examples.is_dir():
        return []
    results: list[UseCase] = []
    for entry in sorted(examples.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            label = f"examples/{entry.name}"
            snippet = f"directory examples/{entry.name}/"
        elif entry.is_file():
            label = f"examples/{entry.name}"
            snippet = entry.name
        else:
            continue
        results.append(
            UseCase(
                label=label,
                evidence=ManifestEvidence(
                    source_file_path=_relpath(entry, checkout),
                    source_lines=None,
                    snippet=_trim_snippet(snippet),
                ),
            )
        )
    return results


def _extract_readme_usage(checkout: Path) -> list[UseCase]:
    for filename in ("README.md", "readme.md", "Readme.md", "README.MD"):
        path = checkout / filename
        text = _read_text(path)
        if not text:
            continue
        match = _USAGE_HEADING_RE.search(text)
        if not match:
            continue
        start = match.start()
        # Bound the snippet at the next heading of equal-or-higher rank or
        # 800 chars, whichever comes first.
        end = len(text)
        for next_match in re.finditer(r"^#+\s+\S", text[match.end() :], re.MULTILINE):
            end = match.end() + next_match.start()
            break
        end = min(end, start + 800)
        section = text[start:end]
        line = text.count("\n", 0, start) + 1
        return [
            UseCase(
                label=match.group(1).title(),
                evidence=ManifestEvidence(
                    source_file_path=_relpath(path, checkout),
                    source_lines=(line, line),
                    snippet=_trim_snippet(section),
                ),
            )
        ]
    return []


def extract_use_cases(checkout: Path) -> list[UseCase]:
    cases: list[UseCase] = []
    for fn in (_extract_readme_usage, _extract_examples_dir):
        try:
            cases.extend(fn(checkout))
        except Exception:
            logger.exception("manifests: use-case extractor %s failed", fn.__name__)
    return cases[:_ENTRY_CAP]


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


async def build_repo_manifests(
    *,
    session: AsyncSession,
    repository_id: UUID,
    checkout_path: Path | str | None,
) -> RepoManifests:
    """Run all 6 extractors. Each is fault-isolated; on any exception the
    relevant list comes back empty rather than aborting the whole run.

    `checkout_path` is None for code paths that haven't been plumbed yet
    (e.g. the `--stages 1-3` CLI). Filesystem extractors degrade to empty;
    the DB-backed `public_api` still runs.
    """
    if checkout_path is not None:
        checkout = Path(checkout_path)
        runtimes = extract_runtimes(checkout)
        run_commands = extract_run_commands(checkout)
        config_keys = extract_config_keys(checkout)
        dependencies = extract_dependencies(checkout)
        use_cases = extract_use_cases(checkout)
    else:
        runtimes = []
        run_commands = []
        config_keys = []
        dependencies = []
        use_cases = []

    try:
        public_api = await extract_public_api(
            session=session, repository_id=repository_id
        )
    except Exception:
        logger.exception("manifests: public_api extractor failed")
        public_api = []

    try:
        exported_types = await extract_exported_types(
            session=session, repository_id=repository_id
        )
    except Exception:
        logger.exception("manifests: exported_types extractor failed")
        exported_types = []

    try:
        error_types = await extract_error_types(
            session=session, repository_id=repository_id
        )
    except Exception:
        logger.exception("manifests: error_types extractor failed")
        error_types = []

    return RepoManifests(
        runtimes=runtimes,
        run_commands=run_commands,
        config_keys=config_keys,
        dependencies=dependencies,
        public_api=public_api,
        exported_types=exported_types,
        error_types=error_types,
        use_cases=use_cases,
    )


__all__ = [
    "ConfigKey",
    "Dependency",
    "ErrorType",
    "ExportedType",
    "ManifestEvidence",
    "PublicApiEntry",
    "RepoManifests",
    "RunCommand",
    "Runtime",
    "TypeField",
    "UseCase",
    "build_repo_manifests",
    "extract_config_keys",
    "extract_dependencies",
    "extract_error_types",
    "extract_exported_types",
    "extract_public_api",
    "extract_run_commands",
    "extract_runtimes",
    "extract_use_cases",
]
