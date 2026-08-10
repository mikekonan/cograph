from __future__ import annotations

from sqlalchemy import select

from backend.app.graph.ingest import GraphIngestService
from backend.app.models.code_edge import CodeEdge
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile


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


async def _count_source_files(db_session, repository_id):
    return len(
        (
            await db_session.scalars(
                select(SourceFile).where(SourceFile.repository_id == repository_id)
            )
        ).all()
    )


async def _nodes_by_qn(db_session, repository_id):
    return {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository_id)
            )
        ).all()
    }


async def _edges(db_session, repository_id):
    return list(
        (
            await db_session.scalars(
                select(CodeEdge).where(CodeEdge.repository_id == repository_id)
            )
        ).all()
    )


async def test_scenario_a_modify_one_file_preserves_unrelated_nodes(
    db_session,
    tmp_path,
):
    repository = await _create_repo(db_session)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "alpha.py").write_text(
        "def alpha() -> int:\n    return 1\n", encoding="utf-8"
    )
    (checkout / "beta.py").write_text(
        "def beta() -> int:\n    return 2\n", encoding="utf-8"
    )

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    before = await _nodes_by_qn(db_session, repository.id)
    alpha_id_before = before["alpha.alpha"].id
    beta_id_before = before["beta.beta"].id

    # Change body of alpha only — signature unchanged, symbol-stable UUID preserved
    (checkout / "alpha.py").write_text(
        "def alpha() -> int:\n    return 42\n", encoding="utf-8"
    )
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    after = await _nodes_by_qn(db_session, repository.id)
    assert after["alpha.alpha"].id == alpha_id_before
    assert after["beta.beta"].id == beta_id_before
    # Content on alpha reflects the new body
    assert "42" in after["alpha.alpha"].content


async def test_scenario_b_delete_file_removes_source_and_nodes(
    db_session,
    tmp_path,
):
    repository = await _create_repo(db_session)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "keep.py").write_text("def keep() -> int:\n    return 1\n", "utf-8")
    (checkout / "gone.py").write_text("def gone() -> int:\n    return 2\n", "utf-8")

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    assert await _count_source_files(db_session, repository.id) == 2

    (checkout / "gone.py").unlink()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    assert await _count_source_files(db_session, repository.id) == 1
    nodes = await _nodes_by_qn(db_session, repository.id)
    assert "gone" not in nodes
    assert "gone.gone" not in nodes
    assert "keep" in nodes
    assert "keep.keep" in nodes


async def test_scenario_d_resurrect_file_rebinds_inbound_edges(
    db_session,
    tmp_path,
):
    repository = await _create_repo(db_session)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "utils.py").write_text(
        "def helper() -> int:\n    return 1\n", "utf-8"
    )
    (checkout / "caller.py").write_text(
        "from utils import helper\n\ndef go() -> int:\n    return helper()\n",
        "utf-8",
    )

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    before = await _nodes_by_qn(db_session, repository.id)
    caller_before = before["caller.go"]
    assert caller_before.callees  # edge resolved

    # Delete utils.py — caller.go still exists, edge becomes unresolved (target NULL)
    (checkout / "utils.py").unlink()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    middle_edges = await _edges(db_session, repository.id)
    unresolved_calls = [
        edge
        for edge in middle_edges
        if edge.edge_type == "calls" and edge.target_node_id is None
    ]
    assert unresolved_calls, "caller should have an unresolved call after target deletion"
    assert unresolved_calls[0].target_qualified_name  # name survives

    # Resurrect utils.py with the same symbol — edge should rebind
    (checkout / "utils.py").write_text(
        "def helper() -> int:\n    return 1\n", "utf-8"
    )
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    final_edges = await _edges(db_session, repository.id)
    still_unresolved = [
        edge
        for edge in final_edges
        if edge.edge_type == "calls" and edge.target_node_id is None
    ]
    assert not still_unresolved, "edges should re-resolve when target symbol is back"


async def test_scenario_f_add_module_constant(db_session, tmp_path):
    repository = await _create_repo(db_session)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "conf.py").write_text(
        "def helper() -> int:\n    return 1\n", "utf-8"
    )

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    before = await _nodes_by_qn(db_session, repository.id)
    assert "conf.NEW_CONSTANT" not in before
    helper_id_before = before["conf.helper"].id

    (checkout / "conf.py").write_text(
        "NEW_CONSTANT = 42\n\ndef helper() -> int:\n    return 1\n", "utf-8"
    )
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    after = await _nodes_by_qn(db_session, repository.id)
    assert "conf.NEW_CONSTANT" in after
    constant_node = after["conf.NEW_CONSTANT"]
    assert constant_node.node_type is CodeNodeType.CONSTANT
    assert constant_node.role == "constant"
    assert "42" in (constant_node.signature or "")
    # Unrelated symbol kept its UUID
    assert after["conf.helper"].id == helper_id_before


async def test_scenario_g_change_constant_value_rotates_symbol_uuid(
    db_session, tmp_path
):
    repository = await _create_repo(db_session)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "version.py").write_text('API_VERSION = "v1"\n', "utf-8")

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    before = await _nodes_by_qn(db_session, repository.id)
    old_id = before["version.API_VERSION"].id
    old_symbol_key = before["version.API_VERSION"].symbol_key

    (checkout / "version.py").write_text('API_VERSION = "v2"\n', "utf-8")
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    after = await _nodes_by_qn(db_session, repository.id)
    new_node = after["version.API_VERSION"]
    # Value is part of the constant's signature, so symbol_key changes and UUID rotates
    assert new_node.symbol_key != old_symbol_key
    assert new_node.id != old_id
    assert "v2" in (new_node.signature or "")


async def test_scenario_h_bulk_modify_touches_only_changed_files(
    db_session, tmp_path
):
    repository = await _create_repo(db_session)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for index in range(10):
        (checkout / f"mod_{index:02d}.py").write_text(
            f"def f_{index}() -> int:\n    return {index}\n", "utf-8"
        )

    service = GraphIngestService()
    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    before_files = {
        sf.file_path: sf.content_hash
        for sf in (
            await db_session.scalars(
                select(SourceFile).where(SourceFile.repository_id == repository.id)
            )
        ).all()
    }
    assert len(before_files) == 10

    # Modify half of the files by bumping the return value
    for index in range(0, 10, 2):
        (checkout / f"mod_{index:02d}.py").write_text(
            f"def f_{index}() -> int:\n    return {index + 100}\n", "utf-8"
        )

    await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()

    after_files = {
        sf.file_path: sf.content_hash
        for sf in (
            await db_session.scalars(
                select(SourceFile).where(SourceFile.repository_id == repository.id)
            )
        ).all()
    }
    changed = [
        path
        for path in after_files
        if after_files[path] != before_files.get(path)
    ]
    assert len(changed) == 5
    assert all(int(path.split("_")[1].split(".")[0]) % 2 == 0 for path in changed)


async def test_scenario_unchanged_file_is_skipped_on_reingest(
    db_session, tmp_path
):
    repository = await _create_repo(db_session)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "a.py").write_text("def helper() -> int:\n    return 1\n", "utf-8")

    service = GraphIngestService()
    first = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()
    assert first.processed_files == 1

    second = await service.ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()
    assert second.processed_files == 0
    assert second.inserted_nodes == 0
