"""Stage 0: deterministic salience scoring + topic-candidate extraction.

This stage runs BEFORE `analyze_repo` and emits `RepoSignals` — a typed
bundle that downstream stages consume as input. No LLM calls; pure
Python.

The single job of this stage is to decide WHICH topics deserve to reach
the mindmap LLM. Page allocation has historically followed code volume,
which mis-promotes verbose internal scaffolding (e.g. a regression-test
validator on a CLI code generator). Stage 0 fixes that: each
`TopicCandidate` carries a `salience_tier` and only `public` /
`supporting` tiers are passed downstream — `internal` /
`test_scaffolding` candidates are suppressed.

S1 covers path heuristics + README mention detection + manifest-derived
public API filtering. S2 (CLI AST extraction) and S3 (docs/ outline
extraction) extend the same `TopicCandidate` list without changing
this module's public contract.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Final

from backend.app.wiki.context import FileTreeEntry, RepoContext
from backend.app.wiki.docs_outline import DocFile, build_docs_outline
from backend.app.wiki.manifests import PublicApiEntry
from backend.app.wiki.schemas import (
    CandidateKind,
    CliCommand,
    DocSection,
    PublicSymbol,
    RepoKind,
    RepoSignals,
    SalienceTier,
    TopicCandidate,
)


# ---------------------------------------------------------------------------
# Path patterns
# ---------------------------------------------------------------------------


_TEST_PATH_RE: Final = re.compile(
    r"(?:^|/)(?:"
    r"[^/]+_test\.go|"
    r"test_[^/]+\.py|"
    r"[^/]+_test\.py|"
    r"[^/]+\.test\.[jt]sx?|"
    r"[^/]+\.spec\.[jt]sx?|"
    r"[^/]+_spec\.rb"
    r")$"
    r"|"
    r"(?:^|/)(?:tests?|__tests__|testdata|fixtures?|golden|"
    r"snapshots?|__snapshots__|mocks?|stubs)(?:/|$)",
    re.IGNORECASE,
)


_INTERNAL_PATH_RE: Final = re.compile(
    r"(?:^|/)internal(?:/|$)",
)


_PUBLIC_TOP_DIR_RE: Final = re.compile(
    r"^(?:cmd|examples?|sample[s]?|docs?)(?:/|$)",
    re.IGNORECASE,
)


_CMD_PATH_RE: Final = re.compile(
    r"^cmd(?:/|$)",
)


_GENERATED_PATH_RE: Final = re.compile(
    r"(?:^|/)(?:gen|generated|\.gen|dist|build|out)(?:/|$)"
    r"|"
    r"_generated\.[a-zA-Z]+$"
    r"|"
    r"\.pb\.go$"
    r"|"
    r"^zz_generated\.",
    re.IGNORECASE,
)


_DOC_PATH_RE: Final = re.compile(
    r"^README(\.[a-zA-Z]+)?$"
    r"|"
    r"^(?:docs?)/.*\.(md|rst)$",
    re.IGNORECASE,
)


_TEST_SCAFFOLDING_NAME_RE: Final = re.compile(
    r"(?<![a-zA-Z])"
    r"(?:fixture|golden|snapshot|comparator|regression|harness|mock|stub)"
    r"(?![a-zA-Z])",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Scoring config (intentionally a dataclass so callers can override for
# experiments without touching the module).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    readme_heading_match: float = 0.30
    readme_code_block_mention: float = 0.25
    readme_inline_mention: float = 0.05
    docs_file_match: float = 0.30
    cmd_path_root: float = 0.25
    exported_symbol_outside_internal: float = 0.20
    public_path: float = 0.10
    internal_path: float = -0.20
    test_scaffolding_name: float = -0.20
    generated_output_path: float = -0.10

    tier_public_min: float = 0.55
    tier_supporting_min: float = 0.40
    tier_internal_min: float = 0.20


_DEFAULT_CONFIG: Final = ScoringConfig()


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


@dataclass
class _FileCluster:
    key: str
    title: str
    files: list[FileTreeEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_repo_signals(
    repo_context: RepoContext,
    *,
    cli_commands: list[CliCommand] | None = None,
    doc_files: list[DocFile] | None = None,
    config: ScoringConfig = _DEFAULT_CONFIG,
) -> RepoSignals:
    """Score topic candidates from a `RepoContext`. Pure / deterministic.

    Inputs that drive scoring:
        - `repo_context.file_tree` — path classification + clustering
        - `repo_context.readme_text` — heading / code-block mentions
        - `repo_context.manifests.public_api` — exported-symbol surface
        - `repo_context.top_summaries` — symbol attribution per cluster
        - `cli_commands` — optional CLI surface from the AST extractor;
          each command becomes a `PUBLIC` TopicCandidate so that real
          user-facing commands always out-rank verbose internal
          packages with similar names.
        - `doc_files` — optional `(path, content)` of doc / README
          markdown. Each H1/H2 heading in a curated doc seeds a
          `DOCS_TOPIC` TopicCandidate. Stale headings (no matching
          path/symbol) are demoted to SUPPORTING.

    Output `RepoSignals.topic_candidates` is sorted by descending
    salience_score then key for stable downstream behavior.
    """
    public_api_surface = _filter_public_api(
        repo_context.manifests.public_api,
    )
    by_path_symbols = _group_symbols_by_path(public_api_surface)

    clusters = _cluster_files(repo_context.file_tree)

    candidates: list[TopicCandidate] = []
    for cluster in clusters:
        candidate = _score_cluster(
            cluster,
            repo_context=repo_context,
            symbols_by_path=by_path_symbols,
            config=config,
        )
        candidates.append(candidate)

    cli_surface = list(cli_commands or [])
    if cli_surface:
        candidates.extend(_cli_topic_candidates(cli_surface))

    docs_sections: list[DocSection] = []
    if doc_files:
        repo_paths = {entry.file_path for entry in repo_context.file_tree}
        repo_symbols = {sym.qualified_name for sym in public_api_surface}
        # Include the bare symbol identifier (last `.`-separated part)
        # so heading text like "Generator architecture" matches the
        # exported `generator.Generator` symbol's last segment.
        repo_symbols.update(
            sym.qualified_name.rsplit(".", 1)[-1] for sym in public_api_surface
        )
        docs_result = build_docs_outline(
            doc_files,
            repo_paths=repo_paths,
            repo_symbol_names=repo_symbols,
        )
        docs_sections = docs_result.sections
        candidates.extend(docs_result.candidates)

    candidates.sort(key=lambda c: (-c.salience_score, c.normalized_key))

    suppressed = sum(
        1
        for c in candidates
        if c.salience_tier in (SalienceTier.INTERNAL, SalienceTier.TEST_SCAFFOLDING)
    )

    return RepoSignals(
        repo_kind_hint=_infer_repo_kind_hint(
            clusters=clusters,
            candidates=candidates,
            repo_context=repo_context,
        ),
        public_api_surface=public_api_surface,
        cli_surface=cli_surface,
        docs_outline=docs_sections,
        topic_candidates=candidates,
        suppressed_count=suppressed,
    )


def _cli_topic_candidates(commands: list[CliCommand]) -> list[TopicCandidate]:
    """Promote each extracted CLI command into a PUBLIC topic candidate.

    The score is set just above `tier_public_min` so the command always
    lands in the PUBLIC bucket, regardless of how the underlying file
    cluster was scored. This is intentional — the AST extractor's
    presence is dispositive evidence that the command is part of the
    binary's user-facing surface.
    """
    out: list[TopicCandidate] = []
    seen_keys: set[str] = set()
    for cmd in commands:
        if not cmd.name:
            continue
        path = "/".join(p for p in (cmd.parent_path, cmd.name) if p)
        key = f"cli:{path}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        evidence = [cmd.source_path] if cmd.source_path else []
        out.append(
            TopicCandidate(
                id=_make_id(key),
                title=cmd.name
                if not cmd.parent_path
                else f"{cmd.parent_path}/{cmd.name}",
                normalized_key=key,
                salience_score=1.0,
                salience_tier=SalienceTier.PUBLIC,
                candidate_kind=CandidateKind.CLI_COMMAND,
                reasons=["cli_ast_extracted"],
                evidence_paths=evidence,
                commands=[path],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Public-API filtering — drop anything under `internal/`.
# ---------------------------------------------------------------------------


def _filter_public_api(entries: list[PublicApiEntry]) -> list[PublicSymbol]:
    out: list[PublicSymbol] = []
    for entry in entries:
        if _INTERNAL_PATH_RE.search(entry.file_path):
            continue
        if _TEST_PATH_RE.search(entry.file_path):
            continue
        out.append(
            PublicSymbol(
                qualified_name=entry.qualified_name,
                kind=str(entry.kind),
                file_path=entry.file_path,
                start_line=entry.start_line,
                end_line=entry.end_line,
            )
        )
    return out


def _group_symbols_by_path(
    surface: list[PublicSymbol],
) -> dict[str, list[PublicSymbol]]:
    by_path: dict[str, list[PublicSymbol]] = defaultdict(list)
    for sym in surface:
        by_path[sym.file_path].append(sym)
    return by_path


# ---------------------------------------------------------------------------
# Clustering — group files into TopicCandidate-shaped buckets.
# ---------------------------------------------------------------------------


_CLUSTER_BY_SECOND_SEGMENT: Final = frozenset(
    {"cmd", "internal", "pkg", "lib", "src", "app"}
)
_TEST_TOP_DIRS: Final = frozenset(
    {
        "tests",
        "test",
        "__tests__",
        "testdata",
        "fixtures",
        "fixture",
        "golden",
        "snapshots",
        "__snapshots__",
        "mocks",
        "stubs",
    }
)
_DOC_TOP_DIRS: Final = frozenset({"docs", "doc"})
_EXAMPLE_TOP_DIRS: Final = frozenset({"examples", "example", "sample", "samples"})


def _cluster_files(file_tree: list[FileTreeEntry]) -> list[_FileCluster]:
    by_key: dict[str, _FileCluster] = {}
    for entry in file_tree:
        key, title = _cluster_key_and_title(entry.file_path)
        cluster = by_key.get(key)
        if cluster is None:
            cluster = _FileCluster(key=key, title=title, files=[])
            by_key[key] = cluster
        cluster.files.append(entry)
    return list(by_key.values())


def _cluster_key_and_title(path: str) -> tuple[str, str]:
    """Decide which cluster a file belongs to and its display title.

    Rules (top-down):
      - `cmd/<name>/...`        → `cmd:<name>` titled "<name>"
      - `internal/<pkg>/...`    → `internal:<pkg>` titled "internal/<pkg>"
      - `pkg/<name>/...`        → `pkg:<name>` titled "<name>"
      - `lib/<name>/...`        → `lib:<name>` titled "<name>"
      - `src/<name>/...`        → `src:<name>` titled "<name>"
      - `app/<name>/...`        → `app:<name>` titled "<name>"
      - `tests/`, `testdata/`…  → `tests:<dir>` titled "<dir>"
      - `examples/`, `sample/`  → `examples` titled "Examples"
      - `docs/`, `doc/`         → `docs` titled "Docs"
      - root file               → `root:<basename>` titled "<basename>"
      - other top-level dir     → `pkg:<dir>` titled "<dir>"
    """
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ("root:", "(root)")

    if len(parts) == 1:
        # Root file — its own cluster only if it's a doc-like file
        # (README); otherwise group root files into a "root" bucket so
        # we don't explode into per-file candidates.
        head = parts[0]
        if _DOC_PATH_RE.match(head):
            return (f"root:{head}", head)
        return ("root", "(root files)")

    head = parts[0]
    head_lower = head.lower()

    if head_lower in _CLUSTER_BY_SECOND_SEGMENT:
        second = parts[1]
        if head_lower == "internal":
            # Keep the "internal/" prefix in the title so the demotion
            # signal is visible in telemetry.
            return (f"internal:{second}", f"internal/{second}")
        return (f"{head_lower}:{second}", second)

    if head_lower in _TEST_TOP_DIRS:
        return (f"tests:{head_lower}", head_lower)

    if head_lower in _EXAMPLE_TOP_DIRS:
        return ("examples", "examples")

    if head_lower in _DOC_TOP_DIRS:
        return ("docs", "docs")

    return (f"pkg:{head}", head)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_cluster(
    cluster: _FileCluster,
    *,
    repo_context: RepoContext,
    symbols_by_path: dict[str, list[PublicSymbol]],
    config: ScoringConfig,
) -> TopicCandidate:
    paths = [f.file_path for f in cluster.files]
    test_paths = [p for p in paths if _TEST_PATH_RE.search(p)]
    non_test_paths = [p for p in paths if not _TEST_PATH_RE.search(p)]

    # All files are test scaffolding — short-circuit with a
    # zero-positive-signal candidate.
    if not non_test_paths:
        return TopicCandidate(
            id=_make_id(cluster.key),
            title=cluster.title,
            normalized_key=cluster.key,
            salience_score=0.0,
            salience_tier=SalienceTier.TEST_SCAFFOLDING,
            candidate_kind=CandidateKind.TEST_SCAFFOLDING,
            reasons=[],
            demotion_reasons=["all_files_test_scaffolding"],
            evidence_paths=test_paths[:10],
        )

    score = 0.0
    reasons: list[str] = []
    demotions: list[str] = []

    # Path-based positive signals (use a representative non-test path)
    representative = non_test_paths[0]

    if _CMD_PATH_RE.match(representative):
        score += config.cmd_path_root
        reasons.append("cmd_path_root")
    elif _PUBLIC_TOP_DIR_RE.match(representative):
        score += config.public_path
        reasons.append("path_under_public_dir")

    # Path-based negative signals
    if _INTERNAL_PATH_RE.search(representative):
        score += config.internal_path
        demotions.append("path_under_internal_dir")

    if _GENERATED_PATH_RE.search(representative):
        score += config.generated_output_path
        demotions.append("generated_output_path")

    # Symbol-based positive signal
    cluster_symbols = _collect_cluster_symbols(non_test_paths, symbols_by_path)
    if cluster_symbols:
        score += config.exported_symbol_outside_internal
        reasons.append(f"exported_symbols={len(cluster_symbols)}")

    # Test-scaffolding name pattern
    if _TEST_SCAFFOLDING_NAME_RE.search(
        cluster.title
    ) or _TEST_SCAFFOLDING_NAME_RE.search(cluster.key):
        score += config.test_scaffolding_name
        demotions.append("test_scaffolding_name")

    # README mention boost
    readme_score, readme_reason = _readme_mention_score(
        repo_context.readme_text or "",
        cluster_title=cluster.title,
        config=config,
    )
    if readme_reason:
        score += readme_score
        reasons.append(readme_reason)

    tier = _score_to_tier(score, config=config)
    kind = _infer_candidate_kind(cluster, cluster_symbols, paths)

    return TopicCandidate(
        id=_make_id(cluster.key),
        title=cluster.title,
        normalized_key=cluster.key,
        salience_score=round(score, 4),
        salience_tier=tier,
        candidate_kind=kind,
        reasons=reasons,
        demotion_reasons=demotions,
        evidence_paths=non_test_paths[:10],
        symbols=[s.qualified_name for s in cluster_symbols][:20],
    )


def _collect_cluster_symbols(
    paths: list[str],
    symbols_by_path: dict[str, list[PublicSymbol]],
) -> list[PublicSymbol]:
    out: list[PublicSymbol] = []
    seen_qns: set[str] = set()
    for p in paths:
        for sym in symbols_by_path.get(p, ()):
            if sym.qualified_name in seen_qns:
                continue
            seen_qns.add(sym.qualified_name)
            out.append(sym)
    return out


# ---------------------------------------------------------------------------
# README mention detection
# ---------------------------------------------------------------------------


_HEADING_RE: Final = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_CODE_FENCE_RE: Final = re.compile(r"```[\s\S]*?```", re.MULTILINE)


def _readme_mention_score(
    readme: str,
    *,
    cluster_title: str,
    config: ScoringConfig,
) -> tuple[float, str | None]:
    title = cluster_title.strip()
    if not title or not readme:
        return (0.0, None)
    needle = title.lower()
    haystack = readme.lower()
    if needle not in haystack:
        return (0.0, None)

    # Heading match — strongest
    for heading in _HEADING_RE.findall(readme):
        if needle in heading.lower():
            return (config.readme_heading_match, "readme_heading_match")

    # Code-block / fenced mention — next strongest
    for block in _CODE_FENCE_RE.findall(readme):
        if needle in block.lower():
            return (
                config.readme_code_block_mention,
                "readme_code_block_mention",
            )

    return (config.readme_inline_mention, "readme_inline_mention")


# ---------------------------------------------------------------------------
# Tier mapping & candidate kind inference
# ---------------------------------------------------------------------------


def _score_to_tier(score: float, *, config: ScoringConfig) -> SalienceTier:
    if score >= config.tier_public_min:
        return SalienceTier.PUBLIC
    if score >= config.tier_supporting_min:
        return SalienceTier.SUPPORTING
    if score >= config.tier_internal_min:
        return SalienceTier.INTERNAL
    return SalienceTier.TEST_SCAFFOLDING


def _infer_candidate_kind(
    cluster: _FileCluster,
    symbols: list[PublicSymbol],
    all_paths: list[str],
) -> CandidateKind:
    key = cluster.key
    if key.startswith("cmd:"):
        return CandidateKind.CLI_COMMAND
    if key == "examples":
        return CandidateKind.EXAMPLE
    if key == "docs" or key.startswith("root:README"):
        return CandidateKind.DOCS_TOPIC
    if key.startswith("tests:"):
        return CandidateKind.TEST_SCAFFOLDING
    if key.startswith("internal:"):
        return CandidateKind.MODULE_CLUSTER
    if any(_GENERATED_PATH_RE.search(p) for p in all_paths):
        return CandidateKind.GENERATED_OUTPUT
    if symbols:
        return CandidateKind.PUBLIC_API
    return CandidateKind.MODULE_CLUSTER


# ---------------------------------------------------------------------------
# Repo-kind hint
# ---------------------------------------------------------------------------


def _infer_repo_kind_hint(
    *,
    clusters: list[_FileCluster],
    candidates: list[TopicCandidate],
    repo_context: RepoContext,
) -> RepoKind:
    has_cmd = any(c.key.startswith("cmd:") for c in clusters)
    public_candidates = [
        c for c in candidates if c.salience_tier == SalienceTier.PUBLIC
    ]
    public_count = len(public_candidates)

    has_runtime_service_signal = bool(repo_context.manifests.run_commands) or any(
        c.key.startswith("pkg:") for c in clusters
    )

    multiple_cmd = sum(1 for c in clusters if c.key.startswith("cmd:")) > 1

    if multiple_cmd or public_count >= 6:
        return RepoKind.MONOREPO if multiple_cmd else RepoKind.HYBRID

    if has_cmd and not _has_long_running_signal(repo_context):
        return RepoKind.CLI

    if has_runtime_service_signal and _has_long_running_signal(repo_context):
        return RepoKind.SERVICE

    if not has_cmd and any(
        c.candidate_kind == CandidateKind.PUBLIC_API for c in candidates
    ):
        return RepoKind.LIBRARY

    return RepoKind.UNKNOWN


def _has_long_running_signal(repo_context: RepoContext) -> bool:
    """Heuristic: services typically have HTTP servers, queue
    consumers, or schedulers configured. We approximate by checking
    config keys + dependency names.
    """
    deps = repo_context.manifests.dependencies
    needles = ("fastapi", "fiber", "echo", "gin", "express", "actix")
    if any(any(n in (d.name or "").lower() for n in needles) for d in deps):
        return True
    config_blobs = " ".join(
        (k.key or "") for k in repo_context.manifests.config_keys
    ).lower()
    if any(n in config_blobs for n in ("http_port", "listen_addr", "broker_url")):
        return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ID_SAFE_RE: Final = re.compile(r"[^a-zA-Z0-9_:.-]+")


def _make_id(normalized_key: str) -> str:
    return _ID_SAFE_RE.sub("-", normalized_key).strip("-").lower()


__all__ = (
    "ScoringConfig",
    "build_repo_signals",
)
