from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from pathlib import PurePosixPath
from uuid import UUID
import uuid

from sqlalchemy import func, or_, over, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.repo_docs.slug import build_slug_map

# Strips markdown syntax for clean excerpt previews:
# headings (#+ prefix), bold/italic (* _ sequences), inline code (` backtick),
# and leading/trailing whitespace.
_MARKDOWN_STRIP_RE = re.compile(r"^#+\s*|[*_`]+", re.MULTILINE)


def _strip_markdown(text: str) -> str:
    return _MARKDOWN_STRIP_RE.sub("", text).strip()


@dataclass(slots=True, kw_only=True)
class RepoDocumentListItem:
    id: UUID
    repository_id: UUID
    file_path: str
    title: str | None
    bytes: int
    chunk_count: int
    mentions_count: int
    excerpt: str | None
    updated_at: datetime


@dataclass(slots=True, kw_only=True)
class RepoDocumentListResult:
    items: list[RepoDocumentListItem]
    total: int
    page: int
    per_page: int
    total_pages: int


@dataclass(slots=True, kw_only=True)
class RepoDocumentMention:
    node_id: UUID
    name: str
    file_path: str


@dataclass(slots=True, kw_only=True)
class RepoDocumentChunkDetail:
    id: UUID
    chunk_index: int
    heading_path: list[str]
    mentions: list[RepoDocumentMention]


@dataclass(slots=True, kw_only=True)
class RepoDocumentDetail:
    id: UUID
    repository_id: UUID
    file_path: str
    title: str | None
    content: str
    bytes: int
    chunks: list[RepoDocumentChunkDetail]
    created_at: datetime
    updated_at: datetime


