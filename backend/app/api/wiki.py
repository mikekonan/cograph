"""Generated wiki endpoints backed by the ``documents`` table.

Single-variant surface:
    GET /repos/{host}/{owner}/{name}/wiki         -> WikiTreeResponse (tree)
    GET /repos/{host}/{owner}/{name}/wiki/{slug}  -> WikiPageResponse (one page)

The legacy ``preview``, ``legacy``, and ``rollout`` endpoints were removed
when the LLM-driven pipeline replaced V1/V2/V3 generation. There is one
variant; old preview/legacy URLs return 404.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.deps import (
    get_current_user_optional,
    get_db_session,
    get_settings_dep,
)
from backend.app.core.errors import ApiError
from backend.app.core.repository_access import get_readable_repository_by_slug
from backend.app.models.enums import RepositoryStatus
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.wiki.citation_repair import repair_page_citations
from backend.app.wiki.citations import RepositorySlug
from backend.app.wiki.queries import (
    WikiCitation,
    WikiPage,
    WikiPageQualityChips,
    WikiQueryService,
    WikiRelatedNode,
    WikiTreeNode,
)

router = APIRouter(prefix="/repos", tags=["wiki"])

_wiki_query_service = WikiQueryService()


class WikiTreeNodeResponse(BaseModel):
    id: UUID
    title: str
    slug: str
    sort_order: int
    parent_slug: str | None = None
    source_commit: str | None = None
    children: list["WikiTreeNodeResponse"] = Field(default_factory=list)


WikiTreeNodeResponse.model_rebuild()


class WikiTreeResponse(BaseModel):
    items: list[WikiTreeNodeResponse]
    total: int


class WikiCitationResponse(BaseModel):
    id: str
    kind: str
    label: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    heading_path: list[str] = Field(default_factory=list)


class WikiRelatedNodeResponse(BaseModel):
    id: UUID
    name: str
    node_type: str
    file_path: str
    start_line: int
    end_line: int


class WikiPageQualityResponse(BaseModel):
    """Per-page grounding telemetry surfaced as chips on the wiki page."""

    code_node_citation_count: int = 0
    doc_chunk_citation_count: int = 0
    unresolved_count: int = 0
    low_confidence_chunk_count: int = 0
    covers_questions: list[str] = Field(default_factory=list)
    manifest_entries_used: int = 0
    has_diagram: bool = False
    auto_links_added: int = 0
    agent_turns: int = 0
    tools_called: dict[str, int] = Field(default_factory=dict)
    files_read: int = 0
    tokens_used: int = 0


class WikiPageMetadata(BaseModel):
    """Per-page metadata: provenance, related items, and quality chips."""

    source_commit: str | None = None
    model: str
    related_files: list[str] = Field(default_factory=list)
    related_symbols: list[str] = Field(default_factory=list)
    related_pages: list[str] = Field(default_factory=list)
    refs: list[WikiCitationResponse] = Field(default_factory=list)
    quality: WikiPageQualityResponse | None = None


class WikiPageResponse(BaseModel):
    id: UUID
    title: str
    slug: str
    content: str
    sort_order: int
    parent_slug: str | None = None
    source_commit: str | None = None
    metadata: WikiPageMetadata
    related_nodes: list[WikiRelatedNodeResponse] = Field(default_factory=list)
    citations: list[WikiCitationResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


@router.get("/{host}/{owner}/{name}/wiki", response_model=WikiTreeResponse)
async def get_wiki_tree(
    host: str,
    owner: str,
    name: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> WikiTreeResponse:
    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    nodes = await _wiki_query_service.list_tree(
        session=session,
        repository_id=repository.id,
    )
    total = await _wiki_query_service.count_pages(
        session=session,
        repository_id=repository.id,
    )
    return WikiTreeResponse(
        items=[_tree_node_to_response(node) for node in nodes],
        total=total,
    )


class WikiCitationRepairResponse(BaseModel):
    """Per-page repair counters surfaced as the FE chip + toast.

    `raced` indicates a concurrent regen committed between our load and
    write — the repair is a no-op in that case (regen produced fresh
    content already). `page_loaded=False` means the slug doesn't exist.
    """

    patched: int = 0
    dropped: int = 0
    unchanged: int = 0
    url_format_upgraded: int = 0
    raced: bool = False


@router.post(
    "/{host}/{owner}/{name}/wiki/{slug}/repair-citations",
    response_model=WikiCitationRepairResponse,
)
async def repair_wiki_page_citations(
    host: str,
    owner: str,
    name: str,
    slug: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> WikiCitationRepairResponse:
    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    result = await repair_page_citations(
        session=session,
        repository_id=repository.id,
        repo_slug=RepositorySlug(
            host=repository.host,
            owner=repository.owner,
            name=repository.name,
        ),
        slug=slug,
    )
    if not result.page_loaded:
        raise ApiError(404, "NOT_FOUND", "Wiki page not found")
    return WikiCitationRepairResponse(
        patched=result.patched,
        dropped=result.dropped,
        unchanged=result.unchanged,
        url_format_upgraded=result.url_format_upgraded,
        raced=result.raced,
    )


@router.get("/{host}/{owner}/{name}/wiki/{slug}", response_model=WikiPageResponse)
async def get_wiki_page(
    host: str,
    owner: str,
    name: str,
    slug: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> WikiPageResponse:
    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    page = await _wiki_query_service.get_page_by_slug(
        session=session,
        repository_id=repository.id,
        slug=slug,
    )
    if page is None:
        raise ApiError(404, "NOT_FOUND", "Wiki page not found")
    return _page_to_response(page)


async def _require_ready_repository(
    *,
    session: AsyncSession,
    host: str,
    owner: str,
    name: str,
    settings: Settings,
    current_user: User | None,
) -> Repository:
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
    return repository


def _tree_node_to_response(node: WikiTreeNode) -> WikiTreeNodeResponse:
    return WikiTreeNodeResponse(
        id=node.id,
        title=node.title,
        slug=node.slug,
        sort_order=node.sort_order,
        parent_slug=node.parent_slug,
        source_commit=node.source_commit,
        children=[_tree_node_to_response(child) for child in node.children],
    )


def _page_to_response(page: WikiPage) -> WikiPageResponse:
    citations = [_citation_to_response(c) for c in page.citations]
    related_nodes = [_related_node_to_response(n) for n in page.related_nodes]
    metadata = WikiPageMetadata(
        source_commit=page.source_commit,
        model=page.model,
        related_files=_related_files(page.citations),
        related_symbols=[node.name for node in page.related_nodes],
        related_pages=[],
        refs=citations,
        quality=_quality_to_response(page.quality),
    )
    return WikiPageResponse(
        id=page.id,
        title=page.title,
        slug=page.slug,
        content=page.content,
        sort_order=page.sort_order,
        parent_slug=page.parent_slug,
        source_commit=page.source_commit,
        metadata=metadata,
        related_nodes=related_nodes,
        citations=citations,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )


def _citation_to_response(citation: WikiCitation) -> WikiCitationResponse:
    return WikiCitationResponse(
        id=citation.id,
        kind=citation.kind,
        label=citation.label,
        file_path=citation.file_path,
        start_line=citation.start_line,
        end_line=citation.end_line,
        heading_path=list(citation.heading_path),
    )


def _related_node_to_response(node: WikiRelatedNode) -> WikiRelatedNodeResponse:
    return WikiRelatedNodeResponse(
        id=node.id,
        name=node.name,
        node_type=node.node_type,
        file_path=node.file_path,
        start_line=node.start_line,
        end_line=node.end_line,
    )


def _quality_to_response(
    quality: WikiPageQualityChips | None,
) -> WikiPageQualityResponse | None:
    if quality is None:
        return None
    return WikiPageQualityResponse(
        code_node_citation_count=quality.code_node_citation_count,
        doc_chunk_citation_count=quality.doc_chunk_citation_count,
        unresolved_count=quality.unresolved_count,
        low_confidence_chunk_count=quality.low_confidence_chunk_count,
        covers_questions=list(quality.covers_questions),
        manifest_entries_used=quality.manifest_entries_used,
        has_diagram=quality.has_diagram,
        auto_links_added=quality.auto_links_added,
        agent_turns=quality.agent_turns,
        tools_called=dict(quality.tools_called),
        files_read=quality.files_read,
        tokens_used=quality.tokens_used,
    )


def _related_files(citations: list[WikiCitation]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for citation in citations:
        path = citation.file_path
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result
