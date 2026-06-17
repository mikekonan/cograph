"""Hand-bumped wiki pipeline schema version + quality-surface fingerprint.

`WIKI_SCHEMA_VERSION` is the invalidation lever for everything the
incremental wiki path persists and reuses: the `wiki_artifacts` row
(overview / mindmap / plan) and the per-page `documents` stamps
(`spec_hash`, `retrieval_fingerprint`, `wiki_schema_version`). A version
mismatch means "the pipeline that produced the cached artifacts is not
the pipeline running now" — the run falls back to a full rebuild.

Bump the constant whenever a change affects what the LLM would produce
for the SAME repo state, e.g.:

- any system prompt or user-prompt builder in `prompts.py`
- writer agent-loop semantics (turn budgets, gates, repair flows)
- the spec-hash / bundle-fingerprint algorithms in `incremental.py`
- plan normalization rules in `pipeline.py`

Pure refactors, logging, and telemetry changes do NOT need a bump.

Reuse-key *narrowing* is a deliberate exception. A `spec_hash` /
`bundle_fingerprint` change that only drops fields — making strictly more
pages eligible for reuse, never fewer — does NOT change what the LLM
produces for a given repo state; it only improves reuse. Bumping in that
case would be self-defeating: a version mismatch forces a full rebuild of
every repo, the exact cost we are cutting. The condition that makes this
safe is a *backfill*: existing `documents.spec_hash` stamps must be
rewritten to the new formula in the same release (see migration 0062), so
no row is ever compared old-formula-against-new and spuriously dirtied.
For such a change, edit `SURFACE_SHA_HISTORY[current]` IN PLACE (not
append), keep `WIKI_SCHEMA_VERSION`, and carry `[wiki-schema-no-bump]` in
the commit.

Adding a NEW regeneration path is the other no-bump exception. The cheap
edit pass (`PAGE_EDITOR_SYSTEM`, `_edit_one` in `pipeline.py`) only ever
rewrites pages the dirty predicate already flagged for rewrite this run;
it never touches a clean cached page, and the full write path that
produced every v1 page is unchanged. So no persisted artifact is
invalidated — a bump would force the full rebuild we are trying to avoid.
Such an addition still moves the surface SHA (a new prompt is part of the
surface), so it too edits `SURFACE_SHA_HISTORY[current]` in place with
`[wiki-schema-no-bump]`.

Two guards enforce this:

1. `SURFACE_SHA_HISTORY` + the unit test in
   `backend/tests/unit/wiki/test_schema_version_guard.py` — recomputes
   `compute_quality_surface_sha()` and compares against the entry for the
   current version. Changing a prompt, a gate budget, or a reuse-hash
   algorithm without bumping the version turns the test red.
2. `scripts/check_wiki_schema_version.sh` (CI) — a PR diff that touches
   the quality-surface modules must either bump `WIKI_SCHEMA_VERSION` or
   carry `[wiki-schema-no-bump]` in a commit message.

When bumping, append the new entry (never edit existing ones); the sole
exception is the reuse-key-narrowing carve-out above, which edits the
current entry in place. Recompute with:

    python -c "from backend.app.wiki.version import \
        compute_quality_surface_sha as f; print(f())"
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import textwrap
from collections.abc import Callable

WIKI_SCHEMA_VERSION = 1

# version -> sha256 of the canonical quality surface at the moment that
# version shipped. Append-only history: each bump adds one entry.
SURFACE_SHA_HISTORY: dict[int, str] = {
    # Edited in place (not appended) twice within version 1, both no-bump
    # changes (see the docstring carve-outs): (1) the spec_hash reuse-key
    # narrowing (drop purpose / sources_hint), paired with a backfill; and
    # (2) adding PAGE_EDITOR_SYSTEM for the cheap edit path — a new regen
    # path that never invalidates a v1-written page (the write path is
    # unchanged), so a bump would force a needless full rebuild of every repo.
    1: "783cc6ff01df0b45f491a415f85eb8d32be355d241ac6baa2be95b1d54a04ab7",
}


def _normalized_source(fn: Callable[..., object]) -> str:
    """AST-normalized function source.

    Formatting and comments drop out of the AST and docstrings are
    stripped explicitly, so only semantic code changes move the
    fingerprint — a comment edit or `ruff format` run must not demand a
    schema bump.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def compute_quality_surface_sha() -> str:
    """Sha256 over everything that decides what the LLM produces for a
    given repo state: the system prompts, the agent/gate budgets, and the
    reuse-hash algorithms whose output is persisted and compared across
    runs. Imports are lazy — `incremental` imports this module.
    """
    from backend.app.wiki import context, incremental, pipeline, prompts

    surface = {
        "prompts": {
            "mindmap_generator_system": prompts.MINDMAP_GENERATOR_SYSTEM,
            "repo_analyzer_system": prompts.REPO_ANALYZER_SYSTEM,
            "page_planner_system": prompts.PAGE_PLANNER_SYSTEM,
            "page_writer_system": prompts.PAGE_WRITER_SYSTEM,
            "page_editor_system": prompts.PAGE_EDITOR_SYSTEM,
            "page_outline_system": prompts.PAGE_OUTLINE_SYSTEM,
            "page_prose_system": prompts.PAGE_PROSE_SYSTEM,
            "diagram_synthesizer_system": prompts.DIAGRAM_SYNTHESIZER_SYSTEM,
            "cross_linker_system": prompts.CROSS_LINKER_SYSTEM,
        },
        "budgets": {
            "agent_max_turns": pipeline._AGENT_MAX_TURNS,
            "agent_max_input_chars": pipeline._AGENT_MAX_INPUT_CHARS,
            "writer_empty_body_max_retries": pipeline._WRITER_EMPTY_BODY_MAX_RETRIES,
            "citation_gate_max_repairs": pipeline._CITATION_GATE_MAX_REPAIRS,
            "coverage_gate_max_repairs": pipeline._COVERAGE_GATE_MAX_REPAIRS,
            "repair_max_turns": pipeline._REPAIR_MAX_TURNS,
            "repair_max_input_chars": pipeline._REPAIR_MAX_INPUT_CHARS,
            "outline_pass_max_attempts": pipeline._OUTLINE_PASS_MAX_ATTEMPTS,
        },
        "hash_algorithms": {
            "canonical_hash": _normalized_source(incremental._canonical_hash),
            "spec_hash": _normalized_source(incremental.spec_hash),
            "bundle_fingerprint": _normalized_source(incremental.bundle_fingerprint),
            "structural_hash": _normalized_source(context.compute_structural_hash),
        },
    }
    return hashlib.sha256(
        json.dumps(surface, sort_keys=True).encode("utf-8")
    ).hexdigest()
