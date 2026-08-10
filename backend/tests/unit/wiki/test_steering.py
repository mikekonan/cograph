"""Tests for `.cograph/wiki.json` steering — parsing, caps, planner bypass,
and the writer-side `<page_hints>` surface."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from backend.app.wiki.prompts import (
    build_page_planner_user,
    build_page_writer_user,
)
from backend.app.wiki.retrieval import PageBundle
from backend.app.wiki.schemas import (
    PageSpec,
    ReaderQuestion,
    RepoOverview,
)
from backend.app.wiki.steering import (
    PageHint,
    RepoNote,
    WikiSteering,
    load_wiki_steering,
)


# ---------------------------------------------------------------------------
# Loader — JSON / YAML / absent / invalid
# ---------------------------------------------------------------------------


def _write_steering(checkout: Path, basename: str, payload: object) -> None:
    cograph_dir = checkout / ".cograph"
    cograph_dir.mkdir(parents=True, exist_ok=True)
    path = cograph_dir / basename
    if basename.endswith((".yaml", ".yml")):
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_returns_none_when_checkout_path_is_none() -> None:
    assert load_wiki_steering(None) is None


def test_load_returns_none_when_directory_missing(tmp_path: Path) -> None:
    # No `.cograph` directory at all.
    assert load_wiki_steering(tmp_path) is None


def test_load_returns_none_when_file_missing(tmp_path: Path) -> None:
    (tmp_path / ".cograph").mkdir()
    assert load_wiki_steering(tmp_path) is None


def test_load_parses_minimal_json_file(tmp_path: Path) -> None:
    _write_steering(
        tmp_path,
        "wiki.json",
        {
            "repo_notes": [
                {"content": "this is a port of go-oas3", "author": "mike"}
            ]
        },
    )
    steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert len(steering.repo_notes) == 1
    assert steering.repo_notes[0].content == "this is a port of go-oas3"
    assert steering.repo_notes[0].author == "mike"
    assert steering.pages is None


def test_load_parses_yaml_file(tmp_path: Path) -> None:
    _write_steering(
        tmp_path,
        "wiki.yaml",
        {
            "repo_notes": [{"content": "the auth module is being rewritten"}],
            "pages": [
                {
                    "title": "Overview",
                    "purpose": "what this repo is",
                    "page_notes": ["focus on the producer side"],
                }
            ],
        },
    )
    steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert steering.repo_notes[0].content == "the auth module is being rewritten"
    assert steering.pages is not None
    assert len(steering.pages) == 1
    assert steering.pages[0].title == "Overview"
    assert steering.pages[0].page_notes == ["focus on the producer side"]


def test_load_prefers_json_over_yaml_when_both_exist(tmp_path: Path) -> None:
    _write_steering(
        tmp_path, "wiki.json", {"repo_notes": [{"content": "from json"}]}
    )
    _write_steering(
        tmp_path, "wiki.yaml", {"repo_notes": [{"content": "from yaml"}]}
    )
    steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert steering.repo_notes[0].content == "from json"


def test_load_returns_none_for_top_level_array(
    tmp_path: Path, caplog
) -> None:
    cograph = tmp_path / ".cograph"
    cograph.mkdir()
    (cograph / "wiki.json").write_text("[1, 2, 3]")
    with caplog.at_level(logging.WARNING):
        assert load_wiki_steering(tmp_path) is None
    assert any("top-level must be an object" in m for m in caplog.messages)


def test_load_returns_none_for_unparseable_file(
    tmp_path: Path, caplog
) -> None:
    cograph = tmp_path / ".cograph"
    cograph.mkdir()
    (cograph / "wiki.json").write_text("{ this is broken: yaml: too:")
    with caplog.at_level(logging.WARNING):
        result = load_wiki_steering(tmp_path)
    assert result is None


def test_load_truncates_oversized_note_content(tmp_path: Path) -> None:
    huge = "x" * 50_000
    _write_steering(tmp_path, "wiki.json", {"repo_notes": [{"content": huge}]})
    steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert len(steering.repo_notes[0].content) == 10_000


def test_load_caps_repo_notes_count(tmp_path: Path, caplog) -> None:
    payload = {"repo_notes": [{"content": f"note {i}"} for i in range(150)]}
    _write_steering(tmp_path, "wiki.json", payload)
    with caplog.at_level(logging.WARNING):
        steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert len(steering.repo_notes) == 100
    assert any("over the 100 cap" in m for m in caplog.messages)


def test_load_caps_pages_count(tmp_path: Path, caplog) -> None:
    payload = {
        "pages": [
            {"title": f"Page {i}", "purpose": "p"} for i in range(40)
        ]
    }
    _write_steering(tmp_path, "wiki.json", payload)
    with caplog.at_level(logging.WARNING):
        steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert steering.pages is not None
    assert len(steering.pages) == 30
    assert any("over the 30 cap" in m for m in caplog.messages)


def test_load_drops_duplicate_titles(tmp_path: Path, caplog) -> None:
    payload = {
        "pages": [
            {"title": "Overview", "purpose": "first"},
            {"title": "Overview", "purpose": "duplicate"},
            {"title": "Architecture", "purpose": "third"},
        ]
    }
    _write_steering(tmp_path, "wiki.json", payload)
    with caplog.at_level(logging.WARNING):
        steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert steering.pages is not None
    titles = [p.title for p in steering.pages]
    assert titles == ["Overview", "Architecture"]
    assert any("duplicate title" in m for m in caplog.messages)


def test_load_clears_unknown_parent(tmp_path: Path, caplog) -> None:
    payload = {
        "pages": [
            {"title": "Index", "purpose": "root"},
            {
                "title": "Child",
                "purpose": "child",
                "parent": "Nonexistent",
            },
        ]
    }
    _write_steering(tmp_path, "wiki.json", payload)
    with caplog.at_level(logging.WARNING):
        steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert steering.pages is not None
    child = next(p for p in steering.pages if p.title == "Child")
    assert child.parent is None
    assert any("unknown parent" in m for m in caplog.messages)


def test_load_clears_self_parent(tmp_path: Path, caplog) -> None:
    payload = {
        "pages": [
            {"title": "Loopy", "purpose": "self", "parent": "Loopy"},
        ]
    }
    _write_steering(tmp_path, "wiki.json", payload)
    with caplog.at_level(logging.WARNING):
        steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert steering.pages is not None
    assert steering.pages[0].parent is None
    assert any("its own parent" in m for m in caplog.messages)


def test_load_rejects_two_level_parent_chain(tmp_path: Path, caplog) -> None:
    payload = {
        "pages": [
            {"title": "Root", "purpose": "root"},
            {"title": "Mid", "purpose": "mid", "parent": "Root"},
            {"title": "Leaf", "purpose": "leaf", "parent": "Mid"},
        ]
    }
    _write_steering(tmp_path, "wiki.json", payload)
    with caplog.at_level(logging.WARNING):
        steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert steering.pages is not None
    leaf = next(p for p in steering.pages if p.title == "Leaf")
    assert leaf.parent is None
    assert any("2-level parent chain" in m for m in caplog.messages)


def test_load_caps_page_notes_per_page(tmp_path: Path) -> None:
    notes = [f"hint {i}" for i in range(20)]
    payload = {
        "pages": [
            {"title": "X", "purpose": "p", "page_notes": notes},
        ]
    }
    _write_steering(tmp_path, "wiki.json", payload)
    steering = load_wiki_steering(tmp_path)
    assert steering is not None
    assert steering.pages is not None
    assert len(steering.pages[0].page_notes) == 10


def test_load_returns_none_pages_when_all_invalid(
    tmp_path: Path, caplog
) -> None:
    payload = {"pages": [{"title": "", "purpose": "empty title"}]}
    _write_steering(tmp_path, "wiki.json", payload)
    with caplog.at_level(logging.WARNING):
        steering = load_wiki_steering(tmp_path)
    assert steering is not None
    # Empty-title pages get pruned; with no survivors the loader falls back
    # to LLM planning by reporting `pages=None`.
    assert steering.pages is None


# ---------------------------------------------------------------------------
# Planner-side surface — `<repo_notes>` block rendering
# ---------------------------------------------------------------------------


def _empty_overview() -> RepoOverview:
    return RepoOverview(one_line="o", long_description="d")


def _make_context_stub(steering: WikiSteering | None):
    """Lightweight stand-in for `RepoContext` — only the attributes the
    planner-user builder reads."""

    class _Ctx:
        previous_run_slugs: list[str] = []

    return _Ctx()


def test_planner_user_renders_repo_notes_when_present() -> None:
    steering = WikiSteering(
        repo_notes=[
            RepoNote(content="port of go-oas3", author="mike"),
            RepoNote(content="ignore the legacy adapter"),
        ]
    )
    body = build_page_planner_user(
        context=_make_context_stub(None),
        overview=_empty_overview(),
        clusters=[],
        steering=steering,
    )
    assert "<repo_notes>" in body
    assert "port of go-oas3" in body
    assert "— mike" in body
    assert "ignore the legacy adapter" in body


def test_planner_user_renders_repo_notes_placeholder_when_absent() -> None:
    body = build_page_planner_user(
        context=_make_context_stub(None),
        overview=_empty_overview(),
        clusters=[],
        steering=None,
    )
    assert "<repo_notes>" in body
    assert "no repo notes" in body


def test_planner_system_documents_repo_notes() -> None:
    from backend.app.wiki.prompts import PAGE_PLANNER_SYSTEM

    assert "<repo_notes>" in PAGE_PLANNER_SYSTEM
    assert "USER STEERING" in PAGE_PLANNER_SYSTEM


# ---------------------------------------------------------------------------
# Planner bypass — `plan_pages` skips the LLM when steering.pages is set
# ---------------------------------------------------------------------------


async def test_plan_pages_bypasses_llm_when_steering_pages_set() -> None:
    from backend.app.wiki.context import RepoContext
    from backend.app.wiki.pipeline import (
        WikiGenerationConfig,
        plan_pages,
    )

    class _NoopProvider:
        model = "fake"

        async def complete_text(self, **_: object) -> str:  # pragma: no cover
            raise AssertionError("LLM must NOT be called when steering bypasses")

        async def complete_json(self, **_: object) -> object:  # pragma: no cover
            raise AssertionError("LLM must NOT be called when steering bypasses")

    steering = WikiSteering(
        pages=[
            PageHint(title="Index", purpose="landing page"),
            PageHint(title="Architecture", purpose="components", parent=None),
            PageHint(title="API", purpose="public surface"),
            PageHint(title="Handlers", purpose="HTTP handlers", parent="API"),
        ]
    )
    context = RepoContext(
        repository_id="00000000-0000-0000-0000-000000000001",
        commit_sha="abc",
        file_tree_hash="ft",
        docs_hash="dh",
        summaries_hash="sh",
        identity_hash="ih",
        steering=steering,
    )
    plan = await plan_pages(
        llm=_NoopProvider(),
        context=context,
        overview=_empty_overview(),
        config=WikiGenerationConfig(),
        clusters=None,
    )
    slugs = [p.slug for p in plan.pages]
    # _normalize_plan promotes the first page to slug `index`.
    assert slugs[0] == "index"
    assert "architecture" in slugs
    assert "api" in slugs
    assert "handlers" in slugs
    handlers = next(p for p in plan.pages if p.slug == "handlers")
    # The flat 2-level wiki contract collapses any non-index page to
    # parent_slug=`index`, even when steering nominated an intermediate
    # parent. The page itself survives — only its position in the tree
    # changes.
    assert handlers.parent_slug == "index"


# ---------------------------------------------------------------------------
# Writer-side surface — `<page_hints>` block rendering
# ---------------------------------------------------------------------------


def _spec(slug: str = "x") -> PageSpec:
    return PageSpec(
        slug=slug,
        title="X",
        purpose="p",
        covers_questions=[ReaderQuestion.PUBLIC_API],
    )


def test_writer_user_renders_page_hints_when_provided() -> None:
    body = build_page_writer_user(
        spec=_spec(),
        overview=_empty_overview(),
        bundle=PageBundle(),
        sibling_pages=[],
        page_notes=["focus on the producer side", "skip the legacy adapter"],
    )
    assert "<page_hints>" in body
    assert "focus on the producer side" in body
    assert "skip the legacy adapter" in body


def test_writer_user_renders_page_hints_placeholder_when_absent() -> None:
    body = build_page_writer_user(
        spec=_spec(),
        overview=_empty_overview(),
        bundle=PageBundle(),
        sibling_pages=[],
        page_notes=None,
    )
    assert "<page_hints>" in body
    assert "no user-supplied hints" in body


def test_writer_system_documents_page_hints() -> None:
    from backend.app.wiki.prompts import PAGE_WRITER_SYSTEM

    assert "<page_hints>" in PAGE_WRITER_SYSTEM
    assert "authoritative" in PAGE_WRITER_SYSTEM


# ---------------------------------------------------------------------------
# Pipeline plumbing — `_page_notes_by_slug` matches plan slugs to hints
# ---------------------------------------------------------------------------


def test_page_notes_by_slug_maps_titles_to_normalized_slugs() -> None:
    from backend.app.wiki.context import RepoContext
    from backend.app.wiki.pipeline import _page_notes_by_slug
    from backend.app.wiki.schemas import PagePlan

    steering = WikiSteering(
        pages=[
            PageHint(
                title="API Reference",
                purpose="p",
                page_notes=["only document v2 endpoints"],
            ),
            PageHint(
                title="Architecture",
                purpose="p",
                page_notes=["highlight the writer pipeline"],
            ),
            PageHint(title="Unmatched", purpose="p", page_notes=["x"]),
        ]
    )
    context = RepoContext(
        repository_id="00000000-0000-0000-0000-000000000001",
        commit_sha="abc",
        file_tree_hash="ft",
        docs_hash="dh",
        summaries_hash="sh",
        identity_hash="ih",
        steering=steering,
    )
    plan = PagePlan(
        pages=[
            PageSpec(slug="api-reference", title="API Reference", purpose="p"),
            PageSpec(slug="architecture", title="Architecture", purpose="p"),
        ]
    )
    out = _page_notes_by_slug(context, plan)
    assert out == {
        "api-reference": ["only document v2 endpoints"],
        "architecture": ["highlight the writer pipeline"],
    }


def test_page_notes_by_slug_returns_empty_without_steering() -> None:
    from backend.app.wiki.context import RepoContext
    from backend.app.wiki.pipeline import _page_notes_by_slug
    from backend.app.wiki.schemas import PagePlan

    context = RepoContext(
        repository_id="00000000-0000-0000-0000-000000000001",
        commit_sha="abc",
        file_tree_hash="ft",
        docs_hash="dh",
        summaries_hash="sh",
        identity_hash="ih",
    )
    plan = PagePlan(
        pages=[PageSpec(slug="index", title="I", purpose="p")]
    )
    assert _page_notes_by_slug(context, plan) == {}
