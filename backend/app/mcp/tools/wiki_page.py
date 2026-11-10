"""On-demand full read of ONE generated-wiki page (or one of its sections).

The summarized wiki resource (`cograph_wiki_tree`) is the default, always-in-
context surface; this tool is the deliberate pull an agent makes when that
summary is too terse for a given page. Keeping full bodies behind a tool — not
a resource — is what stops the large wiki from being advertised into context by
default.
"""

from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel

from backend.app.mcp.services import (
    read_only,
    MCPServices,
    current_user_from_context,
    resolve_readable_repository_by_slug,
    tool_args,
    wiki_page_payload,
)

_WIKI_PAGE_DESCRIPTION = (
    "Read ONE generated-wiki page — or a single named section of it — on "
    "demand. The body is cut at a line boundary after 12000 characters with "
    "structure preserved; `content_truncated` says whether that happened, and "
    "the fix is to re-read one `section` rather than to retry the page.\n"
    "Use when: you need a generated-wiki page's prose, diagrams, or code "
    "samples. `page` is a slug from a search result's `wiki` or from "
    "cograph_outline's `wiki_pages`; pass "
    "`section` (a heading from that page's `sections` list) to pull just that "
    "section and save tokens.\n"
    "Do NOT use to search code (use cograph_retrieve / cograph_search_code)."
)


class WikiPageArgs(BaseModel):
    repository: str
    page: str
    section: str | None = None


def register(server: MCPServer, services: MCPServices) -> None:
    @server.tool(
        name="cograph_wiki_page",
        annotations=read_only("Read a wiki page"),
        description=_WIKI_PAGE_DESCRIPTION,
    )
    async def wiki_page(
        repository: str,
        page: str,
        section: str | None = None,
        ctx: Context | None = None,
    ) -> object:
        args = tool_args(WikiPageArgs, repository=repository, page=page, section=section)
        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            repo = await resolve_readable_repository_by_slug(
                session=session,
                slug=args.repository,
                services=services,
                current_user=current_user,
            )
        return await wiki_page_payload(
            services=services,
            repository=repo,
            page=args.page,
            section=args.section,
        )
