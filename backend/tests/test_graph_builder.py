from __future__ import annotations

from sqlalchemy import select

from backend.app.graph.builder import GraphBuilder
from backend.app.graph.extractor import GraphExtractor
from backend.app.graph.ingest_cache import GraphIngestCache
from backend.app.graph.parser import GraphParser
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository


async def test_graph_builder_persists_nodes_and_relationships(db_session):
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

    source_text = '''"""Module docs"""
import os

class UserService:
    def audit(self, user_id: str) -> None:
        return None

    def login(self, user_id: str) -> bool:
        helper(user_id)
        self.audit(user_id)
        return True


def helper(user_id: str) -> str:
    return normalize(user_id)
'''
    extracted = GraphExtractor().extract(
        GraphParser().parse_source(file_path="service.py", source_text=source_text)
    )

    result = await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted,
    )
    await db_session.commit()

    assert result.inserted_nodes == 5
    assert result.resolved_calls == 2
    assert result.unresolved_calls == 1

    persisted_nodes = {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    module_node = persisted_nodes["service"]
    class_node = persisted_nodes["service.UserService"]
    audit_node = persisted_nodes["service.UserService.audit"]
    login_node = persisted_nodes["service.UserService.login"]
    helper_node = persisted_nodes["service.helper"]

    assert module_node.node_type is CodeNodeType.MODULE
    assert class_node.parent_id == module_node.id
    assert audit_node.parent_id == class_node.id
    assert login_node.parent_id == class_node.id
    assert helper_node.parent_id == module_node.id

    assert module_node.node_metadata["imports"] == ["os"]
    assert str(helper_node.id) in login_node.callees
    assert str(audit_node.id) in login_node.callees
    assert str(login_node.id) in helper_node.callers
    assert str(login_node.id) in audit_node.callers
    assert helper_node.node_metadata["unresolved_calls"] == ["normalize"]


async def test_graph_builder_resolves_cross_file_calls_independent_of_ingest_order(
    db_session,
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

    parser = GraphParser()
    extractor = GraphExtractor()
    builder = GraphBuilder()

    caller_graph = extractor.extract(
        parser.parse_source(
            file_path="b.py",
            source_text="import a\n\ndef call() -> int:\n    return a.helper()\n",
        )
    )
    callee_graph = extractor.extract(
        parser.parse_source(
            file_path="a.py",
            source_text="def helper() -> int:\n    return 1\n",
        )
    )

    first_result = await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=caller_graph,
    )
    second_result = await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=callee_graph,
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

    caller_node = persisted_nodes["b.call"]
    helper_node = persisted_nodes["a.helper"]

    assert first_result.resolved_calls == 0
    assert first_result.unresolved_calls == 1
    # The second call doesn't add new edges, but it re-resolves the
    # dangling `b.call → a.helper` edge inserted by the first call. The
    # accumulated deltas across both calls (resolved=1, unresolved=0)
    # match the final repo-wide totals.
    assert second_result.resolved_calls == 1
    assert second_result.unresolved_calls == -1
    assert first_result.resolved_calls + second_result.resolved_calls == 1
    assert first_result.unresolved_calls + second_result.unresolved_calls == 0
    assert caller_node.callees == [str(helper_node.id)]
    assert helper_node.callers == [str(caller_node.id)]
    assert "unresolved_calls" not in caller_node.node_metadata


async def test_graph_builder_rebinds_inbound_edges_when_reindexing_target_file(
    db_session,
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

    parser = GraphParser()
    extractor = GraphExtractor()
    builder = GraphBuilder()

    helper_graph = extractor.extract(
        parser.parse_source(
            file_path="a.py",
            source_text="def helper() -> int:\n    return 1\n",
        )
    )
    caller_graph = extractor.extract(
        parser.parse_source(
            file_path="b.py",
            source_text="import a\n\ndef call() -> int:\n    return a.helper()\n",
        )
    )

    await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=helper_graph,
    )
    await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=caller_graph,
    )
    await db_session.commit()

    original_helper = await db_session.scalar(
        select(CodeNode).where(CodeNode.qualified_name == "a.helper")
    )
    assert original_helper is not None

    updated_helper_graph = extractor.extract(
        parser.parse_source(
            file_path="a.py",
            source_text="def helper() -> int:\n    return 2\n",
        )
    )
    await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=updated_helper_graph,
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

    caller_node = persisted_nodes["b.call"]
    helper_node = persisted_nodes["a.helper"]

    # Signature of a.helper did not change across reindex (only body did) so the
    # symbol-stable UUID is preserved — cross-file edges keep pointing to it
    # without any rebind work.
    assert helper_node.id == original_helper.id
    assert caller_node.callees == [str(helper_node.id)]
    assert helper_node.callers == [str(caller_node.id)]


