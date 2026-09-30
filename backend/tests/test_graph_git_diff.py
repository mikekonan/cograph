from __future__ import annotations

import logging
import subprocess
from datetime import UTC
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.graph import ingest as ingest_module
from backend.app.graph.extractor import EXTRACTOR_VERSIONS, compute_symbol_key
from backend.app.graph.ingest import GraphIngestService
from backend.app.graph.languages import GraphLanguage
from backend.app.models.code_edge import CodeEdge
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


async def test_stale_extractor_stamp_reextracts_once_and_keeps_node_ids(
    db_session, tmp_path, monkeypatch
):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    (checkout / "a.py").write_text("def a() -> int:\n    return 1\n", "utf-8")
    (checkout / "b.ts").write_text("export function b() { return 2; }\n", "utf-8")
    head = _commit_all(checkout, "initial")

    repository = await _create_repo(db_session)
    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        commit_sha=head,
    )
    await db_session.commit()
    ids_before = {
        qn: node.id for qn, node in (await _nodes_by_qn(db_session, repository.id)).items()
    }

    # A manual reindex of an unchanged HEAD: the git diff is empty, so only
    # the stale stamp can make it re-extract anything.
    async def reindex():
        result = await service.ingest_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout,
            last_commit=head,
            commit_sha=head,
        )
        await db_session.commit()
        return result

    monkeypatch.setitem(EXTRACTOR_VERSIONS, GraphLanguage.PYTHON, 99)
    result = await reindex()
    assert result.processed_files == 1
    assert result.replaced_files == ("a.py",)
    nodes = await _nodes_by_qn(db_session, repository.id)
    assert {qn: node.id for qn, node in nodes.items()} == ids_before
    assert nodes["a"].node_metadata["extractor_stamp"] == "99"

    assert (await reindex()).processed_files == 0


async def test_tsconfig_change_reextracts_only_the_files_it_covers(db_session, tmp_path):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    (checkout / "a.py").write_text("def a() -> int:\n    return 1\n", "utf-8")
    (checkout / "app").mkdir()
    (checkout / "app" / "b.ts").write_text(
        'import { c } from "@/c";\nexport function b() { return c(); }\n', "utf-8"
    )
    (checkout / "app" / "c.ts").write_text("export function c() { return 1; }\n", "utf-8")
    (checkout / "shared").mkdir()
    (checkout / "shared" / "d.ts").write_text("export function d() { return 2; }\n", "utf-8")
    first = _commit_all(checkout, "initial")

    repository = await _create_repo(db_session)
    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        commit_sha=first,
    )
    await db_session.commit()
    nodes = await _nodes_by_qn(db_session, repository.id)
    assert not nodes["app.b.b"].callees

    (checkout / "app" / "tsconfig.json").write_text(
        '{"compilerOptions": {"paths": {"@/*": ["./*"]}}}', "utf-8"
    )
    second = _commit_all(checkout, "alias")

    async def sync(last_commit: str):
        result = await service.ingest_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout,
            last_commit=last_commit,
            commit_sha=second,
        )
        await db_session.commit()
        return result

    # a.py and shared/d.ts sit outside app/tsconfig.json: untouched.
    result = await sync(first)
    assert result.processed_files == 2
    assert sorted(result.replaced_files) == ["app/b.ts", "app/c.ts"]
    nodes = await _nodes_by_qn(db_session, repository.id)
    assert str(nodes["app.c.c"].id) in nodes["app.b.b"].callees

    assert (await sync(second)).processed_files == 0


async def test_restamp_of_a_renamed_go_module_keeps_its_children(
    db_session, tmp_path, caplog
):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    (checkout / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", "utf-8")
    (checkout / "main.go").write_text(
        "package main\n\ntype T struct{}\n\nfunc main() { T{}.Run() }\n", "utf-8"
    )
    (checkout / "run.go").write_text("package main\n\nfunc (T) Run() {}\n", "utf-8")
    head = _commit_all(checkout, "initial")

    repository = await _create_repo(db_session)
    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        commit_sha=head,
    )
    await db_session.commit()
    ids_before = {
        qn: node.id for qn, node in (await _nodes_by_qn(db_session, repository.id)).items()
    }

    # Rows written by an older extractor: the module had no `#module`
    # suffix, so its symbol_key no longer matches, and it carries no stamp.
    module = (await _nodes_by_qn(db_session, repository.id))["main#module"]
    module.qualified_name = "main"
    module.symbol_key = compute_symbol_key(
        language=GraphLanguage.GO, qualified_name="main", signature=module.signature
    )
    module.node_metadata = {
        k: v for k, v in module.node_metadata.items() if k != "extractor_stamp"
    }
    await db_session.commit()

    with caplog.at_level(logging.WARNING, logger="backend.app.graph.ingest"):
        result = await service.ingest_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout,
            last_commit=head,
            commit_sha=head,
        )
        await db_session.commit()

    assert result.replaced_files == ("main.go",)
    assert not [r for r in caplog.records if "persist_graph" in r.getMessage()]
    nodes = await _nodes_by_qn(db_session, repository.id)
    assert {qn: node.id for qn, node in nodes.items()} == ids_before
    assert nodes["main.T.Run"].parent_id == nodes["main.T"].id