class RepoDocumentQueryService:
    async def list_documents(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        page: int,
        per_page: int,
        search: str | None = None,
    ) -> RepoDocumentListResult:
        query = select(RepoDocument).where(RepoDocument.repository_id == repository_id)
        if search:
            pattern = f"%{search.lower()}%"
            query = query.where(
                or_(
                    func.lower(RepoDocument.file_path).like(pattern),
                    func.lower(func.coalesce(RepoDocument.title, "")).like(pattern),
                )
            )

        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        offset = (page - 1) * per_page
        documents = list(
            (
                await session.scalars(
                    query.order_by(RepoDocument.file_path.asc()).offset(offset).limit(per_page)
                )
            ).all()
        )

        document_ids = [document.id for document in documents]
        chunk_counts = await self._chunk_counts(session=session, document_ids=document_ids)
        mention_counts = await self._mention_counts(session=session, document_ids=document_ids)
        first_chunks = await self._first_chunks(session=session, document_ids=document_ids)

        return RepoDocumentListResult(
            items=[
                RepoDocumentListItem(
                    id=document.id,
                    repository_id=document.repository_id,
                    file_path=document.file_path,
                    title=document.title,
                    bytes=document.bytes,
                    chunk_count=chunk_counts.get(document.id, 0),
                    mentions_count=mention_counts.get(document.id, 0),
                    excerpt=first_chunks.get(document.id),
                    updated_at=document.updated_at,
                )
                for document in documents
            ],
            total=total or 0,
            page=page,
            per_page=per_page,
            total_pages=ceil((total or 0) / per_page) if per_page > 0 else 0,
        )

    async def get_document(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        document_id: UUID,
    ) -> RepoDocumentDetail | None:
        document = await session.scalar(
            select(RepoDocument).where(
                RepoDocument.repository_id == repository_id,
                RepoDocument.id == document_id,
            )
        )
        if document is None:
            return None

        chunks = list(
            (
                await session.scalars(
                    select(RepoDocumentChunk)
                    .where(RepoDocumentChunk.document_id == document.id)
                    .order_by(RepoDocumentChunk.chunk_index.asc())
                )
            ).all()
        )
        mention_ids = {
            mention_id
            for chunk in chunks
            for mention_id in chunk.mentions
        }
        mention_uuid_ids = tuple(uuid.UUID(mention_id) for mention_id in mention_ids)
        mention_nodes = {
            str(node.id): node
            for node in (
                await session.scalars(
                    select(CodeNode).where(CodeNode.id.in_(mention_uuid_ids))
                )
            ).all()
        } if mention_ids else {}

        return RepoDocumentDetail(
            id=document.id,
            repository_id=document.repository_id,
            file_path=document.file_path,
            title=document.title,
            content=document.content,
            bytes=document.bytes,
            chunks=[
                RepoDocumentChunkDetail(
                    id=chunk.id,
                    chunk_index=chunk.chunk_index,
                    heading_path=list(chunk.heading_path),
                    mentions=[
                        RepoDocumentMention(
                            node_id=mention_nodes[mention_id].id,
                            name=mention_nodes[mention_id].name,
                            file_path=mention_nodes[mention_id].file_path,
                        )
                        for mention_id in chunk.mentions
                        if mention_id in mention_nodes
                    ],
                )
                for chunk in chunks
            ],
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    async def _chunk_counts(
        self,
        *,
        session: AsyncSession,
        document_ids: list[UUID],
    ) -> dict[UUID, int]:
        if not document_ids:
            return {}
        rows = await session.execute(
            select(RepoDocumentChunk.document_id, func.count())
            .where(RepoDocumentChunk.document_id.in_(tuple(document_ids)))
            .group_by(RepoDocumentChunk.document_id)
        )
        return {document_id: count for document_id, count in rows.all()}

    async def _mention_counts(
        self,
        *,
        session: AsyncSession,
        document_ids: list[UUID],
    ) -> dict[UUID, int]:
        if not document_ids:
            return {}
        chunks = list(
            (
                await session.scalars(
                    select(RepoDocumentChunk).where(
                        RepoDocumentChunk.document_id.in_(tuple(document_ids))
                    )
                )
            ).all()
        )
        mention_counts: dict[UUID, int] = {document_id: 0 for document_id in document_ids}
        for chunk in chunks:
            mention_counts[chunk.document_id] = mention_counts.get(chunk.document_id, 0) + len(
                chunk.mentions
            )
        return mention_counts

    async def _first_chunks(
        self,
        *,
        session: AsyncSession,
        document_ids: list[UUID],
    ) -> dict[UUID, str]:
        """Return a map of document_id -> excerpt (first ~280 chars, markdown stripped).

        Uses ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY chunk_index) to
        fetch exactly one chunk per document in a single query.
        SQLite supports window functions since 3.25; Python ships 3.38+.
        PostgreSQL supports it natively.
        """
        if not document_ids:
            return {}
        rn = over(
            func.row_number(),
            partition_by=RepoDocumentChunk.document_id,
            order_by=RepoDocumentChunk.chunk_index.asc(),
        ).label("rn")

        subq = (
            select(RepoDocumentChunk.document_id, RepoDocumentChunk.content, rn)
            .where(RepoDocumentChunk.document_id.in_(tuple(document_ids)))
            .subquery()
        )

        rows = await session.execute(
            select(subq.c.document_id, subq.c.content).where(subq.c.rn == 1)
        )
        result: dict[UUID, str] = {}
        for document_id, content in rows.all():
            stripped = _strip_markdown(content)[:280]
            if stripped:
                result[document_id] = stripped
        return result


# ---------------------------------------------------------------------------
# Wiki tree / page queries (v1 aliasing over repo_documents)
# ---------------------------------------------------------------------------

# Doc types inferred from top-level directory name (lower-cased).
# Anything not in the map falls back to "guide".
_DIR_TO_DOC_TYPE: dict[str, str] = {
    "api": "api",
    "apis": "api",
    "reference": "api",
    "ref": "api",
    "modules": "module",
    "module": "module",
    "guides": "guide",
    "guide": "guide",
    "tutorials": "guide",
    "tutorial": "guide",
    "howto": "guide",
    "how-to": "guide",
    "overview": "overview",
}

_ROOT_FILENAMES = {"readme", "index", "overview", "introduction", "intro", "main"}


def _infer_doc_type(file_path: str) -> str:
    """Return one of ``overview | module | api | guide`` for the file path."""
    parts = PurePosixPath(file_path).parts
    if len(parts) == 1:
        stem = PurePosixPath(file_path).stem.lower()
        if stem in _ROOT_FILENAMES:
            return "overview"
        return "guide"
    top_dir = parts[0].lower()
    return _DIR_TO_DOC_TYPE.get(top_dir, "guide")


@dataclass(slots=True, kw_only=True)
class WikiRelatedNode:
    id: UUID
    name: str
    node_type: str
    file_path: str
    start_line: int
    end_line: int


@dataclass(slots=True, kw_only=True)
class WikiTreeNode:
    id: UUID
    title: str
    slug: str
    doc_type: str
    sort_order: int
    parent_id: UUID | None
    file_path: str | None = None
    children: list[WikiTreeNode] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class WikiPage:
    id: UUID
    title: str
    slug: str
    content: str
    doc_type: str
    sort_order: int
    parent_id: UUID | None
    related_nodes: list[WikiRelatedNode]
    created_at: datetime
    updated_at: datetime


class WikiQueryService:
    """Build a recursive doc tree mirroring the repo's filesystem layout.

    Tree shape
    ----------
    - Each unique directory prefix becomes a synthetic group node with
      ``slug = "_dir-<posix-path>"`` and ``id = uuid5(repo_id + path)``.
      Group nodes are non-navigable (FE renders them with a folder icon
      and uses the ``_dir-`` sentinel to skip routing).
    - Leaf nodes carry the document's real ``file_path``; their slug is
      path-derived via ``build_slug_map`` and is the URL contract.
    - At each tree level: README/index/overview files pinned to the top,
      then directories alpha, then remaining leaves alpha. The pin reflects
      filesystem convention (README is the landing page in any directory).
    - ``related_nodes`` come from chunk ``mentions``; ``doc_type`` is
      inferred from the immediate parent directory; ``sort_order`` is the
      0-indexed position within the rendered sibling list.
    """

    # uuid5 namespace for synthetic group node ids.
    _GROUP_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

    async def list_docs_tree(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
    ) -> list[WikiTreeNode]:
        documents = list(
            (
                await session.scalars(
                    select(RepoDocument)
                    .where(RepoDocument.repository_id == repository_id)
                    .order_by(RepoDocument.file_path.asc())
                )
            ).all()
        )
        if not documents:
            return []

        slug_map = build_slug_map([(doc.id, doc.file_path) for doc in documents])
        id_to_slug = {doc_id: slug for slug, doc_id in slug_map.items()}

        # Build a directory map: dir_path -> { 'subdirs': set[str], 'docs': list[RepoDocument] }.
        # Empty string is the repo root.
        dir_map: dict[str, dict[str, object]] = {"": {"subdirs": set(), "docs": []}}
        for doc in documents:
            parts = PurePosixPath(doc.file_path).parts
            parent_dir = "/".join(parts[:-1]) if len(parts) > 1 else ""
            # Ensure every ancestor directory exists in dir_map.
            running = ""
            for part in parts[:-1]:
                child = f"{running}/{part}" if running else part
                dir_map.setdefault(running, {"subdirs": set(), "docs": []})
                dir_map[running]["subdirs"].add(child)  # type: ignore[union-attr]
                running = child
            dir_map.setdefault(parent_dir, {"subdirs": set(), "docs": []})
            dir_map[parent_dir]["docs"].append(doc)  # type: ignore[union-attr]

        return self._build_dir_children(
            dir_path="",
            parent_id=None,
            dir_map=dir_map,
            id_to_slug=id_to_slug,
            repository_id=repository_id,
        )

    def _build_dir_children(
        self,
        *,
        dir_path: str,
        parent_id: UUID | None,
        dir_map: dict[str, dict[str, object]],
        id_to_slug: dict[UUID, str],
        repository_id: UUID,
    ) -> list[WikiTreeNode]:
        """Recursively materialise children for ``dir_path`` as WikiTreeNodes."""
        entry = dir_map.get(dir_path)
        if entry is None:
            return []
        subdirs: list[str] = sorted(entry["subdirs"])  # type: ignore[arg-type,assignment]
        docs: list[RepoDocument] = sorted(
            entry["docs"],  # type: ignore[arg-type]
            key=lambda d: PurePosixPath(d.file_path).name.lower(),
        )

        # Pin README/index-style filenames first within this dir, then
        # subdirectories, then the rest of the leaves.
        pinned: list[RepoDocument] = []
        rest: list[RepoDocument] = []
        for doc in docs:
            stem = PurePosixPath(doc.file_path).stem.lower()
            (pinned if stem in _ROOT_FILENAMES else rest).append(doc)

        children: list[WikiTreeNode] = []
        sort_index = 0

        for doc in pinned:
            children.append(self._leaf_node(doc, id_to_slug, parent_id, sort_index))
            sort_index += 1

        for subdir in subdirs:
            group_id = uuid.uuid5(self._GROUP_NS, f"{repository_id}/{subdir}")
            group_basename = PurePosixPath(subdir).name
            group_title = group_basename.replace("-", " ").replace("_", " ").title()
            group_doc_type = _DIR_TO_DOC_TYPE.get(group_basename.lower(), "guide")
            sub_children = self._build_dir_children(
                dir_path=subdir,
                parent_id=group_id,
                dir_map=dir_map,
                id_to_slug=id_to_slug,
                repository_id=repository_id,
            )
            children.append(
                WikiTreeNode(
                    id=group_id,
                    title=group_title,
                    slug=f"_dir-{subdir}",
                    doc_type=group_doc_type,
                    sort_order=sort_index,
                    parent_id=parent_id,
                    file_path=None,
                    children=sub_children,
                )
            )
            sort_index += 1

        for doc in rest:
            children.append(self._leaf_node(doc, id_to_slug, parent_id, sort_index))
            sort_index += 1

        return children

    def _leaf_node(
        self,
        doc: RepoDocument,
        id_to_slug: dict[UUID, str],
        parent_id: UUID | None,
        sort_order: int,
    ) -> WikiTreeNode:
        slug = id_to_slug[doc.id]
        title = doc.title or _slug_to_title(slug)
        return WikiTreeNode(
            id=doc.id,
            title=title,
            slug=slug,
            doc_type=_infer_doc_type(doc.file_path),
            sort_order=sort_order,
            parent_id=parent_id,
            file_path=doc.file_path,
            children=[],
        )

    async def get_doc_by_slug(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        slug: str,
    ) -> WikiPage | None:
        documents = list(
            (
                await session.scalars(
                    select(RepoDocument)
                    .where(RepoDocument.repository_id == repository_id)
                    .order_by(RepoDocument.file_path.asc())
                )
            ).all()
        )
        if not documents:
            return None

        slug_map = build_slug_map([(doc.id, doc.file_path) for doc in documents])
        target_id = slug_map.get(slug)
        if target_id is None:
            return None

        doc = next((d for d in documents if d.id == target_id), None)
        if doc is None:
            return None

        chunks = list(
            (
                await session.scalars(
                    select(RepoDocumentChunk)
                    .where(RepoDocumentChunk.document_id == doc.id)
                    .order_by(RepoDocumentChunk.chunk_index.asc())
                )
            ).all()
        )

        mention_id_strs: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            for mid in chunk.mentions:
                if mid not in seen:
                    seen.add(mid)
                    mention_id_strs.append(mid)

        related_nodes: list[WikiRelatedNode] = []
        if mention_id_strs:
            mention_uuids = [uuid.UUID(mid) for mid in mention_id_strs]
            nodes = list(
                (
                    await session.scalars(
                        select(CodeNode).where(CodeNode.id.in_(mention_uuids))
                    )
                ).all()
            )
            node_map = {str(n.id): n for n in nodes}
            for mid in mention_id_strs:
                node = node_map.get(mid)
                if node is not None:
                    related_nodes.append(
                        WikiRelatedNode(
                            id=node.id,
                            name=node.name,
                            node_type=node.node_type.value,
                            file_path=node.file_path,
                            start_line=node.start_line,
                            end_line=node.end_line,
                        )
                    )

        parts = PurePosixPath(doc.file_path).parts
        parent_dir = "/".join(parts[:-1]) if len(parts) > 1 else ""
        parent_id: UUID | None = None
        if parent_dir:
            parent_id = uuid.uuid5(self._GROUP_NS, f"{repository_id}/{parent_dir}")

        # Sort within the same-immediate-directory siblings the same way
        # `_build_dir_children` does: README-likes first, then subdirs (we
        # only count leaves for sort_order on a leaf page), then the rest
        # ordered by basename.
        same_dir_docs = [
            d for d in documents
            if "/".join(PurePosixPath(d.file_path).parts[:-1]) == parent_dir
        ]
        same_dir_docs.sort(key=lambda d: PurePosixPath(d.file_path).name.lower())
        pinned = [
            d for d in same_dir_docs
            if PurePosixPath(d.file_path).stem.lower() in _ROOT_FILENAMES
        ]
        rest = [d for d in same_dir_docs if d not in pinned]
        leaf_order = pinned + rest
        sort_order = next((i for i, s in enumerate(leaf_order) if s.id == doc.id), 0)

        return WikiPage(
            id=doc.id,
            title=doc.title or _slug_to_title(slug),
            slug=slug,
            content=doc.content,
            doc_type=_infer_doc_type(doc.file_path),
            sort_order=sort_order,
            parent_id=parent_id,
            related_nodes=related_nodes,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )


def _slug_to_title(slug: str) -> str:
    """Convert a slug to a human-readable title as a fallback."""
    clean = slug.lstrip("_")
    return clean.replace("-", " ").title()