async def test_graph_builder_resolves_relative_from_import_calls(db_session):
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

    parser = GraphParser()
    extractor = GraphExtractor()
    builder = GraphBuilder()

    helper_graph = extractor.extract(
        parser.parse_source(
            file_path="pkg/utils.py",
            source_text="def helper() -> int:\n    return 1\n",
        )
    )
    caller_graph = extractor.extract(
        parser.parse_source(
            file_path="pkg/service.py",
            source_text="from .utils import helper\n\ndef call() -> int:\n    return helper()\n",
        )
    )

    await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=helper_graph,
    )
    result = await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=caller_graph,
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

    caller_node = persisted_nodes["pkg.service.call"]
    helper_node = persisted_nodes["pkg.utils.helper"]

    assert result.resolved_calls == 1
    assert result.unresolved_calls == 0
    assert caller_node.callees == [str(helper_node.id)]
    assert helper_node.callers == [str(caller_node.id)]
    assert "unresolved_calls" not in caller_node.node_metadata


async def test_graph_builder_replaces_existing_nodes_for_same_file(db_session):
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

    parser = GraphParser()
    extractor = GraphExtractor()
    builder = GraphBuilder()

    first_graph = extractor.extract(
        parser.parse_source(
            file_path="service.py",
            source_text="def helper(value: str) -> str:\n    return value\n",
        )
    )
    second_graph = extractor.extract(
        parser.parse_source(
            file_path="service.py",
            source_text="def helper(value: str) -> str:\n    return normalize(value)\n",
        )
    )

    await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=first_graph,
    )
    await db_session.commit()

    first_ids = {
        node.qualified_name: node.id
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=second_graph,
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

    assert set(persisted_nodes) == {"service", "service.helper"}
    # Signature of helper() is unchanged across the two persists — the body
    # went from returning the input to calling normalize(). That is a body-only
    # change, so the symbol-stable UUID is preserved and metadata updates in
    # place.
    assert persisted_nodes["service.helper"].id == first_ids["service.helper"]
    assert persisted_nodes["service.helper"].node_metadata["unresolved_calls"] == ["normalize"]


async def _build_two_files(db_session, helper_source: str, caller_source: str):
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

    builder = GraphBuilder()
    for path, text in (("a.py", helper_source), ("caller.py", caller_source)):
        extracted = GraphExtractor().extract(
            GraphParser().parse_source(file_path=path, source_text=text)
        )
        await builder.persist_graph(
            session=db_session,
            repository_id=repository.id,
            extracted_graph=extracted,
        )
    await db_session.commit()

    nodes = {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }
    return repository, nodes


async def test_graph_builder_resolves_aliased_from_import(db_session):
    # Regression for H1: `from a import helper as h; h()` must resolve to a.helper.
    helper = "def helper() -> int:\n    return 1\n"
    caller = "from a import helper as h\n\ndef go() -> int:\n    return h()\n"
    _, nodes = await _build_two_files(db_session, helper, caller)
    helper_node = nodes["a.helper"]
    caller_node = nodes["caller.go"]
    assert caller_node.callees == [str(helper_node.id)]
    assert caller_node.node_metadata.get("unresolved_calls") is None


async def test_graph_builder_resolves_aliased_module_import(db_session):
    # Regression for H1: `import a as mod; mod.helper()` must resolve to a.helper.
    helper = "def helper() -> int:\n    return 1\n"
    caller = "import a as mod\n\ndef go() -> int:\n    return mod.helper()\n"
    _, nodes = await _build_two_files(db_session, helper, caller)
    helper_node = nodes["a.helper"]
    caller_node = nodes["caller.go"]
    assert caller_node.callees == [str(helper_node.id)]


async def test_graph_builder_collapses_typing_overload_stubs(db_session):
    # Regression for C1: @overload stubs share qualified_name with the
    # implementation. The old builder would try to INSERT them all and hit
    # UNIQUE (repository_id, qualified_name). Now dedup keeps the last
    # definition (the implementation) and stashes overload signatures.
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

    source_text = '''from typing import overload


@overload
def f(x: int) -> int: ...
@overload
def f(x: str) -> str: ...
def f(x):
    return x
'''
    extracted = GraphExtractor().extract(
        GraphParser().parse_source(file_path="m.py", source_text=source_text)
    )

    # The extractor emits three ExtractedNodes sharing qualified_name m.f.
    same_qn = [n for n in extracted.nodes if n.qualified_name == "m.f"]
    assert len(same_qn) == 3

    # The builder must not raise IntegrityError.
    result = await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted,
    )
    await db_session.commit()

    persisted = {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }
    assert "m.f" in persisted
    # One merged function node, not three.
    assert len([n for n in persisted.values() if n.qualified_name == "m.f"]) == 1
    overloads = persisted["m.f"].node_metadata.get("overloads") or []
    assert isinstance(overloads, list)
    assert len(overloads) == 2  # two stub signatures captured
    # Overall result counts reflect the deduped set.
    assert result.inserted_nodes == len(persisted)


