"""Repository-doc endpoints backed by ``repo_documents``.

These routes expose markdown files that already exist inside the indexed
repository. They are separate from the generated wiki surface under
``/api/repos/:host/:owner/:name/wiki``.

- ``GET /api/repos/:host/:owner/:name/docs``       → build a grouped tree
  from ``RepoDocument`` rows for the repo and return
  ``{ items: DocTreeNode[] }``.
- ``GET /api/repos/:host/:owner/:name/docs/:slug`` → resolve the slug
  deterministically from ``file_path`` and return ``DocPage`` with
  ``related_nodes`` derived from chunk ``mentions``.

Derived fields:
- ``doc_type`` is inferred from the directory name, not stored in DB.
- ``sort_order`` is derived from alphabetical position within the group.
- ``parent_id`` on leaf nodes is a synthetic uuid5 of the group directory.
- Group nodes are synthetic (not stored); they carry ``slug`` prefixed with
  ``_group-`` so slug round-trips to real pages only.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.deps import get_current_user_optional, get_db_session, get_settings_dep
from backend.app.core.errors import ApiError
from backend.app.core.repository_access import get_readable_repository_by_slug
from backend.app.models.enums import RepositoryStatus
from backend.app.models.repo_document import RepoDocument
from backend.app.models.user import User
from backend.app.repo_docs.queries import WikiQueryService

router = APIRouter(prefix="/repos", tags=["docs"])

_wiki_query_service = WikiQueryService()


# ---------------------------------------------------------------------------
# Response models — must match FE DocTreeNode / DocPage exactly.
# ---------------------------------------------------------------------------


class DocTreeNodeResponse(BaseModel):
    id: UUID
    title: str
    slug: str
    doc_type: str
    sort_order: int
    parent_id: UUID | None
    file_path: str | None = None
    children: list["DocTreeNodeResponse"]

    model_config = {"from_attributes": True}


DocTreeNodeResponse.model_rebuild()


class DocTreeResponse(BaseModel):
    items: list[DocTreeNodeResponse]
    total: int


class RelatedNodeResponse(BaseModel):
    id: UUID
    name: str
    node_type: str
    file_path: str
    start_line: int
    end_line: int


class DocPageResponse(BaseModel):
    id: UUID
    title: str
    slug: str
    content: str
    doc_type: str
    sort_order: int
    parent_id: UUID | None
    related_nodes: list[RelatedNodeResponse]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{host}/{owner}/{name}/docs", response_model=DocTreeResponse)
async def get_docs_tree(
    host: str,
    owner: str,
    name: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> DocTreeResponse:
    """Return nested doc tree for a repo.

    Returns 409 REPO_NOT_READY if the repository is not yet indexed; the FE
    displays the repo-docs not-ready placeholder based on that error code.
    Raises 404 NOT_FOUND if the repo doesn't exist.
    Also returns ``total`` — the count of all ``RepoDocument`` rows for the repo,
    which equals the number of leaf doc pages.
    """
    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    if repository.status is not RepositoryStatus.READY:
        raise ApiError(409, "REPO_NOT_READY", "Repository is not ready yet")
    total = (
        await session.scalar(
            select(func.count(RepoDocument.id)).where(
                RepoDocument.repository_id == repository.id
            )
        )
    ) or 0
    nodes = await _wiki_query_service.list_docs_tree(
        session=session, repository_id=repository.id
    )
    return DocTreeResponse(
        items=[_tree_node_to_response(n) for n in nodes],
        total=total,
    )


@router.get("/{host}/{owner}/{name}/docs/{slug}", response_model=DocPageResponse)
async def get_doc_page(
    host: str,
    owner: str,
    name: str,
    slug: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> DocPageResponse:
    """Return a single doc page by slug.

    Raises 404 NOT_FOUND if the slug doesn't map to a known document or if
    the repo is not yet ready (docs not generated yet).
    """
    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    if repository.status is not RepositoryStatus.READY:
        raise ApiError(409, "REPO_NOT_READY", "Repository is not ready yet")
    page = await _wiki_query_service.get_doc_by_slug(
        session=session, repository_id=repository.id, slug=slug
    )
    if page is None:
        raise ApiError(404, "NOT_FOUND", "Doc page not found")

    return DocPageResponse(
        id=page.id,
        title=page.title,
        slug=page.slug,
        content=page.content,
        doc_type=page.doc_type,
        sort_order=page.sort_order,
        parent_id=page.parent_id,
        related_nodes=[
            RelatedNodeResponse(
                id=rn.id,
                name=rn.name,
                node_type=rn.node_type,
                file_path=rn.file_path,
                start_line=rn.start_line,
                end_line=rn.end_line,
            )
            for rn in page.related_nodes
        ],
        created_at=page.created_at,
        updated_at=page.updated_at,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tree_node_to_response(node) -> DocTreeNodeResponse:  # type: ignore[no-untyped-def]
    from backend.app.repo_docs.queries import WikiTreeNode  # local import avoids circularity

    assert isinstance(node, WikiTreeNode)
    return DocTreeNodeResponse(
        id=node.id,
        title=node.title,
        slug=node.slug,
        doc_type=node.doc_type,
        sort_order=node.sort_order,
        parent_id=node.parent_id,
        file_path=node.file_path,
        children=[_tree_node_to_response(c) for c in node.children],
    )
