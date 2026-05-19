"""Token-cheap 'what's here' overview for a repo or md collection.

Replaces the chained `repositories` → `wiki_tree` → `repo_files`
sequence agents currently use to bootstrap context. Caps everything at 30
items so the response never exceeds a couple thousand tokens.
"""

from __future__ import annotations

from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.app.core.errors import ApiError
from backend.app.core.md_collection_access import get_readable_md_collection
from backend.app.mcp.services import (
    MCPServices,
    _mcp_error,
    current_user_from_context,
    encode_payload,
    require_ready_repository,
    resolve_readable_repository_by_slug,
)
from backend.app.models.md_collection import MdDocument
from backend.app.models.source_file import SourceFile

_OUTLINE_DESCRIPTION = (
    "Token-cheap structural overview: top-level dirs + wiki page titles for a "
    "repo, OR document titles + heading sketches for an md collection. ≤ 30 "
    "items per section.\n"
    "Use when: agent has just resolved a repo/collection and needs to know "
    "what's inside before any retrieval call. One-shot bootstrap.\n"
    "Do NOT use to read content (use cograph_repository_readme / "
    "cograph_collection_document) or to search (use cograph_retrieve)."
)

_OUTLINE_MAX_ITEMS = 30


class OutlineArgs(BaseModel):
    repository: str | None = None
    collection_id: UUID | None = None


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph_outline",
        description=_OUTLINE_DESCRIPTION,
    )
    async def outline(
        repository: str | None = None,
        collection_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> object:
        args = OutlineArgs(repository=repository, collection_id=collection_id)
        if (args.repository is None) == (args.collection_id is None):
            raise ValueError(
                "INVALID_REQUEST: provide exactly one of `repository` or `collection_id`"
            )
        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            if args.repository is not None:
                repo = await resolve_readable_repository_by_slug(
                    session=session,
                    slug=args.repository,
                    services=services,
                    current_user=current_user,
                )
                await require_ready_repository(
                    session=session,
                    repository_id=repo.id,
                )

                # Group by top-level dir in Python — `split_part` is
                # Postgres-only and the outline is cheap (we don't fan out
                # over millions of paths).
                file_path_rows = (
                    await session.execute(
                        select(SourceFile.file_path).where(
                            SourceFile.repository_id == repo.id
                        )
                    )
                ).all()
                dir_counts: dict[str, int] = {}
                for (path,) in file_path_rows:
                    if not path:
                        continue
                    head = path.split("/", 1)[0]
                    dir_counts[head] = dir_counts.get(head, 0) + 1
                ranked_dirs = sorted(
                    dir_counts.items(),
                    key=lambda kv: (-kv[1], kv[0]),
                )[:_OUTLINE_MAX_ITEMS]

                wiki_tree = await services.wiki_queries.list_tree(
                    session=session,
                    repository_id=repo.id,
                )
                wiki_total = await services.wiki_queries.count_pages(
                    session=session,
                    repository_id=repo.id,
                )
                wiki_titles = [
                    {"slug": node.slug, "title": node.title}
                    for node in wiki_tree[:_OUTLINE_MAX_ITEMS]
                ]

                slug_path = f"{repo.host}/{repo.owner}/{repo.name}"
                return encode_payload(
                    {
                        "kind": "repository",
                        "repository_slug": slug_path,
                        "top_directories": [
                            {"path": path, "file_count": count}
                            for path, count in ranked_dirs
                        ],
                        "top_directories_truncated": (
                            len(dir_counts) > len(ranked_dirs)
                        ),
                        "wiki_pages": wiki_titles,
                        "wiki_total": wiki_total,
                        "wiki_pages_truncated": (
                            wiki_total > len(wiki_titles)
                        ),
                    }
                )

            assert args.collection_id is not None
            try:
                collection = await get_readable_md_collection(
                    session=session,
                    collection_id=args.collection_id,
                    current_user=current_user,
                )
            except ApiError as exc:
                raise _mcp_error(exc) from exc

            doc_rows = (
                await session.execute(
                    select(
                        MdDocument.id,
                        MdDocument.source_key,
                        MdDocument.title,
                        MdDocument.heading_tree,
                        MdDocument.bytes,
                    )
                    .where(MdDocument.collection_id == collection.id)
                    .order_by(MdDocument.updated_at.desc())
                    .limit(_OUTLINE_MAX_ITEMS)
                )
            ).all()
            doc_total = (
                await session.scalar(
                    select(func.count(MdDocument.id)).where(
                        MdDocument.collection_id == collection.id
                    )
                )
                or 0
            )
            documents = [
                {
                    "document_id": row.id,
                    "source_key": row.source_key,
                    "title": row.title,
                    "bytes": row.bytes,
                    "headings": _flatten_heading_tree(
                        row.heading_tree, max_items=_OUTLINE_MAX_ITEMS
                    ),
                }
                for row in doc_rows
            ]
            return encode_payload(
                {
                    "kind": "collection",
                    "collection_id": collection.id,
                    "collection_name": collection.name,
                    "documents": documents,
                    "documents_truncated": doc_total > len(documents),
                    "documents_total": int(doc_total),
                }
            )


def _flatten_heading_tree(
    tree: list[dict[str, object]] | None,
    *,
    max_items: int,
) -> list[dict[str, object]]:
    """Walk an md_documents.heading_tree depth-first and emit at most
    ``max_items`` `{level, text}` entries. Heading nodes carry `level`,
    `text`, and `children` (list of dicts) when produced by the indexer.
    """
    if not tree:
        return []
    flat: list[dict[str, object]] = []

    def visit(node: dict[str, object]) -> None:
        if len(flat) >= max_items:
            return
        text = node.get("text")
        level = node.get("level")
        if isinstance(text, str) and text.strip():
            flat.append({"level": int(level) if isinstance(level, int) else 0, "text": text})
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    for root in tree:
        if isinstance(root, dict):
            visit(root)
    return flat
