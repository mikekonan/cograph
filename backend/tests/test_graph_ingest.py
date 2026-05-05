from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select

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

    module_node = persisted_nodes["bcp47_language.bcp47_language"]
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