async def test_graph_builder_resolves_go_package_calls_and_rebinds_method_parent(db_session):
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

    parser = GraphParser()
    extractor = GraphExtractor()
    builder = GraphBuilder()

    login_graph = extractor.extract(
        parser.parse_source(
            file_path="service/login.go",
            source_text="""package service

import localutils "pkg/utils"

func (s *UserService) Login(userID string) error {
    Helper(userID)
    s.audit(userID)
    localutils.Normalize(userID)
    return nil
}
""",
        )
    )
    utils_graph = extractor.extract(
        parser.parse_source(
            file_path="pkg/utils/utils.go",
            source_text="""package utils

func Normalize(userID string) string {
    return userID
}
""",
        )
    )
    user_graph = extractor.extract(
        parser.parse_source(
            file_path="service/user.go",
            source_text="""package service

type UserService struct{}

func (s *UserService) audit(userID string) string {
    return userID
}

func Helper(userID string) string {
    return userID
}
""",
        )
    )

    await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=login_graph,
    )
    await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=utils_graph,
    )
    await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=user_graph,
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

    user_service = persisted_nodes["service.UserService"]
    login_node = persisted_nodes["service.UserService.Login"]
    audit_node = persisted_nodes["service.UserService.audit"]
    helper_node = persisted_nodes["service.Helper"]
    normalize_node = persisted_nodes["pkg.utils.Normalize"]

    assert login_node.parent_id == user_service.id
    assert audit_node.parent_id == user_service.id
    assert set(login_node.callees) == {
        str(audit_node.id),
        str(helper_node.id),
        str(normalize_node.id),
    }
    assert audit_node.callers == [str(login_node.id)]
    assert helper_node.callers == [str(login_node.id)]
    assert normalize_node.callers == [str(login_node.id)]
    assert "unresolved_calls" not in login_node.node_metadata


