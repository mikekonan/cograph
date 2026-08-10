"""Regression tests for CodeEmbedderService skip/re-embed logic.

Covers:
- content_hash unchanged + same model + same neighbor_hash → skipped (no re-embed)
- content_hash unchanged + model changed → re-embedded (Task 1 regression)
- content_hash changed + same model → re-embedded (existing behaviour)
- neighbor_hash changed (caller renamed) → re-embedded even if content_hash same
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.graph._chunking import IN_CHUNK_SIZE
from backend.app.llm.code_embedder import CodeEmbedderService, _neighbor_hash, _node_text
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIMS = 1536


def _make_node(
    *,
    node_id: uuid.UUID | None = None,
    repository_id: uuid.UUID | None = None,
    content_hash: str = "abc123",
    qualified_name: str = "module.fn",
    callers: list[str] | None = None,
    callees: list[str] | None = None,
) -> CodeNode:
    node = MagicMock(spec=CodeNode)
    node.id = node_id or uuid.uuid4()
    node.repository_id = repository_id or uuid.uuid4()
    node.content_hash = content_hash
    node.node_type = "function"
    node.qualified_name = qualified_name
    node.signature = "def fn() -> None"
    node.doc_comment = None
    node.content = "pass"
    node.callers = callers or []
    node.callees = callees or []
    return node


def _make_embedding(
    *,
    code_node_id: uuid.UUID,
    model: str = "text-embedding-3-small",
    content_hash: str = "abc123",
    neighbor_hash: str = "",
) -> CodeEmbedding:
    emb = MagicMock(spec=CodeEmbedding)
    emb.code_node_id = code_node_id
    emb.model = model
    emb.content_hash = content_hash
    emb.neighbor_hash = neighbor_hash
    return emb


def _make_session(
    *,
    nodes: list[CodeNode],
    embeddings: list[CodeEmbedding],
) -> AsyncMock:
    """Return a minimal fake AsyncSession."""
    session = AsyncMock()

    async def _scalars(stmt):
        # First call → nodes, subsequent calls → embeddings.
        result = MagicMock()
        if not hasattr(_scalars, "_called"):
            _scalars._called = True
            result.all.return_value = nodes
        else:
            result.all.return_value = embeddings
        return result

    session.scalars = _scalars
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_model_same_hash_is_skipped():
    """No re-embed when content_hash, model, and neighbor_hash all match."""
    node_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    node = _make_node(node_id=node_id, repository_id=repo_id, content_hash="hash1")
    # Compute the expected neighbor_hash for a node with no callers/callees.
    expected_nb = _neighbor_hash(node, {str(node_id): "module.fn"})
    emb = _make_embedding(
        code_node_id=node_id,
        model="fake-embed-v1",
        content_hash="hash1",
        neighbor_hash=expected_nb,
    )

    provider = FakeEmbedProvider(dims=_DIMS)
    service = CodeEmbedderService(provider, batch_size=256)
    session = _make_session(nodes=[node], embeddings=[emb])

    result = await service.embed_repository(session=session, repository_id=repo_id)

    assert result.skipped_nodes == 1
    assert result.embedded_nodes == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_model_change_triggers_reembed():
    """When provider model differs from stored model, node must be re-embedded
    even if content_hash is identical — regression for Task 1 (RAG poisoning)."""
    node_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    node = _make_node(node_id=node_id, repository_id=repo_id, content_hash="hash1")
    # Existing embedding was produced by a *different* model.
    emb = _make_embedding(
        code_node_id=node_id,
        model="text-embedding-3-small",
        content_hash="hash1",
    )
    # Provider now uses a different model.
    provider = FakeEmbedProvider(dims=_DIMS)
    # Monkey-patch the model property to return the new model name.
    # Restore the original after the test to avoid leaking the class-level patch.
    original_model_prop = FakeEmbedProvider.__dict__["model"]
    type(provider).model = property(lambda self: "text-embedding-3-large")
    try:
        service = CodeEmbedderService(provider, batch_size=256)
        session = _make_session(nodes=[node], embeddings=[emb])

        result = await service.embed_repository(session=session, repository_id=repo_id)

        assert result.embedded_nodes == 1
        assert result.skipped_nodes == 0
        assert session.execute.call_count == 1  # one UPDATE for the re-embedded node
    finally:
        type(provider).model = original_model_prop


@pytest.mark.asyncio
async def test_content_hash_change_triggers_reembed():
    """Changed content_hash forces re-embed regardless of model."""
    node_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    node = _make_node(node_id=node_id, repository_id=repo_id, content_hash="new_hash")
    emb = _make_embedding(
        code_node_id=node_id,
        model="fake-embed-v1",
        content_hash="old_hash",
    )

    provider = FakeEmbedProvider(dims=_DIMS)
    service = CodeEmbedderService(provider, batch_size=256)
    session = _make_session(nodes=[node], embeddings=[emb])

    result = await service.embed_repository(session=session, repository_id=repo_id)

    assert result.embedded_nodes == 1
    assert result.skipped_nodes == 0
    assert session.execute.call_count == 1  # one UPDATE for the re-embedded node


# ---------------------------------------------------------------------------
# Graph enrichment — callers/callees qualified names in embed text
# ---------------------------------------------------------------------------


def test_embed_text_includes_caller_qualified_name():
    caller_id = uuid.uuid4()
    callee_id = uuid.uuid4()
    caller = _make_node(node_id=caller_id, qualified_name="pkg.caller_fn")
    callee = _make_node(
        node_id=callee_id,
        qualified_name="pkg.callee_fn",
        callers=[str(caller_id)],
    )
    id_to_qname = {str(caller.id): caller.qualified_name, str(callee.id): callee.qualified_name}

    text = _node_text(callee, id_to_qname)

    assert "callers: pkg.caller_fn" in text


def test_embed_text_includes_callee_qualified_name():
    caller_id = uuid.uuid4()
    callee_id = uuid.uuid4()
    callee = _make_node(node_id=callee_id, qualified_name="pkg.callee_fn")
    caller = _make_node(
        node_id=caller_id,
        qualified_name="pkg.caller_fn",
        callees=[str(callee_id)],
    )
    id_to_qname = {str(caller.id): caller.qualified_name, str(callee.id): callee.qualified_name}

    text = _node_text(caller, id_to_qname)

    assert "callees: pkg.callee_fn" in text


def test_embed_text_handles_missing_ids_gracefully():
    node_id = uuid.uuid4()
    missing_id = uuid.uuid4()
    node = _make_node(
        node_id=node_id,
        qualified_name="pkg.fn",
        callers=[str(missing_id)],
        callees=[str(missing_id)],
    )
    id_to_qname = {str(node.id): node.qualified_name}

    text = _node_text(node, id_to_qname)

    assert "callers:" not in text
    assert "callees:" not in text


# ---------------------------------------------------------------------------
# neighbor_hash — re-embed when caller/callee set changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reembeds_when_caller_changes():
    """Node A is re-embedded when its caller B is renamed, even if A's content
    is unchanged.  The neighbor_hash captures B's qualified_name, so renaming B
    produces a different digest and bypasses the skip guard.
    """
    repo_id = uuid.uuid4()
    node_a_id = uuid.uuid4()
    node_b_id = uuid.uuid4()

    # B is a caller of A.
    node_a = _make_node(
        node_id=node_a_id,
        repository_id=repo_id,
        qualified_name="pkg.fn_a",
        content_hash="hash_a",
        callers=[str(node_b_id)],
    )
    node_b = _make_node(
        node_id=node_b_id,
        repository_id=repo_id,
        qualified_name="pkg.fn_b_renamed",  # B was renamed
        content_hash="hash_b",
    )

    # Existing embedding for A was computed when B was still "pkg.fn_b_old".
    id_to_qname_old = {str(node_b_id): "pkg.fn_b_old"}
    old_nb_hash = _neighbor_hash(node_a, id_to_qname_old)

    emb_a = _make_embedding(
        code_node_id=node_a_id,
        model="fake-embed-v1",
        content_hash="hash_a",
        neighbor_hash=old_nb_hash,
    )
    emb_b = _make_embedding(
        code_node_id=node_b_id,
        model="fake-embed-v1",
        content_hash="hash_b",
        neighbor_hash=_neighbor_hash(node_b, {str(node_b_id): "pkg.fn_b_renamed"}),
    )

    provider = FakeEmbedProvider(dims=_DIMS)
    service = CodeEmbedderService(provider, batch_size=256)
    session = _make_session(nodes=[node_a, node_b], embeddings=[emb_a, emb_b])

    result = await service.embed_repository(session=session, repository_id=repo_id)

    # A must be re-embedded (neighbor changed); B is unchanged.
    assert result.embedded_nodes >= 1
    assert session.execute.call_count >= 1  # at least one UPDATE for A


@pytest.mark.asyncio
async def test_skips_when_all_match_including_neighbor_hash():
    """Skip when content_hash, model, AND neighbor_hash all match — covers the
    updated skip predicate now that neighbor_hash is part of the cache key.
    """
    repo_id = uuid.uuid4()
    node_id = uuid.uuid4()
    caller_id = uuid.uuid4()

    node = _make_node(
        node_id=node_id,
        repository_id=repo_id,
        qualified_name="pkg.fn",
        content_hash="stable_hash",
        callers=[str(caller_id)],
    )
    id_to_qname = {str(node_id): "pkg.fn", str(caller_id): "pkg.caller"}
    nb = _neighbor_hash(node, id_to_qname)

    emb = _make_embedding(
        code_node_id=node_id,
        model="fake-embed-v1",
        content_hash="stable_hash",
        neighbor_hash=nb,
    )

    # Add a stub for the caller node so the session returns both.
    caller_node = _make_node(
        node_id=caller_id,
        repository_id=repo_id,
        qualified_name="pkg.caller",
        content_hash="caller_hash",
    )
    caller_emb = _make_embedding(
        code_node_id=caller_id,
        model="fake-embed-v1",
        content_hash="caller_hash",
        neighbor_hash=_neighbor_hash(caller_node, id_to_qname),
    )

    provider = FakeEmbedProvider(dims=_DIMS)
    service = CodeEmbedderService(provider, batch_size=256)
    session = _make_session(nodes=[node, caller_node], embeddings=[emb, caller_emb])

    result = await service.embed_repository(session=session, repository_id=repo_id)

    assert result.skipped_nodes == 2
    assert result.embedded_nodes == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_existing_embedding_lookup_chunks_to_stay_under_placeholder_cap():
    """Regression for the asyncpg "the number of query arguments cannot
    exceed 32767" crash hit on large monorepos.

    The CodeEmbedding-by-code_node_id lookup must split into batches of
    at most IN_CHUNK_SIZE so a repository with >32k nodes doesn't take
    down the whole indexing pass with a single oversized statement.

    We verify by feeding more nodes than IN_CHUNK_SIZE, capturing the
    bind values of every `select(CodeEmbedding)` issued, and asserting
    that (a) no single statement carries more than IN_CHUNK_SIZE ids and
    (b) the union of ids across statements covers every node.
    """
    repo_id = uuid.uuid4()
    n_nodes = IN_CHUNK_SIZE + 25  # force at least two chunks
    nodes = [
        _make_node(repository_id=repo_id, qualified_name=f"pkg.fn_{i}")
        for i in range(n_nodes)
    ]
    node_id_set = {n.id for n in nodes}

    embedding_lookup_ids: list[list[uuid.UUID]] = []

    async def _scalars(stmt):
        result = MagicMock()
        # First call is the all-nodes SELECT — return every node.
        if not hasattr(_scalars, "_seen_nodes"):
            _scalars._seen_nodes = True
            result.all.return_value = nodes
            return result
        # Subsequent calls are the chunked CodeEmbedding lookups.
        # SQLAlchemy holds the IN-list on whereclause.right.value as the
        # original Python list — read it back to prove each chunk stays
        # under the placeholder cap.
        where = stmt.whereclause
        ids_in_stmt = list(where.right.value)
        embedding_lookup_ids.append(ids_in_stmt)
        # No pre-existing embeddings — every node is brand new.
        result.all.return_value = []
        return result

    session = AsyncMock()
    session.scalars = _scalars
    session.commit = AsyncMock()
    session.execute = AsyncMock()

    provider = FakeEmbedProvider(dims=_DIMS)
    service = CodeEmbedderService(provider, batch_size=64)

    result = await service.embed_repository(session=session, repository_id=repo_id)

    # Behavioural: every node ends up embedded once.
    assert result.embedded_nodes == n_nodes
    assert result.skipped_nodes == 0

    # Chunking invariants.
    assert len(embedding_lookup_ids) >= 2, (
        "expected the existing-embedding lookup to be split into multiple "
        f"chunks, got {len(embedding_lookup_ids)} statement(s)"
    )
    for chunk in embedding_lookup_ids:
        assert 0 < len(chunk) <= IN_CHUNK_SIZE, (
            f"chunk of size {len(chunk)} exceeds placeholder budget "
            f"{IN_CHUNK_SIZE}"
        )
    union = {uid for chunk in embedding_lookup_ids for uid in chunk}
    assert union == node_id_set, "chunked lookup must cover every node id exactly once"
