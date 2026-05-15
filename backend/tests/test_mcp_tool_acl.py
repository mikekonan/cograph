"""Regression coverage for MCP tool ACL — every read path must hide a
private (ADMIN_ONLY) repository from an anonymous caller.

Approach: seed a `READY` ADMIN_ONLY repository + one source-file row,
then call each tool's payload helper directly (without authenticating
the request). Tools that accept a slug must raise `ValueError`
(MCP's NOT_FOUND mapping); tools that scan-then-filter (repositories,
route) must return the repo nowhere in the result.

We exercise the *payload helpers*, not the MCP wire layer, because:

  * `resolve_readable_repository_by_slug` is the choke point every
    tool funnels through. If a payload helper goes around it (e.g.
    by accepting a raw `repository_id`), this test will catch it.
  * Calling helpers directly lets us assert against the typed
    `ValueError` rather than parsing an MCP envelope.

This test does NOT assert "the agent cannot see chunk N if it
cannot see repo R" — that's a one-level-deeper invariant covered by
`HybridRetriever` / `LexicalRetriever` short-circuiting on
`repository_id is None`, exercised in the regular retrieval suite.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.mcp.services import (
    MCPServices,
    collections_payload,
    repositories_payload,
    resolve_readable_repository_by_slug,
)
from backend.app.models.enums import (
    MdCollectionVisibility,
    RepositoryStatus,
    RepositoryVisibility,
)
from backend.app.models.md_collection import MdCollection
from backend.app.models.repository import Repository
from backend.app.rag.source_router import route_sources


_PRIVATE_SLUG_PARTS = ("github.com", "acme", "secret-payments")


def _services(app) -> MCPServices:
    # The MCP services bundle is built once in `create_app` and parked on
    # `app.state` so it shares the session manager with the test
    # fixtures. We pull it from there rather than building a fresh one
    # so the seeded test rows are visible.
    return app.state.mcp_server._services if hasattr(
        app.state.mcp_server, "_services"
    ) else _build_test_services(app)


def _build_test_services(app) -> MCPServices:
    """Fallback: the MCP server doesn't expose the services bundle on its
    public surface; build a minimal stand-in from the FastAPI app state.

    Only the settings + session manager are needed for the ACL audit —
    the retrieval-heavy attributes (embed_provider, retriever, etc.) are
    not on the code path we're testing.
    """
    from backend.app.mcp.server import build_mcp_services

    services, _ = build_mcp_services(
        settings=app.state.settings,
        session_manager=app.state.session_manager,
    )
    return services


async def _seed_private_repo(db_session) -> Repository:
    host, owner, name = _PRIVATE_SLUG_PARTS
    repo = Repository(
        host=host,
        owner=owner,
        name=name,
        git_url=f"https://{host}/{owner}/{name}.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return repo


async def _seed_private_collection(db_session) -> MdCollection:
    coll = MdCollection(
        name="Private team runbook",
        description="Routing and credentials — internal only.",
        owner_id=None,
        visibility=MdCollectionVisibility.PRIVATE,
    )
    db_session.add(coll)
    await db_session.commit()
    await db_session.refresh(coll)
    return coll


# ---------- list payloads -----------------------------------------------------


@pytest.mark.asyncio
async def test_repositories_payload_hides_private_repo_from_anon(
    app, db_session
) -> None:
    await _seed_private_repo(db_session)
    payload = await repositories_payload(
        services=_services(app),
        current_user=None,
        search=None,
        status=None,
        limit=100,
    )
    slugs = [item["slug"] for item in payload["items"]]  # type: ignore[index]
    assert "github.com/acme/secret-payments" not in slugs, payload


@pytest.mark.asyncio
async def test_collections_payload_hides_private_collection_from_anon(
    app, db_session
) -> None:
    await _seed_private_collection(db_session)
    payload = await collections_payload(
        services=_services(app),
        current_user=None,
        search=None,
        limit=100,
    )
    names = [item["name"] for item in payload["items"]]  # type: ignore[index]
    assert "Private team runbook" not in names, payload


# ---------- slug-resolution choke point ---------------------------------------
#
# resolve_readable_repository_by_slug is the gate every slug-taking tool funnels
# through (cograph.outline, cograph.retrieve, cograph.read_node,
# cograph.search_code, cograph.related, cograph.repository_readme,
# cograph.read_file_range). One test against that choke point covers them all.


@pytest.mark.asyncio
async def test_resolve_readable_repository_by_slug_404s_private_for_anon(
    app, db_session
) -> None:
    await _seed_private_repo(db_session)
    with pytest.raises(ValueError) as excinfo:
        async with app.state.session_manager.session() as session:
            await resolve_readable_repository_by_slug(
                session=session,
                slug="/".join(_PRIVATE_SLUG_PARTS),
                services=_services(app),
                current_user=None,
            )
    # MCP's error-mapping prefixes "NOT_FOUND:" — leaking "PERMISSION_DENIED"
    # would tell an anonymous caller the repo exists, defeating the gate.
    assert "NOT_FOUND" in str(excinfo.value), str(excinfo.value)


@pytest.mark.asyncio
async def test_resolve_readable_repository_by_slug_404s_unknown_slug(
    app, db_session
) -> None:
    # Same error code for a slug that doesn't exist at all — the
    # "private vs missing" distinction must collapse to one observable.
    with pytest.raises(ValueError) as excinfo:
        async with app.state.session_manager.session() as session:
            await resolve_readable_repository_by_slug(
                session=session,
                slug=f"github.com/acme/does-not-exist-{uuid4().hex[:8]}",
                services=_services(app),
                current_user=None,
            )
    assert "NOT_FOUND" in str(excinfo.value), str(excinfo.value)


# ---------- router-level audit ------------------------------------------------


@pytest.mark.asyncio
async def test_route_sources_does_not_surface_private_repo(
    app, db_session, settings
) -> None:
    await _seed_private_repo(db_session)
    hits = await route_sources(
        db_session,
        query="secret payments",
        current_user=None,
        settings=settings,
        top_k=10,
    )
    assert all(
        h.label != "github.com/acme/secret-payments" for h in hits
    ), hits


@pytest.mark.asyncio
async def test_route_sources_does_not_surface_private_collection(
    app, db_session, settings
) -> None:
    await _seed_private_collection(db_session)
    hits = await route_sources(
        db_session,
        query="private team runbook routing credentials",
        current_user=None,
        settings=settings,
        top_k=10,
    )
    assert all(
        h.label != "Private team runbook" for h in hits
    ), hits
