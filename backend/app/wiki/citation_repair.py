"""Lazy citation repair for already-persisted wiki pages.

Wiki pages are written once at generation time with frozen `?node=<uuid>`
hrefs in markdown. Two failure modes accumulate post-generation:

  1. **URL format**: pages persisted before commit `a882930` (Q1.0) carry
     `/repos/<uuid>/graph?node=<uuid>` hrefs. The FE only routes the
     slug shape `/repos/:host/:owner/:name/graph`, so every UUID-form
     citation falls through to NotFoundPage.
  2. **Stale node UUIDs**: a re-index can drop a code_node when its
     `(file_path, symbol_key)` changes (rename, move, signature change),
     leaving the persisted UUID without a current row. Click → 404
     → empty graph panel.

This module repairs both cases without re-running the writer:

  * URL-format upgrade: any `/repos/<repository_uuid>/...` href
    referencing the current repository is rewritten to the slug shape.
  * Stale-UUID rehydrate: when a `kind=node` href points at a missing
    UUID but the link text still carries the qualified_name, look up
    the current UUID by that name and patch the href in place. If the
    qualified_name is also gone, drop the link to bare backticked text
    and remove the citation from the page's `citations[]`.

The service writes back with a conditional `WHERE updated_at = :loaded`
guard so concurrent regen wins — repair is idempotent and bounded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.document import Document
from backend.app.wiki.citations import RepositorySlug

_DOC_TYPE = "wiki"

# Capture `[`labelled qn`](/repos/<uuid>/graph?node=<uuid>)`. The leading
# UUID identifies the repository — when it matches the current repo, we
# rewrite to the slug shape; the trailing UUID is the cited node.
_UUID = r"[0-9a-fA-F-]{36}"
_GRAPH_LINK_RE = re.compile(
    rf"\[(?P<label>`[^`]+`)\]\(/repos/(?P<repo>{_UUID})/graph\?node=(?P<node>{_UUID})\)"
)
# Slug-form graph link, with any host/owner/name shape. We only need to
# refresh `node=<uuid>` if the UUID is stale; the URL prefix is already
# correct.
_GRAPH_LINK_SLUG_RE = re.compile(
    rf"\[(?P<label>`[^`]+`)\]\(/repos/(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<name>[^/]+)/graph\?node=(?P<node>{_UUID})\)"
)
# Capture `[label](/repos/<uuid>/docs/<slug>...)` so we can upgrade the
# URL prefix without touching the doc-slug or fragment.
_DOC_LINK_RE = re.compile(
    rf"\[(?P<label>[^\]]+)\]\(/repos/(?P<repo>{_UUID})/docs/(?P<rest>[^)]+)\)"
)


@dataclass(frozen=True)
class RepairResult:
    """Per-page repair counters surfaced to the FE for the chip + toast."""

    patched: int = 0
    dropped: int = 0
    unchanged: int = 0
    url_format_upgraded: int = 0
    raced: bool = False
    page_loaded: bool = True
    new_citations: list[dict[str, object]] = field(default_factory=list)
    new_content: str = ""

    @property
    def changed(self) -> bool:
        return (
            self.patched > 0
            or self.dropped > 0
            or self.url_format_upgraded > 0
        )


def repair_markdown(
    *,
    content: str,
    citations: list[dict[str, object]],
    repository_id: UUID,
    repo_slug: RepositorySlug,
    existing_node_ids: set[UUID],
    qn_to_node_id: dict[str, UUID],
) -> RepairResult:
    """Pure-function repair of one page's markdown + citations payload.

    No DB. Caller pre-loads the existence map and the qualified-name map
    so this function is fully deterministic and testable. The function
    returns the rewritten content + citation list plus per-page counters.
    """

    repo_str = str(repository_id).lower()
    patched = 0
    dropped = 0
    url_format_upgraded = 0

    # Collect citation IDs we want to delete (kind=node entries that are
    # genuinely gone). We mutate after the markdown rewrite so we can
    # see which UUIDs disappeared.
    surviving_uuid_by_old: dict[str, str] = {}
    drop_uuids: set[str] = set()

    def _resolve_node(label: str, current_uuid: str) -> tuple[str, str] | None:
        """Decide what to do with a `kind=node` link.

        Returns (new_uuid, qualified_name) if the link should be rewritten
        to point at `new_uuid`. Returns ("", qualified_name) if the link
        should be dropped (bare backticked text). Returns None when the
        UUID is still current — caller leaves it alone.
        """
        try:
            uuid_obj = UUID(current_uuid)
        except ValueError:
            return None
        if uuid_obj in existing_node_ids:
            return None
        # Stale. Try to resolve by the qualified_name carried in the
        # link's backticked label.
        qn = _qualified_name_from_label(label)
        if qn and qn in qn_to_node_id:
            return str(qn_to_node_id[qn]), qn
        return "", qn or ""

    def _on_uuid_form(match: re.Match[str]) -> str:
        nonlocal patched, dropped, url_format_upgraded
        repo_in_url = match.group("repo").lower()
        # Only rewrite citations that name THIS repository. A href
        # pointing at a different repo's UUID is suspicious; leave it
        # alone (the citation was wrong before the migration).
        if repo_in_url != repo_str:
            return match.group(0)

        label = match.group("label")
        node_uuid = match.group("node")

        decision = _resolve_node(label, node_uuid)
        if decision is None:
            url_format_upgraded += 1
            return f"[{label}]({repo_slug.path}/graph?node={node_uuid})"
        new_uuid, _qn = decision
        if not new_uuid:
            drop_uuids.add(node_uuid.lower())
            dropped += 1
            return label
        surviving_uuid_by_old[node_uuid.lower()] = new_uuid
        patched += 1
        url_format_upgraded += 1
        return f"[{label}]({repo_slug.path}/graph?node={new_uuid})"

    def _on_slug_form(match: re.Match[str]) -> str:
        nonlocal patched, dropped
        host = match.group("host")
        owner = match.group("owner")
        name = match.group("name")
        # Only act on links to THIS repository's slug. Cross-repo links
        # are out of scope.
        if (host, owner, name) != (
            repo_slug.host,
            repo_slug.owner,
            repo_slug.name,
        ):
            return match.group(0)
        label = match.group("label")
        node_uuid = match.group("node")
        decision = _resolve_node(label, node_uuid)
        if decision is None:
            return match.group(0)
        new_uuid, _qn = decision
        if not new_uuid:
            drop_uuids.add(node_uuid.lower())
            dropped += 1
            return label
        surviving_uuid_by_old[node_uuid.lower()] = new_uuid
        patched += 1
        return f"[{label}]({repo_slug.path}/graph?node={new_uuid})"

    def _on_doc_uuid(match: re.Match[str]) -> str:
        nonlocal url_format_upgraded
        repo_in_url = match.group("repo").lower()
        if repo_in_url != repo_str:
            return match.group(0)
        url_format_upgraded += 1
        label = match.group("label")
        rest = match.group("rest")
        return f"[{label}]({repo_slug.path}/docs/{rest})"

    new_content = _GRAPH_LINK_RE.sub(_on_uuid_form, content)
    new_content = _GRAPH_LINK_SLUG_RE.sub(_on_slug_form, new_content)
    new_content = _DOC_LINK_RE.sub(_on_doc_uuid, new_content)

    new_citations: list[dict[str, object]] = []
    for entry in citations:
        if not isinstance(entry, dict):
            new_citations.append(entry)
            continue
        kind = str(entry.get("kind", ""))
        cid = str(entry.get("id", "")).lower()
        if kind != "node":
            new_citations.append(entry)
            continue
        if cid in drop_uuids:
            continue
        replacement = surviving_uuid_by_old.get(cid)
        if replacement is not None:
            new_entry = dict(entry)
            new_entry["id"] = replacement
            new_citations.append(new_entry)
        else:
            new_citations.append(entry)

    unchanged = sum(
        1
        for entry in citations
        if isinstance(entry, dict) and str(entry.get("kind", "")) == "node"
    ) - patched - dropped

    return RepairResult(
        patched=patched,
        dropped=dropped,
        unchanged=max(unchanged, 0),
        url_format_upgraded=url_format_upgraded,
        raced=False,
        page_loaded=True,
        new_citations=new_citations,
        new_content=new_content,
    )


def _qualified_name_from_label(label: str) -> str | None:
    r"""Extract the qualified_name from a `\`pkg.Type.method\`` link label.

    The citation resolver writes `[\`{row.qualified_name}\`]({anchor})` —
    the qualified_name is the entire backtick contents. For an auto-link
    or hand-edited variant the label might contain extra text; we only
    repair when the label is pure-backtick (the resolver's canonical
    form). Otherwise we cannot be confident the backtick contents is a
    qualified_name and not a free-form code span, and we'd rather skip
    the rewrite than guess wrong.
    """
    stripped = label.strip()
    if not stripped.startswith("`") or not stripped.endswith("`"):
        return None
    inner = stripped[1:-1].strip()
    if not inner or "(" in inner or " " in inner:
        return None
    return inner


async def repair_page_citations(
    *,
    session: AsyncSession,
    repository_id: UUID,
    repo_slug: RepositorySlug,
    slug: str,
) -> RepairResult:
    """Load one wiki page, repair stale citations + URL format, write back.

    The write is guarded by `WHERE updated_at = <loaded value>` so a
    concurrent wiki regen wins (regen rewrites everything; this no-ops).
    """

    document = await session.scalar(
        select(Document).where(
            Document.repository_id == repository_id,
            Document.slug == slug,
            Document.doc_type == _DOC_TYPE,
        )
    )
    if document is None:
        return RepairResult(page_loaded=False)

    citations = list(document.citations or [])
    cited_uuids: set[UUID] = set()
    cited_qns: set[str] = set()
    for entry in citations:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("kind", "")) != "node":
            continue
        try:
            cited_uuids.add(UUID(str(entry["id"])))
        except (KeyError, ValueError):
            continue

    # Also harvest qualified_names referenced from the markdown link
    # text — they're the only handle we have on cited nodes whose UUIDs
    # have been deleted by a re-index.
    for match in _GRAPH_LINK_RE.finditer(document.content or ""):
        qn = _qualified_name_from_label(match.group("label"))
        if qn:
            cited_qns.add(qn)
    for match in _GRAPH_LINK_SLUG_RE.finditer(document.content or ""):
        qn = _qualified_name_from_label(match.group("label"))
        if qn:
            cited_qns.add(qn)

    existing_node_ids: set[UUID] = set()
    if cited_uuids:
        rows = await session.scalars(
            select(CodeNode.id).where(
                CodeNode.repository_id == repository_id,
                CodeNode.id.in_(cited_uuids),
            )
        )
        existing_node_ids = set(rows.all())

    qn_to_node_id: dict[str, UUID] = {}
    if cited_qns:
        rows = await session.execute(
            select(CodeNode.qualified_name, CodeNode.id).where(
                CodeNode.repository_id == repository_id,
                CodeNode.qualified_name.in_(cited_qns),
            )
        )
        qn_to_node_id = {row.qualified_name: row.id for row in rows.all()}

    result = repair_markdown(
        content=document.content or "",
        citations=citations,
        repository_id=repository_id,
        repo_slug=repo_slug,
        existing_node_ids=existing_node_ids,
        qn_to_node_id=qn_to_node_id,
    )

    if not result.changed:
        return result

    loaded_content = document.content or ""

    # Conditional update — if a regen committed between our load and
    # write, `documents.content` no longer matches what we patched.
    # That's the correct outcome: the regen produced a freshly-resolved
    # page, and our patch would be stale. We guard on content rather
    # than `updated_at` because SQLite's CURRENT_TIMESTAMP is
    # second-precision while SQLAlchemy serializes Python datetimes
    # with microseconds in WHERE clauses, breaking timestamp-based
    # optimistic locks in test environments.
    stmt = (
        update(Document)
        .where(
            Document.id == document.id,
            Document.content == loaded_content,
        )
        .values(
            content=result.new_content,
            citations=result.new_citations,
        )
    )
    exec_result = await session.execute(stmt)
    if exec_result.rowcount == 0:
        return RepairResult(
            patched=result.patched,
            dropped=result.dropped,
            unchanged=result.unchanged,
            url_format_upgraded=result.url_format_upgraded,
            raced=True,
            page_loaded=True,
            new_citations=result.new_citations,
            new_content=result.new_content,
        )
    await session.commit()
    return result
