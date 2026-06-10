"""Forgotten-bump guard for `WIKI_SCHEMA_VERSION`.

The incremental wiki path reuses persisted artifacts and skips clean
pages based on the schema version. If the quality surface — system
prompts, agent/gate budgets, reuse-hash algorithms — changes while the
version stays put, stale artifacts pass `artifact_reusable` and pages
written by the *old* pipeline are served as if the *new* one produced
them. These tests make that mistake a red build instead of a silent
quality drift.

When a surface change is intentional:

1. Bump `WIKI_SCHEMA_VERSION` in `backend/app/wiki/version.py`.
2. Append the new sha to `SURFACE_SHA_HISTORY` (never edit old entries):

    python -c "from backend.app.wiki.version import \
        compute_quality_surface_sha as f; print(f())"
"""

from __future__ import annotations

from backend.app.wiki.version import (
    SURFACE_SHA_HISTORY,
    WIKI_SCHEMA_VERSION,
    compute_quality_surface_sha,
)


def test_history_has_exactly_one_entry_per_version() -> None:
    """Contiguous 1..current: a bumped version without its history entry
    (or a history entry without a bump) is caught here."""
    assert set(SURFACE_SHA_HISTORY) == set(range(1, WIKI_SCHEMA_VERSION + 1))


def test_quality_surface_sha_matches_current_version_entry() -> None:
    actual = compute_quality_surface_sha()
    expected = SURFACE_SHA_HISTORY[WIKI_SCHEMA_VERSION]
    assert actual == expected, (
        "The wiki quality surface (system prompts, gate budgets, or "
        "reuse-hash algorithms) changed but WIKI_SCHEMA_VERSION did not. "
        "Persisted artifacts from the old pipeline would be reused as if "
        "nothing changed. Bump WIKI_SCHEMA_VERSION and append "
        f'{{<new version>: "{actual}"}} to SURFACE_SHA_HISTORY in '
        "backend/app/wiki/version.py (do not edit existing entries)."
    )


def test_surface_sha_is_stable_within_a_process() -> None:
    """The fingerprint must be a pure function — no timestamps, dict
    ordering, or hash randomization leaking in."""
    assert compute_quality_surface_sha() == compute_quality_surface_sha()
