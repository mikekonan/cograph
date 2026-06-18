"""One-call README/Overview fetch for a repository.

Replaces the chained `cograph_repositories` → `cograph_retrieve` → `cograph_read_node`
sequence agents currently use to answer "what is repo X about?".
"""

from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel
from sqlalchemy import select

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
    require_ready_repository,
    resolve_readable_repository_by_slug,
)
from backend.app.models.repo_document import RepoDocument
from backend.app.rag.snippet import make_snippet
from backend.app.wiki.compact import extract_lead, extract_sections

_README_DESCRIPTION = (
    "Fetch the canonical README/Overview document for a repository in one call.\n"
    "Use when: the agent has a repo slug and wants to know what the project does, "
    "its scope, or how to use it. Falls back to the compacted wiki Overview "
    "(lead + section headings) if no README-named file is indexed.\n"
    "Do NOT use to search inside the readme (use cograph_retrieve mode='wiki') or "
    "to read other docs (use cograph_collection_search / cograph_read_chunk)."
)

_README_SNIPPET_CHARS = 4000


class RepositoryReadmeArgs(BaseModel):
    slug: str


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph_repository_readme",
        description=_README_DESCRIPTION,
    )
    async def repository_readme(
        slug: str,
        ctx: Context | None = None,
    ) -> object:
        args = RepositoryReadmeArgs(slug=slug)
        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            repository = await resolve_readable_repository_by_slug(
                session=session,
                slug=args.slug,
                services=services,
                current_user=current_user,
            )
            await require_ready_repository(
                session=session,
                repository_id=repository.id,
            )

            doc = await session.scalar(
                select(RepoDocument)
                .where(
                    RepoDocument.repository_id == repository.id,
                    RepoDocument.file_path.ilike("%readme%"),
                )
                .order_by(RepoDocument.bytes.desc())
                .limit(1)
            )

            slug_path = (
                f"{repository.host}/{repository.owner}/{repository.name}"
            )

            if doc is not None:
                snippet, truncated = make_snippet(
                    doc.content,
                    None,
                    chars=_README_SNIPPET_CHARS,
                )
                return encode_payload(
                    {
                        "repository_slug": slug_path,
                        "source": "repo_doc",
                        "document_id": doc.id,
                        "source_path": doc.file_path,
                        "title": doc.title,
                        "content": snippet,
                        "content_truncated": truncated,
                        "bytes": doc.bytes,
                    }
                )

            # Fallback: wiki Overview page, served in its SUMMARIZED form here
            # (lead overview + section headings). The full body is pull-only,
            # via the cograph_wiki_page tool — not returned from this readme call.
            wiki_page = await services.wiki_queries.get_page_by_slug(
                session=session,
                repository_id=repository.id,
                slug="overview",
            )

        if wiki_page is None:
            raise ValueError(
                "NOT_FOUND: No README and no wiki overview indexed for this repo"
            )

        return encode_payload(
            {
                "repository_slug": slug_path,
                "source": "wiki",
                "wiki_slug": wiki_page.slug,
                "title": wiki_page.title,
                "lead": extract_lead(wiki_page.content, max_chars=1200),
                "sections": extract_sections(wiki_page.content),
            }
        )
