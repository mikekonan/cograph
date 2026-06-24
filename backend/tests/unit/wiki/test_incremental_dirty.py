"""Pure-function tests for the incremental-wiki dirty predicate.

No DB, no provider: these tests specify the `incremental.py` API — the
hashes (`spec_hash`, `cited_fingerprint`, `compute_structural_hash`),
the per-page dirty clauses, and the artifact-reuse key.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

from backend.app.models.wiki_artifact import WikiArtifact
from backend.app.wiki.context import (
    FileTreeEntry,
    RepoContext,
    RepoDocIndexEntry,
    compute_structural_hash,
)
from backend.app.wiki.incremental import (
    PageRecord,
    artifact_reusable,
    cited_fingerprint,
    page_cited_reason,
    page_dirty_cheap_reason,
    rehydrate_artifact,
    spec_hash,
)
from backend.app.wiki.manifests import PublicApiEntry, RepoManifests
from backend.app.wiki.schemas import (
    MindMap,
    PagePlan,
    PageKind,
    PageSpec,
    QualityStatus,
    ReaderQuestion,
    RepoOverview,
)
from backend.app.wiki.version import WIKI_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# spec_hash
# ---------------------------------------------------------------------------


def _spec(**overrides: object) -> PageSpec:
    base: dict[str, object] = {
        "slug": "auth",
        "title": "Authentication",
        "parent_slug": "index",
        "purpose": "explain the auth flow",
        "sources_hint": ["auth.py", "tokens.py"],
        "covers_questions": [ReaderQuestion.CONFIGURATION],
        "diagram": False,
        "page_kind": PageKind.CONCEPT,
    }
    base.update(overrides)
    return PageSpec.model_validate(base)


def test_spec_hash_stable_for_identical_specs() -> None:
    assert spec_hash(_spec()) == spec_hash(_spec())


def test_spec_hash_insensitive_to_purpose() -> None:
    # `purpose` is a free-text planner hint regenerated non-deterministically
    # on every re-plan; hashing it made any re-plan dirty every page. A
    # reworded purpose with an unchanged contract + evidence must not dirty.
    assert spec_hash(_spec()) == spec_hash(_spec(purpose="changed purpose"))


def test_spec_hash_insensitive_to_sources_hint() -> None:
    # `sources_hint` is subsumed by `bundle_fingerprint` — the evidence
    # actually retrieved for the page is the authoritative signal, so the
    # planner's hint list is pure noise in the contract hash.
    assert spec_hash(_spec()) == spec_hash(_spec(sources_hint=["other.py"]))


def test_spec_hash_changes_on_covers_questions() -> None:
    assert spec_hash(_spec()) != spec_hash(
        _spec(covers_questions=[ReaderQuestion.HOW_TO_RUN])
    )


def test_spec_hash_changes_on_page_kind_and_diagram() -> None:
    assert spec_hash(_spec()) != spec_hash(_spec(page_kind=PageKind.KEY_FLOW))
    assert spec_hash(_spec()) != spec_hash(_spec(diagram=True))


def test_spec_hash_changes_on_title_and_parent() -> None:
    assert spec_hash(_spec()) != spec_hash(_spec(title="Other title"))
    assert spec_hash(_spec()) != spec_hash(_spec(parent_slug=None))


def test_spec_hash_insensitive_to_planner_metadata() -> None:
    # Planner-only telemetry never reaches the page contract.
    tagged = _spec(facet_tags=["x", "y"], salience_tier="public")
    assert spec_hash(_spec()) == spec_hash(tagged)


def _load_migration_0062() -> object:
    # versions/ is not a package (no __init__.py) and the module name starts
    # with a digit — load it by file path.
    import backend

    path = (
        Path(backend.__file__).parent
        / "app/db/migrations/versions/0062_backfill_spec_hash.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0062", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0062_spec_hash_byte_identical() -> None:
    """0062 inlines a frozen copy of spec_hash — it cannot import
    incremental (that drags retrieval/embedder deps into the Alembic env).
    This pins the copy byte-for-byte against the live function: if spec_hash
    changes, this goes red and the change must ship its own backfill."""
    migration = _load_migration_0062()
    edge_specs = [
        _spec(),
        _spec(parent_slug=None),
        _spec(covers_questions=[]),
        _spec(
            covers_questions=[
                ReaderQuestion.PUBLIC_API,
                ReaderQuestion.HOW_TO_RUN,
                ReaderQuestion.CONFIGURATION,
            ]
        ),
        _spec(diagram=True),
        _spec(page_kind=PageKind.KEY_FLOW),
        _spec(slug="weird/slug:with-chars", title='Tïtlé — 日本語 "q"'),
        # purpose / sources_hint differ but must NOT move the hash now:
        _spec(purpose="totally different", sources_hint=["z.py", "a.py"]),
    ]
    for spec in edge_specs:
        assert migration._spec_hash(spec) == spec_hash(spec)  # type: ignore[attr-defined]


def test_migration_0062_backfill_updates_only_planned_slugs() -> None:
    """Backfill plumbing on sqlite: upgrade rewrites spec_hash for every
    document whose slug is in the repo plan, leaves orphan rows (no plan
    entry) untouched, and downgrade restores the legacy formula. Exercises
    the JSONB-as-TEXT read path and the (repository_id, slug) WHERE clause."""
    import json as _json

    import sqlalchemy as _sa
    from sqlalchemy.pool import StaticPool

    migration = _load_migration_0062()
    specs = [
        _spec(slug="index", title="Index", parent_slug=None),
        _spec(
            slug="auth",
            title="Authentication",
            covers_questions=[
                ReaderQuestion.CONFIGURATION,
                ReaderQuestion.PUBLIC_API,
            ],
            diagram=True,
        ),
        _spec(slug="api", title="API", page_kind=PageKind.KEY_FLOW),
    ]
    specs_by_slug = {s.slug: s for s in specs}
    plan_payload = {"pages": [s.model_dump(mode="json") for s in specs]}

    engine = _sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with engine.begin() as conn:
            conn.execute(
                _sa.text("CREATE TABLE wiki_artifacts (repository_id TEXT, plan TEXT)")
            )
            conn.execute(
                _sa.text(
                    "CREATE TABLE documents "
                    "(repository_id TEXT, slug TEXT, spec_hash TEXT)"
                )
            )
            conn.execute(
                _sa.text("INSERT INTO wiki_artifacts VALUES (:r, :p)"),
                {"r": "repo1", "p": _json.dumps(plan_payload)},
            )
            for s in specs:
                conn.execute(
                    _sa.text("INSERT INTO documents VALUES (:r, :s, 'STALE')"),
                    {"r": "repo1", "s": s.slug},
                )
            # Orphan: slug absent from the plan — must stay untouched.
            conn.execute(
                _sa.text("INSERT INTO documents VALUES ('repo1', 'orphan', 'STALE')")
            )

        with engine.begin() as conn:
            migration._rebackfill(migration._spec_hash, bind=conn)  # type: ignore[attr-defined]
            up = dict(
                conn.execute(_sa.text("SELECT slug, spec_hash FROM documents")).all()
            )
        for slug, spec in specs_by_slug.items():
            assert up[slug] == spec_hash(spec)
        assert up["orphan"] == "STALE"

        with engine.begin() as conn:
            migration._rebackfill(migration._spec_hash_legacy, bind=conn)  # type: ignore[attr-defined]
            down = dict(
                conn.execute(_sa.text("SELECT slug, spec_hash FROM documents")).all()
            )
        for slug, spec in specs_by_slug.items():
            assert down[slug] == migration._spec_hash_legacy(spec)  # type: ignore[attr-defined]
            assert down[slug] != spec_hash(spec)
        assert down["orphan"] == "STALE"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# cited_fingerprint  (P1: retrieval-free, cited-only)
# ---------------------------------------------------------------------------


def _cited(
    node_ids,
    chunk_ids=(),
    *,
    contents=None,
    summaries=None,
    chunk_texts=None,
) -> str:
    """cited_fingerprint over plain id lists + DB-shaped component maps."""
    return cited_fingerprint(
        cited_node_ids=list(node_ids),
        cited_chunk_ids=list(chunk_ids),
        node_content_hashes=contents or {},
        node_summaries=summaries or {},
        chunk_contents=chunk_texts or {},
    )


def test_cited_fingerprint_blind_to_uncited_churn() -> None:
    """The $64 root-cause fix.

    The fingerprint reads ONLY the cited ids out of the component maps, so
    churn in *uncited* evidence — the extra top-k members the old
    whole-bundle hash folded in, whose ANN rank jittered on every push —
    cannot move it. Here a page cites {A, B}; an uncited C changes content
    between pushes and is then replaced by a D. The cited fingerprint is
    identical across all of it.
    """
    a, b = str(uuid4()), str(uuid4())
    c, d = str(uuid4()), str(uuid4())
    contents = {a: "ha", b: "hb"}
    summaries = {a: "sa", b: "sb"}
    base = _cited([a, b], contents=contents, summaries=summaries)
    with_c1 = _cited(
        [a, b], contents={**contents, c: "hc1"}, summaries={**summaries, c: "sc"}
    )
    with_c2 = _cited(
        [a, b], contents={**contents, c: "hc2"}, summaries={**summaries, c: "sc"}
    )
    with_d = _cited(
        [a, b], contents={**contents, d: "hd"}, summaries={**summaries, d: "sd"}
    )
    assert base == with_c1 == with_c2 == with_d


def test_cited_fingerprint_changes_on_cited_content() -> None:
    a = str(uuid4())
    before = _cited([a], contents={a: "h1"}, summaries={a: "s"})
    after = _cited([a], contents={a: "h2"}, summaries={a: "s"})
    assert before != after


def test_cited_fingerprint_changes_on_cited_summary() -> None:
    """A cited node's summary regenerates on a neighbor change while its body
    (content_hash) and UUID survive — the fingerprint must still move."""
    a = str(uuid4())
    before = _cited([a], contents={a: "h"}, summaries={a: "old summary"})
    after = _cited([a], contents={a: "h"}, summaries={a: "new summary"})
    assert before != after


def test_cited_fingerprint_changes_on_cited_chunk_content() -> None:
    c = str(uuid4())
    before = _cited([], [c], chunk_texts={c: "## Setup v1"})
    after = _cited([], [c], chunk_texts={c: "## Setup v2"})
    assert before != after


def test_cited_fingerprint_order_independent() -> None:
    """Set-keyed: citation order (ANN rank jitter) must not move it."""
    a, b = str(uuid4()), str(uuid4())
    contents = {a: "ha", b: "hb"}
    assert _cited([a, b], contents=contents) == _cited([b, a], contents=contents)


def test_cited_fingerprint_missing_inputs_are_deterministic() -> None:
    """A cited id with no live content/summary contributes empty components
    (liveness is caught upstream); the hash stays deterministic and still
    differs from the populated case."""
    a = str(uuid4())
    assert _cited([a]) == _cited([a])
    assert _cited([a]) != _cited([a], contents={a: "h"})


# ---------------------------------------------------------------------------
# page_dirty_cheap_reason / page_fingerprint_reason
# ---------------------------------------------------------------------------


_NODE_A = str(uuid4())
_CHUNK_A = str(uuid4())


def _record(**overrides: object) -> PageRecord:
    base: dict[str, object] = {
        "slug": "auth",
        "spec_hash": "spec-1",
        "wiki_schema_version": WIKI_SCHEMA_VERSION,
        "source_node_ids": (_NODE_A,),
        "source_repo_doc_chunk_ids": (_CHUNK_A,),
        "quality_status": QualityStatus.OK,
    }
    base.update(overrides)
    return PageRecord(**base)  # type: ignore[arg-type]


def _cheap(record: PageRecord | None, **overrides: object) -> str | None:
    kwargs: dict[str, object] = {
        "record": record,
        "current_spec_hash": "spec-1",
        "live_node_ids": {_NODE_A},
        "live_chunk_ids": {_CHUNK_A},
    }
    kwargs.update(overrides)
    return page_dirty_cheap_reason(**kwargs)  # type: ignore[arg-type]


def test_clean_when_nothing_changed() -> None:
    assert _cheap(_record()) is None


def test_dirty_when_row_missing() -> None:
    assert _cheap(None) == "missing_row"


def test_dirty_on_schema_version_mismatch() -> None:
    assert _cheap(_record(wiki_schema_version=WIKI_SCHEMA_VERSION - 1)) == (
        "schema_version"
    )
    # Legacy rows (no stamp at all) are dirty too.
    assert _cheap(_record(wiki_schema_version=None)) == "schema_version"


def test_dirty_on_spec_change() -> None:
    assert _cheap(_record(), current_spec_hash="spec-2") == "spec_changed"


def test_dirty_on_degraded_quality() -> None:
    assert _cheap(_record(quality_status=QualityStatus.DEGRADED)) == (
        "quality_degraded"
    )


def test_dirty_on_unknown_quality() -> None:
    assert _cheap(_record(quality_status=None)) == "quality_unknown"


def test_partial_quality_is_clean() -> None:
    """Partial pages legitimately ship (a reader question may not be
    answerable from the repo); retrying them every sync burns the savings."""
    assert _cheap(_record(quality_status=QualityStatus.PARTIAL)) is None


def test_dirty_when_cited_node_missing() -> None:
    assert _cheap(_record(), live_node_ids=set()) == "cited_node_missing"


def test_dirty_when_cited_chunk_missing() -> None:
    assert _cheap(_record(), live_chunk_ids=set()) == "cited_chunk_missing"


# ---------------------------------------------------------------------------
# page_cited_reason  (P1: cited-only dirty clause, NULL → adopt)
# ---------------------------------------------------------------------------


def test_cited_reason_clean_when_fingerprint_matches() -> None:
    record = _record(cited_fingerprint="cf-1")
    assert page_cited_reason(record=record, current_cited_fingerprint="cf-1") is None


def test_cited_reason_dirty_when_cited_evidence_changed() -> None:
    record = _record(cited_fingerprint="cf-1")
    assert (
        page_cited_reason(record=record, current_cited_fingerprint="cf-2")
        == "cited_evidence_changed"
    )


def test_cited_reason_null_stamp_adopts_not_dirty() -> None:
    """The deploy-safety floor: a None stored stamp ADOPTS, never dirties.

    Pages written before the cited-fingerprint column existed land with a
    NULL stamp. `page_cited_reason` returns None for them regardless of the
    freshly computed value — so the runtime stamps it and the page stays
    clean, never a rewrite. This is what keeps a deploy (or a skipped
    backfill) from triggering a regeneration storm.
    """
    record = _record(cited_fingerprint=None)
    assert page_cited_reason(record=record, current_cited_fingerprint="cf-1") is None
    assert (
        page_cited_reason(record=record, current_cited_fingerprint="anything") is None
    )


# ---------------------------------------------------------------------------
# compute_structural_hash
# ---------------------------------------------------------------------------


def _context(**overrides: object) -> RepoContext:
    base: dict[str, object] = {
        "repository_id": uuid4(),
        "commit_sha": "commit-1",
        "readme_text": "# Repo",
        "file_tree": [
            FileTreeEntry(
                file_path="pkg/f.py", language="python", bytes=100, importance=1.0
            )
        ],
        "repo_doc_index": [
            RepoDocIndexEntry(file_path="README.md", title="Repo", first_heading="Repo")
        ],
        "manifests": RepoManifests(
            public_api=[
                PublicApiEntry(
                    qualified_name="pkg.f",
                    kind="function",
                    file_path="pkg/f.py",
                    start_line=10,
                    end_line=20,
                )
            ]
        ),
        "file_tree_hash": "x",
        "docs_hash": "x",
        "summaries_hash": "x",
        "identity_hash": "x",
    }
    base.update(overrides)
    return RepoContext.model_validate(base)


def test_structural_hash_excludes_commit_sha() -> None:
    repo_id = uuid4()
    a = _context(repository_id=repo_id, commit_sha="commit-1")
    b = _context(repository_id=repo_id, commit_sha="commit-2")
    assert compute_structural_hash(a) == compute_structural_hash(b)


def test_structural_hash_excludes_bytes_and_importance() -> None:
    repo_id = uuid4()
    a = _context(repository_id=repo_id)
    b = _context(
        repository_id=repo_id,
        file_tree=[
            FileTreeEntry(
                file_path="pkg/f.py", language="python", bytes=999, importance=0.1
            )
        ],
    )
    assert compute_structural_hash(a) == compute_structural_hash(b)


def test_structural_hash_excludes_manifest_line_numbers() -> None:
    repo_id = uuid4()
    a = _context(repository_id=repo_id)
    b = _context(
        repository_id=repo_id,
        manifests=RepoManifests(
            public_api=[
                PublicApiEntry(
                    qualified_name="pkg.f",
                    kind="function",
                    file_path="pkg/f.py",
                    start_line=42,
                    end_line=52,
                )
            ]
        ),
    )
    assert compute_structural_hash(a) == compute_structural_hash(b)


def test_structural_hash_changes_on_new_file() -> None:
    repo_id = uuid4()
    a = _context(repository_id=repo_id)
    b = _context(
        repository_id=repo_id,
        file_tree=[
            FileTreeEntry(
                file_path="pkg/f.py", language="python", bytes=100, importance=1.0
            ),
            FileTreeEntry(
                file_path="pkg/g.py", language="python", bytes=10, importance=0.0
            ),
        ],
    )
    assert compute_structural_hash(a) != compute_structural_hash(b)


def test_structural_hash_changes_on_readme_and_public_api() -> None:
    repo_id = uuid4()
    a = _context(repository_id=repo_id)
    assert compute_structural_hash(a) != compute_structural_hash(
        _context(repository_id=repo_id, readme_text="# Rewritten")
    )
    assert compute_structural_hash(a) != compute_structural_hash(
        _context(
            repository_id=repo_id,
            manifests=RepoManifests(
                public_api=[
                    PublicApiEntry(
                        qualified_name="pkg.g", kind="function", file_path="pkg/g.py"
                    )
                ]
            ),
        )
    )


# ---------------------------------------------------------------------------
# artifact_reusable / rehydrate_artifact
# ---------------------------------------------------------------------------


def _artifact(**overrides: object) -> WikiArtifact:
    base: dict[str, object] = {
        "repository_id": uuid4(),
        "wiki_schema_version": WIKI_SCHEMA_VERSION,
        "structural_hash": "struct-1",
        "plan_hash": "plan-1",
        "chat_model": "gpt-test",
        "embed_model": "fake-embed-v1",
        "overview": RepoOverview(one_line="x", long_description="y").model_dump(
            mode="json"
        ),
        "mindmap": MindMap().model_dump(mode="json"),
        "plan": PagePlan(pages=[_spec(slug="index", parent_slug=None)]).model_dump(
            mode="json"
        ),
    }
    base.update(overrides)
    return WikiArtifact(**base)  # type: ignore[arg-type]


_REUSE_KEY = {
    "structural_hash": "struct-1",
    "chat_model": "gpt-test",
    "embed_model": "fake-embed-v1",
}


def test_artifact_reusable_happy_path() -> None:
    assert artifact_reusable(_artifact(), **_REUSE_KEY)


def test_artifact_not_reusable_when_missing() -> None:
    assert not artifact_reusable(None, **_REUSE_KEY)


def test_artifact_not_reusable_on_key_mismatch() -> None:
    assert not artifact_reusable(
        _artifact(wiki_schema_version=WIKI_SCHEMA_VERSION - 1), **_REUSE_KEY
    )
    assert not artifact_reusable(_artifact(structural_hash="struct-2"), **_REUSE_KEY)
    assert not artifact_reusable(_artifact(chat_model="other"), **_REUSE_KEY)
    assert not artifact_reusable(_artifact(embed_model="fake-embed-v2"), **_REUSE_KEY)


def test_rehydrate_artifact_round_trips() -> None:
    rehydrated = rehydrate_artifact(_artifact())
    assert rehydrated is not None
    assert rehydrated.overview.one_line == "x"
    assert [p.slug for p in rehydrated.plan.pages] == ["index"]


def test_rehydrate_artifact_corrupt_payload_returns_none() -> None:
    corrupt = _artifact(plan={"pages": [{"not_a_spec": True}]})
    assert rehydrate_artifact(corrupt) is None
