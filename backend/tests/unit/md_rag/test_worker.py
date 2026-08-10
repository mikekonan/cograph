from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.app.config import Settings
from backend.app.db.session import SessionManager
from backend.app.md_rag.worker import embed_md_collection, resolve_md_links
from backend.app.models.enums import MdJobStatus


@pytest.fixture
def fake_session_manager() -> MagicMock:
    manager = MagicMock(spec=SessionManager)
    session = AsyncMock()
    manager.session = MagicMock()
    manager.session.return_value.__aenter__ = AsyncMock(return_value=session)
    manager.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return manager


async def test_embed_md_collection_success(fake_session_manager: MagicMock) -> None:
    collection_id = uuid4()
    job_id = uuid4()
    ctx: dict[str, Any] = {
        "session_manager": fake_session_manager,
        "settings": MagicMock(spec=Settings, embedding=MagicMock(batch_size=10)),
    }

    with patch("backend.app.md_rag.worker.build_runtime_providers", new=AsyncMock()) as mock_providers:
        mock_providers.return_value = MagicMock(embed_provider=MagicMock(model="test-model"))
        with patch("backend.app.md_rag.worker.MdChunkEmbedderService") as mock_embedder_cls:
            mock_embedder = MagicMock()
            mock_embedder.embed_collection = AsyncMock(
                return_value=MagicMock(embedded_nodes=5, skipped_nodes=2, model="test-model")
            )
            mock_embedder_cls.return_value = mock_embedder
            with patch("backend.app.md_rag.worker.MdJobTracker") as mock_tracker:
                mock_tracker.update_status = AsyncMock()
                mock_tracker.update_progress = AsyncMock()

                result = await embed_md_collection(ctx, str(collection_id), str(job_id))

    assert result["embedded_nodes"] == 5
    assert result["skipped_nodes"] == 2


async def test_embed_md_collection_error(fake_session_manager: MagicMock) -> None:
    collection_id = uuid4()
    job_id = uuid4()
    ctx: dict[str, Any] = {
        "session_manager": fake_session_manager,
        "settings": MagicMock(spec=Settings, embedding=MagicMock(batch_size=10)),
    }

    with patch("backend.app.md_rag.worker.build_runtime_providers", new=AsyncMock()) as mock_providers:
        mock_providers.return_value = MagicMock(embed_provider=MagicMock(model="test-model"))
        with patch("backend.app.md_rag.worker.MdChunkEmbedderService") as mock_embedder_cls:
            mock_embedder = MagicMock()
            mock_embedder.embed_collection = AsyncMock(side_effect=RuntimeError("embed failed"))
            mock_embedder_cls.return_value = mock_embedder
            with patch("backend.app.md_rag.worker.MdJobTracker") as mock_tracker:
                mock_tracker.update_status = AsyncMock()
                mock_tracker.update_progress = AsyncMock()

                with pytest.raises(RuntimeError, match="embed failed"):
                    await embed_md_collection(ctx, str(collection_id), str(job_id))

    error_calls = [
        c for c in mock_tracker.update_status.call_args_list
        if c.kwargs.get("status") == MdJobStatus.ERROR
    ]
    assert len(error_calls) == 1


async def test_resolve_md_links_success(fake_session_manager: MagicMock) -> None:
    collection_id = uuid4()
    job_id = uuid4()
    ctx: dict[str, Any] = {
        "session_manager": fake_session_manager,
    }

    with patch("backend.app.md_rag.worker.MdLinkResolver") as mock_resolver_cls:
        mock_resolver = MagicMock()
        mock_resolver.resolve_collection = AsyncMock(return_value=3)
        mock_resolver_cls.return_value = mock_resolver
        with patch("backend.app.md_rag.worker.MdJobTracker") as mock_tracker:
            mock_tracker.update_status = AsyncMock()
            mock_tracker.update_progress = AsyncMock()

            result = await resolve_md_links(ctx, str(collection_id), str(job_id))

    assert result["resolved"] == 3
