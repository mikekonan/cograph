"""On-demand full read of ONE generated-wiki page (or one of its sections).

The summarized wiki resource (`cograph_wiki_tree`) is the default, always-in-
context surface; this tool is the deliberate pull an agent makes when that
summary is too terse for a given page. Keeping full bodies behind a tool — not
a resource — is what stops the large wiki from being advertised into context by
default.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    resolve_readable_repository_by_slug,
    wiki_page_payload,
)

_WIKI_PAGE_DESCRIPTION = (
    "Read ONE generated-wiki page in full — or a single named section of it — "
    "on demand.\n"
    "Use when: the summarized wiki (the cograph_wiki_tree resource) is too "
    "terse for a topic and you need a page's full prose, diagrams, or code "
    "samples verbatim. `page` is a wiki slug from that summary's tree; pass "
    "`section` (a heading from that page's `sections` list) to pull just that "
    "section and save tokens.\n"
    "Do NOT use as your first wiki read — start from the summarized wiki "
    "resource and pull full pages only for the few that warrant depth. Do NOT "
    "use to search code (use cograph_retrieve / cograph_search_code)."
)


class WikiPageArgs(BaseModel):
    repository: str
    page: str
    section: str | None = None


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph_wiki_page",
        description=_WIKI_PAGE_DESCRIPTION,
    )
    async def wiki_page(
        repository: str,
        page: str,
        section: str | None = None,
        ctx: Context | None = None,
    ) -> object:
        args = WikiPageArgs(repository=repository, page=page, section=section)
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