async def test_graph_builder_preserves_go_module_node_when_stem_matches_symbol_name(
    db_session,
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

    extracted = GraphExtractor().extract(
        GraphParser().parse_source(
            file_path="currency/currency.go",
            source_text="""package currency

type currency struct{}
""",
        )
    )

    result = await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted,
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

    module_node = persisted_nodes["currency.currency#module"]
    symbol_node = persisted_nodes["currency.currency"]

    assert result.inserted_nodes == 2
    assert module_node.node_type is CodeNodeType.MODULE
    assert symbol_node.node_type is CodeNodeType.STRUCT
    assert symbol_node.parent_id == module_node.id
    assert module_node.source_file_id == symbol_node.source_file_id


async def test_graph_builder_preserves_go_module_node_when_stem_matches_function_name(
    db_session,
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

    extracted = GraphExtractor().extract(
        GraphParser().parse_source(
            file_path="currency/currency.go",
            source_text="""package currency

func currency() string {
    return "ok"
}
""",
        )
    )

    result = await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted,
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

    module_node = persisted_nodes["currency.currency#module"]
    symbol_node = persisted_nodes["currency.currency"]

    assert result.inserted_nodes == 2
    assert module_node.node_type is CodeNodeType.MODULE
    assert symbol_node.node_type is CodeNodeType.FUNCTION
    assert symbol_node.parent_id == module_node.id
    assert module_node.source_file_id == symbol_node.source_file_id


async def test_rebuild_relationships_scoped_survives_id_set_larger_than_chunk_size(
    db_session, monkeypatch
):
    """Regression: when the touched-id set exceeds the configured IN_CHUNK_SIZE,
    rebuild_relationships_scoped must still complete and rebuild every
    caller/callee link correctly. Models the production crash on a fresh
    sync of a large monorepo where the IN-list blew the asyncpg 32767
    placeholder cap (the underlying cause; here we shrink the cap to 3
    so the chunking is exercised against a small fixture).
    """
    from backend.app.graph import _chunking, builder as builder_module

    monkeypatch.setattr(_chunking, "IN_CHUNK_SIZE", 3)
    monkeypatch.setattr(builder_module, "chunked", _chunking.chunked)

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

    # 10 leaf helpers + 1 hub function that calls every helper → 11 nodes
    # and 10 CALLS edges. With IN_CHUNK_SIZE=3, the touched set (11 ids)
    # forces 4 chunks for each IN-list scan inside the resolver.
    helper_defs = "\n".join(f"def helper_{i}() -> int:\n    return {i}\n" for i in range(10))
    hub_def = (
        "def hub() -> int:\n"
        + "    return (\n"
        + " + ".join(f"helper_{i}()" for i in range(10))
        + "\n    )\n"
    )
    source_text = f"{helper_defs}\n{hub_def}"

    extracted = GraphExtractor().extract(
        GraphParser().parse_source(file_path="mod.py", source_text=source_text)
    )
    result = await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted,
    )
    await db_session.commit()

    assert result.resolved_calls == 10

    persisted_nodes = {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }
    hub_node = persisted_nodes["mod.hub"]
    helper_ids = {str(persisted_nodes[f"mod.helper_{i}"].id) for i in range(10)}
    # Every helper id must appear in the hub's callees array — if a
    # chunk got dropped, this set would be short.
    assert set(hub_node.callees) == helper_ids
    for i in range(10):
        assert persisted_nodes[f"mod.helper_{i}"].callers == [str(hub_node.id)]


async def test_graph_builder_shared_cache_resolves_cross_file_calls(db_session):
    """A second `persist_graph` call sharing the cache from the first must
    resolve into nodes the first call inserted without re-running the
    repo-wide CodeNode SELECT.
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

    parser = GraphParser()
    extractor = GraphExtractor()
    builder = GraphBuilder()

    helper_graph = extractor.extract(
        parser.parse_source(
            file_path="a.py",
            source_text="def helper() -> int:\n    return 1\n",
        )
    )
    caller_graph = extractor.extract(
        parser.parse_source(
            file_path="b.py",
            source_text="import a\n\ndef call() -> int:\n    return a.helper()\n",
        )
    )

    cache = GraphIngestCache()

    first = await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=helper_graph,
        cache=cache,
    )
    second = await builder.persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=caller_graph,
        cache=cache,
    )
    await db_session.commit()

    persisted = {
        node.qualified_name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }
    caller_node = persisted["b.call"]
    helper_node = persisted["a.helper"]

    assert first.resolved_calls == 0
    # Critical bit: the second call's edge resolver sees the helper that
    # the first call added — *via the cache*, not via a re-read from the
    # database.
    assert second.resolved_calls == 1
    assert caller_node.callees == [str(helper_node.id)]
    # And the cache reflects both inserts.
    assert helper_node.id in cache.node_by_id
    assert caller_node.id in cache.node_by_id
    assert cache.nodes_by_qn["a.helper"] is helper_node
    assert cache.nodes_by_qn["b.call"] is caller_node


async def test_graph_builder_persist_graph_flush_budget(db_session, monkeypatch):
    """Pin the flush budget for `persist_graph` so a future change can't
    silently re-introduce a per-step flush.
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

    extracted = GraphExtractor().extract(
        GraphParser().parse_source(
            file_path="m.py",
            source_text="def helper() -> int:\n    return 1\n",
        )
    )

    flush_count = 0
    original_flush = db_session.flush

    async def _counting_flush(*args, **kwargs):
        nonlocal flush_count
        flush_count += 1
        return await original_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", _counting_flush)

    await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted,
    )

    # On a fresh repo (no `existing_nodes_for_files`) persist_graph runs
    # 4 flushes: post-insert PK assignment, post-metadata write,
    # post-new-edge add_all, and the final commit-prep. Add one flush
    # per new SourceFile in `_upsert_source_files` (one here).
    assert flush_count <= 5