async def test_class_header_change_keeps_its_methods(db_session, tmp_path, caplog):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    source = "export class B {}\nexport class A%s {\n  m() { return 1; }\n}\n"
    (checkout / "a.ts").write_text(source % "", "utf-8")
    first = _commit_all(checkout, "initial")

    repository = await _create_repo(db_session)
    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        commit_sha=first,
    )
    await db_session.commit()
    method_id = (await _nodes_by_qn(db_session, repository.id))["a.A.m"].id

    # A TS class signature is its header, so a new base rotates its key.
    (checkout / "a.ts").write_text(source % " extends B", "utf-8")
    second = _commit_all(checkout, "inherit")
    with caplog.at_level(logging.WARNING, logger="backend.app.graph.ingest"):
        await service.ingest_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout,
            last_commit=first,
            commit_sha=second,
        )
        await db_session.commit()

    assert not [r for r in caplog.records if "persist_graph" in r.getMessage()]
    nodes = await _nodes_by_qn(db_session, repository.id)
    assert nodes["a.A.m"].id == method_id
    assert nodes["a.A.m"].parent_id == nodes["a.A"].id


@pytest.mark.parametrize("full_walk", [False, True])
async def test_deleting_a_go_type_file_keeps_methods_in_other_files(
    db_session, tmp_path, full_walk
):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    (checkout / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", "utf-8")
    (checkout / "t.go").write_text("package main\n\ntype T struct{}\n", "utf-8")
    (checkout / "run.go").write_text("package main\n\nfunc (T) Run() {}\n", "utf-8")
    first = _commit_all(checkout, "initial")

    repository = await _create_repo(db_session)
    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        commit_sha=first,
    )
    await db_session.commit()
    run_id = (await _nodes_by_qn(db_session, repository.id))["main.T.Run"].id

    (checkout / "t.go").unlink()
    if full_walk:  # a root go.mod change escalates to the full walk and its prune
        (checkout / "go.mod").write_text("module example.com/app\n\ngo 1.23\n", "utf-8")
    second = _commit_all(checkout, "drop the type")
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        last_commit=first,
        commit_sha=second,
    )
    await db_session.commit()

    nodes = await _nodes_by_qn(db_session, repository.id)
    assert "main.T" not in nodes
    assert nodes["main.T.Run"].id == run_id
    assert nodes["main.T.Run"].parent_id is None


async def test_full_walk_keeps_committed_files_when_it_dies(
    db_session, tmp_path, monkeypatch
):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    for name in "abcdef":
        (checkout / f"{name}.py").write_text(f"def {name}() -> int:\n    return 1\n", "utf-8")
    head = _commit_all(checkout, "initial")

    repository = await _create_repo(db_session)
    service = GraphIngestService()
    monkeypatch.setattr(ingest_module, "_PARSE_COMMIT_EVERY", 2)
    persist = service._parse_and_persist

    async def dies_on_e(**kwargs):
        if kwargs["relative_path"].name == "e.py":
            raise RuntimeError("step timeout")
        return await persist(**kwargs)

    monkeypatch.setattr(service, "_parse_and_persist", dies_on_e)
    with pytest.raises(RuntimeError):
        await service.ingest_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout,
            commit_sha=head,
        )
    await db_session.rollback()
    assert {"a", "b", "c", "d"} <= set(await _nodes_by_qn(db_session, repository.id))

    # A failed sync does not advance last_commit, so the retry walks again.
    monkeypatch.setattr(service, "_parse_and_persist", persist)
    result = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
        commit_sha=head,
    )
    await db_session.commit()
    assert result.replaced_files == ("e.py", "f.py")


async def test_callers_arrays_match_the_edges_after_every_sync(db_session, tmp_path):
    checkout = tmp_path / "checkout"
    _init_git_repo(checkout)
    repository = await _create_repo(db_session)
    service = GraphIngestService()
    files = {
        "hub.py": "def h1() -> int:\n    return 1\n\n\ndef h2() -> int:\n    return 2\n",
        "a.py": "from hub import h1, h2\n\n\ndef a1() -> int:\n    return h1() + h2()\n",
        "b.py": (
            "from hub import h1\n\n\ndef b1() -> int:\n    return h1()\n\n\n"
            "def b2() -> int:\n    return h1()\n"
        ),
        "c.py": "from hub import h2\n\n\ndef c1() -> int:\n    return h2()\n",
    }
    steps = [
        {},
        {"a.py": "from hub import h1\n\n\ndef a1() -> int:\n    return h1()\n"},
        {"b.py": "from hub import h1\n\n\ndef b1() -> int:\n    return h1()\n"},
        {"c.py": None},
        {"hub.py": "def h1() -> int:\n    return 1\n"},
        {"hub.py": files["hub.py"]},
        # The only caller of h1 in its file is deleted, not rewritten.
        {"a.py": "def a3() -> int:\n    return 3\n"},
    ]
    last = None
    for step, change in enumerate(steps):
        files.update(change)
        for name, text in files.items():
            if text is None:
                (checkout / name).unlink(missing_ok=True)
            else:
                (checkout / name).write_text(text, "utf-8")
        head = _commit_all(checkout, f"step {step}")
        await service.ingest_checkout(
            session=db_session,
            repository_id=repository.id,
            checkout_path=checkout,
            last_commit=last,
            commit_sha=head,
        )
        await db_session.commit()
        last = head

        edges = (
            await db_session.execute(
                select(CodeEdge.source_node_id, CodeEdge.target_node_id).where(
                    CodeEdge.repository_id == repository.id,
                    CodeEdge.edge_type == "calls",
                    CodeEdge.target_node_id.is_not(None),
                )
            )
        ).all()
        nodes = await _nodes_by_qn(db_session, repository.id)
        assert nodes["hub.h1"].callers, step
        for node in nodes.values():
            assert sorted(node.callees) == sorted(
                str(t) for s, t in edges if s == node.id
            ), (step, node.qualified_name)
            assert sorted(node.callers) == sorted(
                str(s) for s, t in edges if t == node.id
            ), (step, node.qualified_name)
