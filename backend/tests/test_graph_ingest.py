from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import event, select

from backend.app.graph.go_variants import resolve_go_index_profile
from backend.app.graph.ingest import GitFileChange, GraphIngestService
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository


async def test_graph_ingest_service_indexes_supported_checkout_files(
    db_session,
    tmp_path,
):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    package_path = checkout_path / "pkg"
    package_path.mkdir(parents=True)
    (checkout_path / "README.md").write_text("# ignored\n", encoding="utf-8")
    (package_path / "utils.py").write_text(
        "def helper() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (package_path / "service.py").write_text(
        "from .utils import helper\n\ndef call() -> int:\n    return helper()\n",
        encoding="utf-8",
    )

    result = await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    persisted_nodes = {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    helper_node = persisted_nodes["pkg.utils.helper"]
    caller_node = persisted_nodes["pkg.service.call"]

    assert result.processed_files == 2
    assert result.inserted_nodes == 4
    assert set(result.replaced_files) == {"pkg/service.py", "pkg/utils.py"}
    assert result.resolved_calls == 1
    assert result.unresolved_calls == 0
    assert caller_node.callees == [str(helper_node.id)]
    assert helper_node.callers == [str(caller_node.id)]
    assert "README" not in persisted_nodes


async def test_graph_ingest_service_indexes_go_checkout_and_resolves_repo_calls(
    db_session,
    tmp_path,
):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    service_path = checkout_path / "service"
    utils_path = checkout_path / "pkg" / "utils"
    service_path.mkdir(parents=True)
    utils_path.mkdir(parents=True)

    (checkout_path / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
    (service_path / "login.go").write_text(
        """package service

import localutils "example.com/demo/pkg/utils"

func (s *UserService) Login(userID string) error {
    Helper(userID)
    s.audit(userID)
    localutils.Normalize(userID)
    return nil
}
""",
        encoding="utf-8",
    )
    (service_path / "user.go").write_text(
        """package service

type UserService struct{}

func (s *UserService) audit(userID string) string {
    return userID
}

func Helper(userID string) string {
    return userID
}
""",
        encoding="utf-8",
    )
    (utils_path / "utils.go").write_text(
        """package utils

func Normalize(userID string) string {
    return userID
}
""",
        encoding="utf-8",
    )

    result = await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    persisted_nodes = {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    login_node = persisted_nodes["service.UserService.Login"]
    audit_node = persisted_nodes["service.UserService.audit"]
    helper_node = persisted_nodes["service.Helper"]
    normalize_node = persisted_nodes["pkg.utils.Normalize"]

    assert result.processed_files == 3
    assert result.inserted_nodes == 8
    assert set(result.replaced_files) == {
        "pkg/utils/utils.go",
        "service/login.go",
        "service/user.go",
    }
    assert result.resolved_calls == 3
    assert result.unresolved_calls == 0
    assert set(login_node.callees) == {
        str(audit_node.id),
        str(helper_node.id),
        str(normalize_node.id),
    }


async def test_graph_ingest_service_indexes_go_types_fixture_repo_shape(
    db_session,
    tmp_path,
    copy_go_types_fixture: Callable[[Path], Path],
):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="go-types",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = copy_go_types_fixture(tmp_path / "checkout")

    result = await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    persisted_nodes = {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    module_node = persisted_nodes["bcp47_language.bcp47_language#module"]
    base_language_node = persisted_nodes["bcp47_language.Language.BaseISO639Language"]
    lookup_node = persisted_nodes["language.ByAlpha2CodeStrErr"]
    subdivision_unmarshal = persisted_nodes["country.subdivision.Code.UnmarshalJSON"]
    unsafe_string_node = persisted_nodes["internal.utils.UnsafeStringFromJson"]
    subdivision_validate = persisted_nodes[
        "country.subdivision.Code.ValidateForCountry"
    ]
    country_alpha2_unmarshal = persisted_nodes["country.Alpha2Code.UnmarshalJSON"]
    country_lookup_node = persisted_nodes["country.ByAlpha2CodeErr"]

    assert result.processed_files == 8
    assert set(result.replaced_files) == {
        "bcp47_language/bcp47_language.go",
        "country/alpha2.go",
        "country/country.go",
        "country/subdivision/code.go",
        "country/subdivision/subdivision.go",
        "internal/utils/json.go",
        "language/alpha2.go",
        "language/language.go",
    }
    assert result.inserted_nodes >= 40
    assert result.resolved_calls >= 10
    assert module_node.node_metadata["package_name"] == "bcp47_language"
    assert module_node.node_metadata["package_qualified_name"] == "bcp47_language"
    assert "language" in module_node.node_metadata["imports"]
    assert (
        "golang.org.x.text.language as stdLanguage"
        in module_node.node_metadata["imports"]
    )
    assert str(lookup_node.id) in base_language_node.callees
    assert str(unsafe_string_node.id) in subdivision_unmarshal.callees
    assert str(country_lookup_node.id) in subdivision_validate.callees
    assert str(country_lookup_node.id) in country_alpha2_unmarshal.callees


async def test_graph_ingest_service_prunes_nodes_for_deleted_files(
    db_session,
    tmp_path,
):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    first_file = checkout_path / "a.py"
    second_file = checkout_path / "b.py"
    first_file.write_text("def helper() -> int:\n    return 1\n", encoding="utf-8")
    second_file.write_text("def stale() -> int:\n    return 2\n", encoding="utf-8")

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    second_file.unlink()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    persisted_qualified_names = {
        node.qualified_name
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    assert persisted_qualified_names == {"a", "a.helper"}


async def test_graph_ingest_service_skips_unchanged_files_on_reingest(
    db_session,
    tmp_path,
):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    (checkout_path / "a.py").write_text(
        "def helper() -> int:\n    return 1\n", encoding="utf-8"
    )

    service = GraphIngestService()
    first_result = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    second_result = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    assert first_result.processed_files == 1
    assert second_result.processed_files == 0
    assert second_result.inserted_nodes == 0
    assert second_result.replaced_files == ()


async def test_graph_ingest_runs_single_repo_wide_select(db_session, tmp_path, app):
    """Pin the O(F+N) win: the whole repo-wide CodeNode SELECT must fire
    exactly once for an entire full-walk ingest, not once per file.

    Before the cache refactor this query was inside `persist_graph` and
    fired F times per ingest — the root cause of the parse-step hang on
    large monorepos.
    """

    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    package_path = checkout_path / "pkg"
    package_path.mkdir(parents=True)
    for index in range(4):
        (package_path / f"mod{index}.py").write_text(
            f"def helper_{index}() -> int:\n    return {index}\n",
            encoding="utf-8",
        )

    repo_wide_select_count = 0
    engine = app.state.session_manager.engine

    matched_sql: list[str] = []

    def _on_execute(_conn, clauseelement, _multiparams, _params, _execution_options):
        nonlocal repo_wide_select_count
        try:
            sql = str(clauseelement).lower()
        except Exception:
            return
        if not sql.startswith("select") or "from code_nodes" not in sql:
            return
        if "where" not in sql:
            return
        where_clause = sql.split("where", 1)[1]
        # The full-repo CodeNode fetch: WHERE filters *only* by
        # repository_id. Per-file scans add `file_path`, scoped fetches
        # add `id IN (...)`, and the existing_module_hashes scan adds a
        # `node_type` predicate. Anything that narrows further is not
        # the smoking-gun query we're trying to keep at exactly one.
        if (
            "repository_id" in where_clause
            and "file_path" not in where_clause
            and "node_type" not in where_clause
            and " in " not in where_clause
            and ".id" not in where_clause
        ):
            repo_wide_select_count += 1
            matched_sql.append(sql)

    event.listen(engine.sync_engine, "before_execute", _on_execute)
    try:
        result = await GraphIngestService().ingest_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout_path,
        )
        await db_session.commit()
    finally:
        event.remove(engine.sync_engine, "before_execute", _on_execute)

    assert result.processed_files == 4
    # One SELECT at the top of `_ingest_full_walk` to seed the cache; the
    # per-file path no longer issues this query.
    assert repo_wide_select_count == 1, "\n---\n".join(matched_sql)


async def test_graph_ingest_full_walk_counter_parity_with_repo_total(
    db_session, tmp_path
):
    """Sanity: deltas accumulated in the loop match what a final repo-wide
    SELECT would have computed (the path we just removed).
    """

    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    package_path = checkout_path / "pkg"
    package_path.mkdir(parents=True)
    (package_path / "utils.py").write_text(
        "def helper() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (package_path / "service.py").write_text(
        "from .utils import helper\n\ndef call() -> int:\n    return helper()\n",
        encoding="utf-8",
    )

    result = await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    persisted_nodes = list(
        (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    )
    total_resolved = sum(len(node.callees) for node in persisted_nodes)
    total_unresolved = 0
    for node in persisted_nodes:
        unresolved = node.node_metadata.get("unresolved_calls")
        if isinstance(unresolved, list):
            total_unresolved += len(unresolved)

    assert result.resolved_calls == total_resolved
    assert result.unresolved_calls == total_unresolved


async def test_graph_ingest_emits_structured_start_and_done_logs(
    caplog,
    db_session,
    tmp_path,
):
    """Operator-facing observability: every ingest run must emit a
    structured `ingest_start` and `ingest_done` INFO log with
    machine-parseable `extra={...}` fields. Without this, a parse-step
    hang on a large monorepo is invisible until the step-timeout fires.
    """
    import logging

    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    pkg = checkout_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "a.py").write_text("def f(): return 1\n", encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="backend.app.graph.ingest"):
        await GraphIngestService().ingest_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout_path,
        )

    events = {
        record.__dict__.get("event")
        for record in caplog.records
        if record.name == "backend.app.graph.ingest"
    }
    assert "ingest_start" in events
    assert "ingest_done" in events

    start_record = next(
        record
        for record in caplog.records
        if record.__dict__.get("event") == "ingest_start"
    )
    assert start_record.__dict__["mode"] == "full"
    assert start_record.__dict__["repository_id"] == str(repository.id)

    done_record = next(
        record
        for record in caplog.records
        if record.__dict__.get("event") == "ingest_done"
    )
    assert done_record.__dict__["mode"] == "full"
    assert done_record.__dict__["files_processed"] >= 1
    assert "duration_s" in done_record.__dict__


async def test_one_bad_file_does_not_kill_the_whole_ingest(
    db_session,
    tmp_path,
    monkeypatch,
):
    """If `persist_graph` raises `StaleDataError` for one file, the
    SAVEPOINT must roll that file's writes back and the rest of the
    ingest must continue. Pre-fix, a single bad file dropped the whole
    reindex of a large monorepo on the floor."""
    from sqlalchemy.orm.exc import StaleDataError

    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    (checkout_path / "good_a.py").write_text(
        "def alpha() -> int:\n    return 1\n", encoding="utf-8"
    )
    (checkout_path / "bad.py").write_text(
        "def beta() -> int:\n    return 2\n", encoding="utf-8"
    )
    (checkout_path / "good_b.py").write_text(
        "def gamma() -> int:\n    return 3\n", encoding="utf-8"
    )

    # Monkeypatch GraphBuilder.persist_graph to detonate on `bad.py`
    # by inspecting the file_path of any extracted node.
    from backend.app.graph.builder import GraphBuilder

    real_persist_graph = GraphBuilder.persist_graph

    async def _maybe_raising_persist_graph(self, *args, **kwargs):
        extracted = kwargs.get("extracted_graph")
        if extracted is not None:
            for node in extracted.nodes:
                if node.file_path == "bad.py":
                    raise StaleDataError(
                        "UPDATE statement on table 'code_nodes' "
                        "expected to update 1 row(s); 0 were matched.",
                        None,
                        None,
                    )
        return await real_persist_graph(self, *args, **kwargs)

    monkeypatch.setattr(GraphBuilder, "persist_graph", _maybe_raising_persist_graph)

    result = await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    persisted = {
        node.qualified_name
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    # Both "good" files made it through; the bad file's nodes are absent.
    assert "good_a" in persisted
    assert "good_a.alpha" in persisted
    assert "good_b" in persisted
    assert "good_b.gamma" in persisted
    assert "bad" not in persisted
    assert "bad.beta" not in persisted

    # The ingest itself succeeded — no exception propagated out.
    assert result is not None


async def test_one_fk_violation_does_not_kill_the_whole_ingest(
    db_session,
    tmp_path,
    monkeypatch,
):
    """An `IntegrityError` (e.g. FK violation when the cache holds a
    stale parent UUID) must be handled identically to `StaleDataError`:
    SAVEPOINT rolls the file back, cache is refreshed, the rest of the
    ingest continues. Prod hit this on a Go monorepo where reparenting
    a method onto a deleted struct produced an
    `asyncpg.exceptions.ForeignKeyViolationError`.
    """
    from sqlalchemy.exc import IntegrityError

    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    (checkout_path / "good_a.py").write_text(
        "def alpha() -> int:\n    return 1\n", encoding="utf-8"
    )
    (checkout_path / "bad.py").write_text(
        "def beta() -> int:\n    return 2\n", encoding="utf-8"
    )
    (checkout_path / "good_b.py").write_text(
        "def gamma() -> int:\n    return 3\n", encoding="utf-8"
    )

    from backend.app.graph.builder import GraphBuilder

    real_persist_graph = GraphBuilder.persist_graph

    async def _maybe_raising_persist_graph(self, *args, **kwargs):
        extracted = kwargs.get("extracted_graph")
        if extracted is not None:
            for node in extracted.nodes:
                if node.file_path == "bad.py":
                    raise IntegrityError(
                        "UPDATE code_nodes SET parent_id=$1 WHERE id=$2",
                        params=None,
                        orig=Exception(
                            "insert or update on table 'code_nodes' violates "
                            "foreign key constraint "
                            "'fk_code_nodes_parent_id_code_nodes'"
                        ),
                    )
        return await real_persist_graph(self, *args, **kwargs)

    monkeypatch.setattr(GraphBuilder, "persist_graph", _maybe_raising_persist_graph)

    result = await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    persisted = {
        node.qualified_name
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    assert "good_a" in persisted
    assert "good_a.alpha" in persisted
    assert "good_b" in persisted
    assert "good_b.gamma" in persisted
    assert "bad" not in persisted
    assert "bad.beta" not in persisted

    assert result is not None


# --- TS/JS quality gates -----------------------------------------------------
# Gate A: the junk filter is scoped to TS/JS files ONLY — Go/Python discovery
# stays byte-identical, so adding TS/JS support is a $0 no-op for every
# existing repo (no structural_hash movement, no re-embeds, no wiki churn).
# Gate B: JS-ecosystem junk (node_modules, dist, minified, over-cap) never
# produces code_nodes — no tokens are ever spent embedding/summarizing it.


def test_discover_source_files_junk_filter_is_scoped_to_ts_js(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export const x = 1;\n", "utf-8")
    (tmp_path / "src" / "ok.js").write_text("module.exports = 1;\n", "utf-8")
    (tmp_path / "src" / "util.py").write_text("KEEP = 1\n", "utf-8")
    (tmp_path / "src" / "bundle.min.js").write_text("!function(){}();\n", "utf-8")
    (tmp_path / "src" / "lib.min.mjs").write_text("export{};\n", "utf-8")
    (tmp_path / "src" / "huge.ts").write_text("x" * 1_048_577, "utf-8")

    junk_ts_js = {
        "node_modules/pkg": "index.js",
        "dist": "gen.ts",
        "build": "out.js",
        ".next": "page.tsx",
        ".nuxt": "n.ts",
        ".output": "o.mjs",
        ".turbo": "t.cts",
        "coverage": "cov.js",
    }
    for directory, filename in junk_ts_js.items():
        target = tmp_path / directory
        target.mkdir(parents=True, exist_ok=True)
        (target / filename).write_text("export const junk = 1;\n", "utf-8")
    # Go/Python inside those same dirs are indexed today — must survive.
    (tmp_path / "node_modules" / "pkg" / "keep.go").write_text(
        "package keep\n", "utf-8"
    )
    (tmp_path / "node_modules" / "pkg" / "keep.py").write_text("KEEP = 1\n", "utf-8")
    (tmp_path / "dist" / "keep.go").write_text("package keep\n", "utf-8")

    discovered = {
        path.relative_to(tmp_path).as_posix()
        for path in GraphIngestService()._discover_source_files(tmp_path)
    }

    assert discovered == {
        "src/app.ts",
        "src/ok.js",
        "src/util.py",
        "node_modules/pkg/keep.go",
        "node_modules/pkg/keep.py",
        "dist/keep.go",
    }


async def test_graph_ingest_skips_js_ecosystem_junk_end_to_end(db_session, tmp_path):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/ts-app.git",
        name="ts-app",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout = tmp_path / "checkout"
    (checkout / "src").mkdir(parents=True)
    (checkout / "src" / "app.ts").write_text(
        "export function main(): void {}\n", "utf-8"
    )
    decoy = checkout / "node_modules" / "left-pad"
    decoy.mkdir(parents=True)
    (decoy / "index.js").write_text(
        "module.exports = function leftPad() {};\n", "utf-8"
    )
    (checkout / "dist").mkdir()
    (checkout / "dist" / "bundle.min.js").write_text(
        "!function(){function junk(){}}();\n", "utf-8"
    )

    result = await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    file_paths = {
        node.file_path
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }
    assert file_paths == {"src/app.ts"}
    assert result.processed_files == 1


async def test_incremental_ts_js_pruned_changes_skip_and_downgrade(
    db_session, tmp_path
):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/ts-app.git",
        name="ts-app",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout = tmp_path / "checkout"
    (checkout / "src").mkdir(parents=True)
    (checkout / "src" / "app.ts").write_text(
        "export function main(): void {}\n", "utf-8"
    )
    (checkout / "src" / "keep.ts").write_text(
        "export function keep(): void {}\n", "utf-8"
    )

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    # Simulate a commit that moves a source file into dist/ and churns junk.
    (checkout / "dist").mkdir()
    (checkout / "src" / "app.ts").rename(checkout / "dist" / "app.ts")
    (checkout / "dist" / "bundle.min.js").write_text("!function(){}();\n", "utf-8")
    decoy = checkout / "node_modules" / "left-pad"
    decoy.mkdir(parents=True)
    (decoy / "index.js").write_text("module.exports = 1;\n", "utf-8")

    await service._ingest_incremental_from_git(
        session=db_session,
        repository_id=repository.id,
        root_path=checkout,
        git_changes=[
            GitFileChange(kind="M", file_path="dist/bundle.min.js"),
            GitFileChange(kind="A", file_path="node_modules/left-pad/index.js"),
            GitFileChange(
                kind="R", old_file_path="src/app.ts", file_path="dist/app.ts"
            ),
        ],
        commit_sha=None,
        go_module_path=None,
        go_profile=resolve_go_index_profile(checkout),
    )
    await db_session.commit()

    file_paths = {
        node.file_path
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }
    # Rename into dist/ downgraded to a delete of the old rows; junk churn
    # produced nothing.
    assert file_paths == {"src/keep.ts"}


async def _ingest_ts_app(db_session, tmp_path, copy_ts_app_fixture):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/ts-app.git",
        name="ts-app",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout = copy_ts_app_fixture(tmp_path / "checkout")
    service = GraphIngestService()
    result = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()
    return repository, checkout, service, result


async def test_graph_ingest_indexes_ts_app_fixture_repo_shape(
    db_session, tmp_path, copy_ts_app_fixture
):
    repository, _, _, result = await _ingest_ts_app(
        db_session, tmp_path, copy_ts_app_fixture
    )

    nodes = {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    # Decoys (node_modules, dist/*.min.js) never became nodes.
    assert result.processed_files == 5
    assert not any(
        "node_modules" in node.file_path or node.file_path.startswith("dist/")
        for node in nodes.values()
    )

    # Barrel: src/index.ts collapses to module QN `src`.
    assert nodes["src"].node_type is CodeNodeType.MODULE

    service = nodes["src.services.userService.UserService"]
    assert service.node_type is CodeNodeType.CLASS
    assert service.language == "typescript"
    assert service.node_metadata["exported"] is True

    error_type = nodes["src.services.userService.NotFoundError"]
    assert error_type.node_metadata["bases"] == ["Error"]

    assert nodes["src.types.Handler"].node_type is CodeNodeType.INTERFACE
    assert nodes["src.types.Result"].node_type is CodeNodeType.TYPE_ALIAS
    assert nodes["src.types.Color"].node_metadata["ts_kind"] == "enum"
    assert nodes["src.components.Button.Button"].node_type is CodeNodeType.FUNCTION

    # CJS: module.exports gates the export flag, not naming.
    assert nodes["src.legacy.util.normalize"].node_metadata["exported"] is True
    assert nodes["src.legacy.util.internalOnly"].node_metadata["exported"] is False
    assert nodes["src.legacy.util.normalize"].language == "javascript"

    # Call resolution: `this.audit` and the cross-file TS→JS relative import.
    login = nodes["src.services.userService.UserService.login"]
    assert str(nodes["src.services.userService.UserService.audit"].id) in login.callees
    assert str(nodes["src.legacy.util.normalize"].id) in login.callees
    assert result.resolved_calls > 0


async def test_graph_ingest_ts_app_reingest_unchanged_is_free(
    db_session, tmp_path, copy_ts_app_fixture
):
    # Gate C: an unchanged TS/JS repo re-syncs with zero parses — the same
    # content_hash reuse that makes Go/Python repos $0.
    repository, checkout, service, first = await _ingest_ts_app(
        db_session, tmp_path, copy_ts_app_fixture
    )
    assert first.processed_files == 5

    second = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    assert second.processed_files == 0
    assert second.inserted_nodes == 0
    assert second.replaced_files == ()


async def test_graph_ingest_ts_app_single_file_change_touches_one_file(
    db_session, tmp_path, copy_ts_app_fixture
):
    repository, checkout, service, _ = await _ingest_ts_app(
        db_session, tmp_path, copy_ts_app_fixture
    )
    before = {
        node.qualified_name: node.id
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    target = checkout / "src" / "services" / "userService.ts"
    target.write_text(
        target.read_text(encoding="utf-8").replace("user ${id} not found", "gone"),
        encoding="utf-8",
    )
    result = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    assert result.processed_files == 1
    assert result.replaced_files == ("src/services/userService.ts",)
    after = {
        node.qualified_name: node.id
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }
    # Untouched files keep their node UUIDs (embeddings survive).
    assert (
        after["src.components.Button.Button"] == before["src.components.Button.Button"]
    )
    assert after["src.legacy.util.normalize"] == before["src.legacy.util.normalize"]


async def test_incremental_cross_extension_renames_reconcile_both_sides(
    db_session, tmp_path
):
    # Codex-debate F5: a.ts → a.txt must delete the stale TS rows; a.go → a.ts
    # must index the new TS file (the go-package reingest only sees .go).
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/mixed.git",
        name="mixed",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout = tmp_path / "checkout"
    (checkout / "src").mkdir(parents=True)
    (checkout / "pkg").mkdir()
    (checkout / "go.mod").write_text("module example.com/mixed\n", "utf-8")
    (checkout / "src" / "app.ts").write_text(
        "export function main(): void {}\n", "utf-8"
    )
    (checkout / "pkg" / "tool.go").write_text(
        'package pkg\n\nfunc Tool() string { return "x" }\n', "utf-8"
    )
    (checkout / "pkg" / "keep.go").write_text(
        'package pkg\n\nfunc Keep() string { return "y" }\n', "utf-8"
    )

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    # Simulate the renames on disk.
    (checkout / "src" / "app.ts").rename(checkout / "src" / "app.txt")
    (checkout / "pkg" / "tool.go").rename(checkout / "src" / "tool.ts")
    (checkout / "src" / "tool.ts").write_text(
        "export function tool(): void {}\n", "utf-8"
    )

    await service._ingest_incremental_from_git(
        session=db_session,
        repository_id=repository.id,
        root_path=checkout,
        git_changes=[
            GitFileChange(
                kind="R", old_file_path="src/app.ts", file_path="src/app.txt"
            ),
            GitFileChange(
                kind="R", old_file_path="pkg/tool.go", file_path="src/tool.ts"
            ),
        ],
        commit_sha=None,
        go_module_path="example.com/mixed",
        go_profile=resolve_go_index_profile(checkout),
    )
    await db_session.commit()

    file_paths = {
        node.file_path
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }
    assert "src/app.ts" not in file_paths  # stale TS rows reconciled
    assert "pkg/tool.go" not in file_paths  # go side reconciled by package
    assert "src/tool.ts" in file_paths  # new TS side actually indexed
    assert "pkg/keep.go" in file_paths


async def test_cross_language_rename_with_colliding_qualified_name(
    db_session, tmp_path
):
    # Codex-debate must-fix: a.go (func X → QN a.X) renamed to a.ts
    # (export function X → the same QN a.X). Inserts used to run before
    # the go stale-path cleanup, so the TS file savepoint-skipped on the
    # QN collision and the cleanup then wiped the go rows — both sides
    # vanished. Deletes-before-inserts keeps a.X alive as TypeScript.
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/collide.git",
        name="collide",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "go.mod").write_text("module example.com/collide\n", "utf-8")
    (checkout / "a.go").write_text(
        'package a\n\nfunc X() string { return "go" }\n', "utf-8"
    )

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    (checkout / "a.go").unlink()
    (checkout / "a.ts").write_text("export function X(): void {}\n", "utf-8")

    await service._ingest_incremental_from_git(
        session=db_session,
        repository_id=repository.id,
        root_path=checkout,
        git_changes=[
            GitFileChange(kind="R", old_file_path="a.go", file_path="a.ts"),
        ],
        commit_sha=None,
        go_module_path="example.com/collide",
        go_profile=resolve_go_index_profile(checkout),
    )
    await db_session.commit()

    nodes = (
        await db_session.scalars(
            select(CodeNode).where(CodeNode.repository_id == repository.id)
        )
    ).all()
    file_paths = {node.file_path for node in nodes}
    assert "a.go" not in file_paths
    functions = {
        node.qualified_name: node
        for node in nodes
        if node.node_type is CodeNodeType.FUNCTION
    }
    assert "a.X" in functions
    assert functions["a.X"].language == "typescript"
    assert functions["a.X"].file_path == "a.ts"


async def test_go_package_move_with_retained_package_name(db_session, tmp_path):
    # Codex-debate round 3: z/a.go (package z, func X → QN z.X) moved to
    # the repo root while KEEPING `package z` — the QN doesn't change.
    # Per-package delete→insert interleave used to insert package "."
    # first (savepoint-skip on the live z.X rows), then package "z"
    # stale-cleanup wiped the old rows — z.X vanished entirely. Go stale
    # deletes now run for ALL packages before any package inserts.
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/pkgmove.git",
        name="pkgmove",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout = tmp_path / "checkout"
    (checkout / "z").mkdir(parents=True)
    (checkout / "go.mod").write_text("module example.com/pkgmove\n", "utf-8")
    (checkout / "z" / "a.go").write_text(
        'package z\n\nfunc X() string { return "go" }\n', "utf-8"
    )

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    (checkout / "z" / "a.go").rename(checkout / "a.go")

    await service._ingest_incremental_from_git(
        session=db_session,
        repository_id=repository.id,
        root_path=checkout,
        git_changes=[
            GitFileChange(kind="R", old_file_path="z/a.go", file_path="a.go"),
        ],
        commit_sha=None,
        go_module_path="example.com/pkgmove",
        go_profile=resolve_go_index_profile(checkout),
    )
    await db_session.commit()

    nodes = (
        await db_session.scalars(
            select(CodeNode).where(CodeNode.repository_id == repository.id)
        )
    ).all()
    file_paths = {node.file_path for node in nodes}
    assert "z/a.go" not in file_paths
    functions = {
        node.qualified_name: node
        for node in nodes
        if node.node_type is CodeNodeType.FUNCTION
    }
    assert "z.X" in functions
    assert functions["z.X"].file_path == "a.go"
