from __future__ import annotations

import subprocess
from datetime import UTC
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.graph import ingest as ingest_module
from backend.app.graph.ingest import GraphIngestService
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "--initial-branch=main", cwd=path)
    _git("config", "user.email", "test@cograph", cwd=path)
    _git("config", "user.name", "Cograph Test", cwd=path)


def _commit_all(path: Path, message: str) -> str:
    _git("add", "-A", cwd=path)
    _git("commit", "-m", message, cwd=path)
    return _git("rev-parse", "HEAD", cwd=path)


def _has_git() -> bool:
    try:
        subprocess.run(
            ["git", "--version"], check=True, capture_output=True, timeout=5
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _has_git(), reason="git CLI is required")


async def _create_repo(db_session) -> Repository:
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
    return repository


async def _nodes_by_qn(db_session, repository_id):
    return {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository_id)
            )
        ).all()
    }


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def test_git_diff_mode_processes_only_changed_file(
    db_session, tmp_path, monkeypatch
):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    (checkout / "alpha.py").write_text(
        "def alpha() -> int:\n    return 1\n", encoding="utf-8"
    )
    (checkout / "beta.py").write_text(
        "def beta() -> int:\n    return 2\n", encoding="utf-8"
    )
    first_commit = _commit_all(checkout, "initial")

    repository = await _create_repo(db_session)

    service = GraphIngestService()
    first_result = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        commit_sha=first_commit,
    )
    await db_session.commit()
    assert first_result.processed_files == 2
    first_nodes = await _nodes_by_qn(db_session, repository.id)
    assert first_nodes["alpha.alpha"].first_seen_commit == first_commit
    assert first_nodes["alpha.alpha"].last_changed_commit == first_commit
    first_alpha_changed_at = first_nodes["alpha.alpha"].last_changed_at
    assert first_alpha_changed_at is not None

    # Modify only alpha.py
    (checkout / "alpha.py").write_text(
        "def alpha() -> int:\n    return 42\n", encoding="utf-8"
    )
    second_commit = _commit_all(checkout, "bump alpha")

    # Spy on the subprocess call to verify argv AND capture return value so we
    # can prove incremental path — not full-walk fallback — was taken.
    recorded_argv: list[list[str]] = []
    real_run = subprocess.run

    def spy_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(argv, list) and argv[:1] == ["git"] and "diff" in argv:
            recorded_argv.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(ingest_module.subprocess, "run", spy_run)

    incremental_calls: list[list[ingest_module.GitFileChange] | None] = []
    real_detect = ingest_module._detect_git_changes_safely

    def spy_detect(root_path, since_commit):
        result = real_detect(root_path, since_commit)
        incremental_calls.append(result)
        return result

    monkeypatch.setattr(
        ingest_module, "_detect_git_changes_safely", spy_detect
    )

    second_result = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        last_commit=first_commit,
        commit_sha=second_commit,
    )
    await db_session.commit()

    # Incremental detector ran exactly once and returned a concrete change list,
    # not None (which would mean silent full-walk fallback).
    assert len(incremental_calls) == 1
    assert incremental_calls[0] is not None
    assert len(incremental_calls[0]) == 1
    assert incremental_calls[0][0].kind == "M"
    assert incremental_calls[0][0].file_path == "alpha.py"

    # Exact argv contract — no invalid flags, since_commit properly terminated.
    assert len(recorded_argv) == 1
    argv = recorded_argv[0]
    assert "--no-renames=false" not in argv
    assert "--name-status" in argv
    assert f"{first_commit}..HEAD" in argv
    # The `--` separator must follow the revision range to prevent path/option
    # ambiguity if since_commit ever looks like a flag.
    assert argv[-1] == "--"

    # Only alpha.py should have been re-processed (via git diff)
    assert second_result.processed_files == 1
    assert any("alpha.py" in f for f in second_result.replaced_files)
    assert not any("beta.py" in f for f in second_result.replaced_files)
    second_nodes = await _nodes_by_qn(db_session, repository.id)
    assert second_nodes["alpha.alpha"].first_seen_commit == first_commit
    assert second_nodes["alpha.alpha"].last_changed_commit == second_commit
    assert second_nodes["alpha.alpha"].last_changed_at is not None
    assert _as_utc(second_nodes["alpha.alpha"].last_changed_at) >= _as_utc(first_alpha_changed_at)
    assert second_nodes["beta.beta"].last_changed_commit == first_commit


async def test_detect_git_changes_rejects_malformed_since_commit(tmp_path):
    # Safety net: SHA validation rejects injection attempts before subprocess
    # invocation, preventing argv-as-flag confusion.
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    assert (
        ingest_module._detect_git_changes_safely(repo, "--config=core.pager=evil")
        is None
    )
    assert ingest_module._detect_git_changes_safely(repo, "; rm -rf /") is None
    assert ingest_module._detect_git_changes_safely(repo, "") is None


async def test_git_diff_mode_handles_delete(db_session, tmp_path):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    (checkout / "keep.py").write_text("def keep() -> int:\n    return 1\n", "utf-8")
    (checkout / "gone.py").write_text("def gone() -> int:\n    return 2\n", "utf-8")
    first_commit = _commit_all(checkout, "initial")

    repository = await _create_repo(db_session)
    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        commit_sha=first_commit,
    )
    await db_session.commit()

    (checkout / "gone.py").unlink()
    second_commit = _commit_all(checkout, "remove gone")

    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        last_commit=first_commit,
        commit_sha=second_commit,
    )
    await db_session.commit()

    sources = [
        sf.file_path
        for sf in (
            await db_session.scalars(
                select(SourceFile).where(SourceFile.repository_id == repository.id)
            )
        ).all()
    ]
    assert sources == ["keep.py"]
    nodes = await _nodes_by_qn(db_session, repository.id)
    assert "gone" not in nodes
    assert "gone.gone" not in nodes


async def test_git_diff_mode_falls_back_to_full_walk_on_unknown_commit(
    db_session, tmp_path
):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    (checkout / "alpha.py").write_text(
        "def alpha() -> int:\n    return 1\n", encoding="utf-8"
    )
    _commit_all(checkout, "initial")

    repository = await _create_repo(db_session)
    service = GraphIngestService()
    # Pass a fabricated last_commit — git diff will fail; full walk kicks in.
    result = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        last_commit="0000000000000000000000000000000000000000",
    )
    await db_session.commit()

    assert result.processed_files == 1
    nodes = await _nodes_by_qn(db_session, repository.id)
    assert "alpha" in nodes
    assert "alpha.alpha" in nodes
