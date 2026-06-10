"""Hand-bumped wiki pipeline schema version.

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
CI enforces this via `scripts/check_wiki_schema_version.sh`.
"""

from __future__ import annotations

WIKI_SCHEMA_VERSION = 1
