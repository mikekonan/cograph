"""Read-side facade consumed by `api/wiki.py` and `mcp/resources.py`.

Single-variant read service for the LLM-driven wiki pipeline. Returns
`WikiPage` / `WikiTreeNode` / `WikiCitation` shapes consumed by both the
REST surface and the MCP resources.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.document import Document
from backend.app.wiki.compact import extract_lead, extract_sections

logger = logging.getLogger(__name__)


_DOC_TYPE = "wiki"

# The index page is the repo's narrative overview, so it earns a larger lead
# budget — the compact carries its elevator pitch in full. Every other page
# contributes only a one-glance blurb.
_INDEX_LEAD_CHARS = 1200
_PAGE_LEAD_CHARS = 400


class WikiCitation(BaseModel):
    id: str
    kind: Literal["node", "repo_doc_chunk"]
    label: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    heading_path: list[str] = Field(default_factory=list)


class WikiRelatedNode(BaseModel):
    id: UUID
    name: str
    node_type: str
    file_path: str
    start_line: int
    end_line: int


class WikiTreeNode(BaseModel):
    id: UUID
    title: str
    slug: str
    parent_slug: str | None = None
    sort_order: int
    source_commit: str | None = None
    children: list["WikiTreeNode"] = Field(default_factory=list)


WikiTreeNode.model_rebuild()


class WikiPageQualityChips(BaseModel):
    """Quality telemetry surfaced by the frontend chip row.

    Mirrors `backend.app.wiki.schemas.WikiPageQuality` but lives in the
    read-side facade because that schema is internal to the generation pipeline.
    """

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


class WikiPage(BaseModel):
    id: UUID
    title: str
    slug: str
    content: str
    parent_slug: str | None = None
    sort_order: int
    source_commit: str | None = None
    model: str
    citations: list[WikiCitation] = Field(default_factory=list)
    related_nodes: list[WikiRelatedNode] = Field(default_factory=list)
    source_node_ids: list[UUID] = Field(default_factory=list)
    source_repo_doc_chunk_ids: list[UUID] = Field(default_factory=list)
    quality: WikiPageQualityChips | None = None
    created_at: datetime
    updated_at: datetime


class WikiCompactPage(BaseModel):
    """One page reduced to its map entry: what it's about, what's in it, and
    which reader-questions it answers. Over MCP this entry is the only served
    form; the full body is rendered by the web UI alone.
    """

    slug: str
    title: str
    parent_slug: str | None = None
    sort_order: int
    lead: str
    sections: list[str] = Field(default_factory=list)
    covers_questions: list[str] = Field(default_factory=list)


class WikiQueryService:
    """Read-side facade. All endpoints use this — there is one variant only."""

    async def list_tree(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
    ) -> list[WikiTreeNode]:
        stmt = (
            select(
                Document.id,
                Document.slug,
                Document.title,
                Document.parent_slug,
                Document.sort_order,
                Document.source_commit,
            )
            .where(
                Document.repository_id == repository_id,
                Document.doc_type == _DOC_TYPE,
            )
            .order_by(Document.sort_order.asc(), Document.slug.asc())
        )
        rows = (await session.execute(stmt)).all()
        flat: list[WikiTreeNode] = [
            WikiTreeNode(
                id=row.id,
                title=row.title,
                slug=row.slug,
                parent_slug=row.parent_slug,
                sort_order=row.sort_order,
                source_commit=row.source_commit,
            )
            for row in rows
        ]
        return _build_tree(flat)

    async def get_page_by_slug(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        slug: str,
    ) -> WikiPage | None:
        stmt = select(Document).where(
            Document.repository_id == repository_id,
            Document.slug == slug,
            Document.doc_type == _DOC_TYPE,
        )
        document = (await session.execute(stmt)).scalar_one_or_none()
        if document is None:
            return None

        related_nodes = await _load_related_nodes(
            session=session,
            repository_id=repository_id,
            node_ids=document.source_node_ids,
        )
        citations = [
            _citation_from_payload(payload)
            for payload in (document.citations or [])
            if isinstance(payload, dict)
        ]

        return WikiPage(
            id=document.id,
            title=document.title,
            slug=document.slug,
            content=document.content,
            parent_slug=document.parent_slug,
            sort_order=document.sort_order,
            source_commit=document.source_commit,
            model=document.model,
            citations=citations,
            related_nodes=related_nodes,
            source_node_ids=[
                _coerce_uuid(value) for value in (document.source_node_ids or [])
            ],
            source_repo_doc_chunk_ids=[
                _coerce_uuid(value)
                for value in (document.source_repo_doc_chunk_ids or [])
            ],
            quality=_quality_from_payload(document.quality),
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    async def count_pages(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
    ) -> int:
        stmt = select(func.count(Document.id)).where(
            Document.repository_id == repository_id,
            Document.doc_type == _DOC_TYPE,
        )
        return int((await session.scalar(stmt)) or 0)

    async def get_compact(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
    ) -> list[WikiCompactPage]:
        """A compacted view of the whole wiki — every page as a map entry.

        Deterministic, LLM-free, recomputed on read from the stored markdown,
        so it never drifts from the published pages. ~2-3k tokens for a
        typical repo versus ~73k for the full wiki.
        """
        stmt = (
            select(
                Document.slug,
                Document.title,
                Document.parent_slug,
                Document.sort_order,
                Document.content,
                Document.quality,
            )
            .where(
                Document.repository_id == repository_id,
                Document.doc_type == _DOC_TYPE,
            )
            .order_by(Document.sort_order.asc(), Document.slug.asc())
        )
        rows = (await session.execute(stmt)).all()
        compact: list[WikiCompactPage] = []
        for row in rows:
            content = row.content or ""
            quality = row.quality if isinstance(row.quality, dict) else {}
            covers_raw = quality.get("covers_questions") or []
            covers = [str(item) for item in covers_raw if item is not None]
            max_chars = (
                _INDEX_LEAD_CHARS if row.slug == "index" else _PAGE_LEAD_CHARS
            )
            compact.append(
                WikiCompactPage(
                    slug=row.slug,
                    title=row.title,
                    parent_slug=row.parent_slug,
                    sort_order=row.sort_order,
                    lead=extract_lead(content, max_chars=max_chars),
                    sections=extract_sections(content),
                    covers_questions=covers,
                )
            )
        return compact


def _build_tree(flat: list[WikiTreeNode]) -> list[WikiTreeNode]:
    by_slug: dict[str, WikiTreeNode] = {node.slug: node for node in flat}
    roots: list[WikiTreeNode] = []
    for node in flat:
        parent_slug = node.parent_slug
        parent = by_slug.get(parent_slug) if parent_slug else None
        if parent is None or parent is node:
            roots.append(node)
        else:
            parent.children.append(node)
    return roots


def _citation_from_payload(payload: dict[str, object]) -> WikiCitation:
    raw_kind = str(payload.get("kind", ""))
    kind: Literal["node", "repo_doc_chunk"]
    if raw_kind in {"node", "repo_doc_chunk"}:
        kind = raw_kind  # type: ignore[assignment]
    else:
        # Legacy `source_file` rows from V3 wikis collapse to `node` so the
        # frontend doesn't need a third branch; the resolver no longer
        # produces `source_file` going forward.
        kind = "node"
    heading_raw = payload.get("heading_path") or []
    heading_path = [str(item) for item in heading_raw if item is not None]
    return WikiCitation(
        id=str(payload.get("id", "")),
        kind=kind,
        label=str(payload.get("label", "")),
        file_path=str(payload.get("file_path", "")),
        start_line=_parse_optional_int(payload.get("start_line")),
        end_line=_parse_optional_int(payload.get("end_line")),
        heading_path=heading_path,
    )


def _quality_from_payload(payload: object) -> WikiPageQualityChips | None:
    if not isinstance(payload, dict):
        return None
    covers_raw = payload.get("covers_questions") or []
    covers = [str(item) for item in covers_raw if item is not None]
    tools_raw = payload.get("tools_called") or {}
    tools_called: dict[str, int] = {}
    if isinstance(tools_raw, dict):
        for name, count in tools_raw.items():
            parsed = _parse_optional_int(count)
            if parsed is not None:
                tools_called[str(name)] = parsed
    return WikiPageQualityChips(
        code_node_citation_count=_parse_optional_int(
            payload.get("code_node_citation_count")
        )
        or 0,
        doc_chunk_citation_count=_parse_optional_int(
            payload.get("doc_chunk_citation_count")
        )
        or 0,
        unresolved_count=_parse_optional_int(payload.get("unresolved_count")) or 0,
        low_confidence_chunk_count=_parse_optional_int(
            payload.get("low_confidence_chunk_count")
        )
        or 0,
        covers_questions=covers,
        manifest_entries_used=_parse_optional_int(
            payload.get("manifest_entries_used")
        )
        or 0,
        has_diagram=bool(payload.get("has_diagram", False)),
        auto_links_added=_parse_optional_int(payload.get("auto_links_added")) or 0,
        agent_turns=_parse_optional_int(payload.get("agent_turns")) or 0,
        tools_called=tools_called,
        files_read=_parse_optional_int(payload.get("files_read")) or 0,
        tokens_used=_parse_optional_int(payload.get("tokens_used")) or 0,
    )


def _parse_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _coerce_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


async def _load_related_nodes(
    *,
    session: AsyncSession,
    repository_id: UUID,
    node_ids: list[str],
) -> list[WikiRelatedNode]:
    if not node_ids:
        return []
    uuids: list[UUID] = []
    for value in node_ids:
        try:
            uuids.append(_coerce_uuid(value))
        except ValueError:
            continue
    if not uuids:
        return []
    stmt = select(
        CodeNode.id,
        CodeNode.name,
        CodeNode.node_type,
        CodeNode.file_path,
        CodeNode.start_line,
        CodeNode.end_line,
    ).where(
        CodeNode.repository_id == repository_id,
        CodeNode.id.in_(uuids),
    )
    rows = (await session.execute(stmt)).all()
    by_id = {
        row.id: WikiRelatedNode(
            id=row.id,
            name=row.name,
            node_type=row.node_type.value
            if hasattr(row.node_type, "value")
            else str(row.node_type),
            file_path=row.file_path,
            start_line=int(row.start_line or 0),
            end_line=int(row.end_line or 0),
        )
        for row in rows
    }
    # Preserve the order of source_node_ids while skipping unknowns.
    result: list[WikiRelatedNode] = []
    seen: set[UUID] = set()
    for nid in uuids:
        node = by_id.get(nid)
        if node is None or node.id in seen:
            continue
        seen.add(node.id)
        result.append(node)
    return result
