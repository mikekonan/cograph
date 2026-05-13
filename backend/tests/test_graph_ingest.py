from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import event, select

from backend.app.graph.ingest import GraphIngestService
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import RepositoryStatus, SyncSchedule
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
        record for record in caplog.records
        if record.__dict__.get("event") == "ingest_start"
    )
    assert start_record.__dict__["mode"] == "full"
    assert start_record.__dict__["repository_id"] == str(repository.id)

    done_record = next(
        record for record in caplog.records
        if record.__dict__.get("event") == "ingest_done"
    )
    assert done_record.__dict__["mode"] == "full"
    assert done_record.__dict__["files_processed"] >= 1
    assert "duration_s" in done_record.__dict__
