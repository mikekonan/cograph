"""Stage 5: deterministic resolution of LLM-emitted citation placeholders.

The page-writer prompt instructs the LLM to emit two placeholder kinds:

    [[node:fully.qualified.Name]]    -> code_nodes lookup by qualified_name
    [[doc:docs/foo.md#section]]      -> repo_document_chunks lookup by file + heading

Each placeholder is replaced in-place with a markdown link to the resolved
anchor. Unresolved placeholders render as a `⚠️ unresolved: <key>` chip and
are recorded for the per-page quality telemetry. The pipeline runs a
pre-validation pass before final resolution and triggers a single repair
re-prompt when the writer cited an identifier that isn't in the chunk set.

`[[file:…]]` was a citation kind in V1; the writer prompt no longer
advertises it because file paths belong in prose, not as link targets.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.repo_docs.slug import build_slug_map
from backend.app.wiki.schemas import ResolvedCitation

logger = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\[\[(node|doc):([^\]]+)\]\]")


@dataclass(frozen=True)
class RepositorySlug:
    """Compound `host/owner/name` identity used in user-facing URLs.

    Wiki citations are persisted with the slug-form anchor so they match
    the FE route shape (`/repos/:host/:owner/:name/...`). The repository
    UUID is still passed alongside for DB lookups; only the rendered
    href uses the slug.
    """

    host: str
    owner: str
    name: str

    @property
    def path(self) -> str:
        return f"/repos/{self.host}/{self.owner}/{self.name}"

# Agents occasionally emit `` `[[node:Foo.Bar]]` `` (the placeholder wrapped in
# inline-code backticks). After resolution that becomes `` `[`Foo.Bar`](url)` ``
# — outer backticks turn the link into inline-code, breaking the markdown.
# Pre-strip backticks immediately around any placeholder.
_BACKTICKED_PLACEHOLDER_RE = re.compile(r"`(\[\[(?:node|doc):[^\]]+\]\])`")

# --- Stage 5b: symbol auto-linking ------------------------------------------

# Spans we MUST NOT touch when scanning for identifiers to auto-link:
#   * fenced code blocks (full ```…``` regions, including diagrams)
#   * existing `[[node:…]]` / `[[doc:…]]` placeholders
#   * markdown links (entire `[label](url)` form — wrapping inside a link
#     produces broken nested-link markdown).
_AUTO_LINK_SKIP_RE = re.compile(
    r"```[a-zA-Z0-9_+-]*\n.*?\n```"  # fenced code blocks
    r"|\[\[(?:node|doc):[^\]]+\]\]"  # existing citation placeholders
    r"|\[[^\]]*?\]\([^)\n]+\)",  # markdown links (greedy URL up to ')')
    re.DOTALL,
)

# Backticked inline span — `…`. We allow Go decorators (`*Foo`, `&Foo`)
# inside the backticks; they're stripped before lookup.
_BACKTICK_RE = re.compile(r"`(?P<inner>[^`\n]+?)`")

# A bare dotted qualified name like `pkg.Type.Member` or Go-style
# `pkg.Generator`. Anchored against word boundaries so `foo.Bar.baz` isn't
# fragmented. The trailing lookahead allows a sentence-ending period
# (`Outro pkg.Generator.`) — only a `.` followed by another identifier
# char is treated as part of the qualified name. The first segment may
# start lowercase (Go package convention); the DB lookup against
# `code_nodes.qualified_name` is the real filter.
_BARE_DOTTED_RE = re.compile(
    r"(?<![\w.])[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+(?!\w|\.\w)"
)

# Single-token identifier shape (used for backticked single-token candidates).
_IDENT_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DOTTED_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_DECORATOR_PREFIX_RE = re.compile(r"^[*&]+")

# Common-word stoplist — primitive types and English words that show up as
# leaf names of legitimate code nodes in some codebases. Auto-linking these
# generates noisy "Object" / "String" links; the plan calls it out as a
# false-positive risk.
_AUTO_LINK_STOPLIST: frozenset[str] = frozenset(
    {
        "any",
        "Array",
        "bool",
        "Boolean",
        "byte",
        "Dict",
        "Error",
        "Exception",
        "False",
        "float32",
        "float64",
        "int",
        "int32",
        "int64",
        "List",
        "Map",
        "nil",
        "None",
        "Number",
        "Object",
        "rune",
        "Set",
        "String",
        "string",
        "true",
        "True",
        "uint",
        "uint32",
        "uint64",
    }
)

# Keep the warning marker stable so the FE renderer can match on it without
# parsing arbitrary prose. `MarkdownRenderer.tsx` (PR4) styles this token.
UNRESOLVED_MARKER = "⚠️ unresolved: "

# `repo_documents` indexes every text file in the checkout (.go, .yaml,
# go.mod, …). Wiki citations only land on the `/repos/:host/:owner/:name/docs/:slug` page
# which renders markdown via `MarkdownRenderer`; pointing it at a `.go` file
# yields a broken page. We restrict `[[doc:…]]` resolution to the same
# extensions that `repo_docs.slug._STRIP_EXTENSIONS` recognises as
# documentation, plus a couple of common docs flavours.
_DOC_CITATION_EXTENSIONS: tuple[str, ...] = (
    ".md",
    ".mdx",
    ".markdown",
    ".rst",
    ".txt",
)


@dataclass(slots=True, frozen=True)
class _NodeRow:
    id: UUID
    qualified_name: str
    name: str
    file_path: str
    start_line: int
    end_line: int


@dataclass(slots=True, frozen=True)
class _DocRow:
    chunk_id: UUID
    file_path: str
    title: str | None
    heading_path: list[str]


class CitationResolver:
    """Deterministic placeholder → ResolvedCitation resolver."""

    async def resolve_page(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        repo_slug: RepositorySlug,
        markdown: str,
    ) -> tuple[str, list[ResolvedCitation], list[str]]:
        """Resolve all `[[…]]` placeholders in one page.

        Returns:
            (rendered_markdown, citations, unresolved_placeholder_keys)
        """
        markdown = _BACKTICKED_PLACEHOLDER_RE.sub(r"\1", markdown)
        matches = list(PLACEHOLDER_RE.finditer(markdown))
        if not matches:
            return markdown, [], []

        node_targets: set[str] = set()
        doc_targets: set[str] = set()
        for match in matches:
            kind = match.group(1)
            value = match.group(2).strip()
            if not value:
                continue
            if kind == "node":
                node_targets.add(value)
            elif kind == "doc":
                doc_targets.add(_normalise_file_path(_strip_doc_anchor(value)[0]))

        nodes_by_qn = await _load_nodes(
            session=session, repository_id=repository_id, names=node_targets
        )
        docs_by_path = await _load_doc_chunks(
            session=session, repository_id=repository_id, paths=doc_targets
        )
        slug_by_path: dict[str, str] = {}
        if docs_by_path:
            slug_by_path = await _load_doc_slug_map(
                session=session, repository_id=repository_id
            )

        citations: list[ResolvedCitation] = []
        unresolved: list[str] = []
        seen_citation_keys: set[tuple[str, str]] = set()

        def _add_citation(citation: ResolvedCitation) -> None:
            key = (citation.kind, citation.id)
            if key in seen_citation_keys:
                return
            seen_citation_keys.add(key)
            citations.append(citation)

        def _replace(match: re.Match[str]) -> str:
            kind = match.group(1)
            raw_value = match.group(2).strip()
            if not raw_value:
                return ""

            if kind == "node":
                row = nodes_by_qn.get(raw_value)
                if row is None:
                    key = f"node:{raw_value}"
                    unresolved.append(key)
                    return f"{UNRESOLVED_MARKER}{key}"
                _add_citation(
                    ResolvedCitation(
                        id=str(row.id),
                        kind="node",
                        label=row.name,
                        file_path=row.file_path,
                        start_line=row.start_line,
                        end_line=row.end_line,
                    )
                )
                anchor = _node_anchor(repo_slug=repo_slug, node_id=row.id)
                return f"[`{row.qualified_name}`]({anchor})"

            if kind == "doc":
                path_part, heading = _strip_doc_anchor(raw_value)
                path = _normalise_file_path(path_part)
                # Manifest / source-file references (`go.mod`, `*.go`, …) are
                # not markdown — the FE docs route can't render them. Rather
                # than leave a noisy `⚠️ unresolved: doc:go.mod` chip we
                # downgrade the placeholder to bare path text in prose. This
                # matches the prompt's "manifest paths are not citation
                # targets" rule without punishing the reader for the agent's
                # slip-up.
                if not _has_markdown_extension(path):
                    return path_part if path_part else raw_value
                chunks = docs_by_path.get(path) or []
                chosen = _pick_doc_chunk(chunks=chunks, heading=heading)
                if chosen is None:
                    key = f"doc:{raw_value}"
                    unresolved.append(key)
                    return f"{UNRESOLVED_MARKER}{key}"
                _add_citation(
                    ResolvedCitation(
                        id=str(chosen.chunk_id),
                        kind="repo_doc_chunk",
                        label=chosen.title or path,
                        file_path=chosen.file_path,
                        heading_path=list(chosen.heading_path),
                    )
                )
                slug = slug_by_path.get(chosen.file_path)
                if slug is None:
                    # Doc row exists but slug map didn't see it (race or
                    # stale read). Treat as unresolved rather than emit a
                    # broken FE URL.
                    key = f"doc:{raw_value}"
                    unresolved.append(key)
                    return f"{UNRESOLVED_MARKER}{key}"
                anchor = _doc_anchor(
                    repo_slug=repo_slug,
                    slug=slug,
                    heading=heading,
                )
                label = chosen.title or path
                return f"[{label}]({anchor})"

            return match.group(0)

        rendered = PLACEHOLDER_RE.sub(_replace, markdown)
        return rendered, citations, unresolved

    async def prevalidate_page(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        markdown: str,
    ) -> list[str]:
        """Return the placeholder keys (`node:Foo` / `doc:bar.md`) that
        cannot be resolved against the indexed state. Used by the pipeline
        to decide whether to fire a single repair re-prompt before final
        rendering — keeps the resolver's authoritative lookup as the only
        source of truth for "does this exist".
        """
        markdown = _BACKTICKED_PLACEHOLDER_RE.sub(r"\1", markdown)
        matches = list(PLACEHOLDER_RE.finditer(markdown))
        if not matches:
            return []

        node_targets: set[str] = set()
        doc_targets: set[str] = set()
        seen_keys: set[str] = set()
        keys_in_order: list[str] = []
        for match in matches:
            kind = match.group(1)
            value = match.group(2).strip()
            if not value:
                continue
            key = f"{kind}:{value}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            keys_in_order.append(key)
            if kind == "node":
                node_targets.add(value)
            elif kind == "doc":
                doc_targets.add(_normalise_file_path(_strip_doc_anchor(value)[0]))

        nodes_by_qn = await _load_nodes(
            session=session, repository_id=repository_id, names=node_targets
        )
        docs_by_path = await _load_doc_chunks(
            session=session, repository_id=repository_id, paths=doc_targets
        )

        unresolved: list[str] = []
        for key in keys_in_order:
            kind, _, value = key.partition(":")
            if kind == "node":
                if value not in nodes_by_qn:
                    unresolved.append(key)
            elif kind == "doc":
                path_part, heading = _strip_doc_anchor(value)
                path = _normalise_file_path(path_part)
                # Non-markdown citations are silently downgraded to prose
                # by `resolve_page` — they aren't a "missing" signal worth
                # firing a repair re-prompt over.
                if not _has_markdown_extension(path):
                    continue
                chunks = docs_by_path.get(path) or []
                if _pick_doc_chunk(chunks=chunks, heading=heading) is None:
                    unresolved.append(key)
        return unresolved


def _normalise_file_path(value: str) -> str:
    return value.strip().lstrip("./")


def _strip_doc_anchor(value: str) -> tuple[str, str | None]:
    if "#" not in value:
        return value, None
    head, _, tail = value.partition("#")
    head = head.strip()
    tail = tail.strip()
    return head, (tail or None)


def _heading_anchor(heading_path: list[str]) -> str:
    if not heading_path:
        return ""
    last = heading_path[-1].strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", last).strip("-")


def _pick_doc_chunk(*, chunks: list[_DocRow], heading: str | None) -> _DocRow | None:
    if not chunks:
        return None
    if heading is None:
        return chunks[0]
    target = heading.strip().lower()
    for chunk in chunks:
        if _heading_anchor(chunk.heading_path) == target:
            return chunk
        if any(seg.strip().lower() == target for seg in chunk.heading_path):
            return chunk
    return chunks[0]


def _node_anchor(*, repo_slug: RepositorySlug, node_id: UUID) -> str:
    # The graph page reads the focused node from the `node` query param
    # (router.tsx: `/repos/:host/:owner/:name/graph` + `useGraph(?node=…)`).
    # The slug shape must match the FE route exactly — `/repos/<uuid>/graph`
    # falls through to the `*` catch-all and renders NotFoundPage.
    return f"{repo_slug.path}/graph?node={node_id}"


def _doc_anchor(
    *, repo_slug: RepositorySlug, slug: str, heading: str | None
) -> str:
    # The FE docs route is keyed by the slug column produced by
    # `repo_docs.slug.build_slug_map`, NOT by the raw on-disk file_path.
    # Wiki citations carrying a raw `[[doc:docs/foo.md]]` placeholder are
    # rewritten to `/repos/:host/:owner/:name/docs/:slug` so the link
    # resolves to the same row the docs API serves.
    base = f"{repo_slug.path}/docs/{slug}"
    if heading:
        return f"{base}#{heading.strip().lower()}"
    return base


async def _load_nodes(
    *, session: AsyncSession, repository_id: UUID, names: set[str]
) -> dict[str, _NodeRow]:
    if not names:
        return {}
    stmt = select(
        CodeNode.id,
        CodeNode.qualified_name,
        CodeNode.name,
        CodeNode.file_path,
        CodeNode.start_line,
        CodeNode.end_line,
    ).where(
        CodeNode.repository_id == repository_id,
        CodeNode.qualified_name.in_(names),
    )
    rows = (await session.execute(stmt)).all()
    return {
        row.qualified_name: _NodeRow(
            id=row.id,
            qualified_name=row.qualified_name,
            name=row.name,
            file_path=row.file_path,
            start_line=int(row.start_line or 0),
            end_line=int(row.end_line or 0),
        )
        for row in rows
    }


def _split_safe_unsafe(markdown: str) -> list[tuple[str, bool]]:
    """Yield (text, is_safe) pairs covering the whole markdown.

    A run is "unsafe" (we never insert auto-links into it) if it sits inside
    a fenced code block, an existing `[[…]]` placeholder, or a markdown link.
    The remaining "safe" runs are scanned for backticked or dotted symbols.
    """
    parts: list[tuple[str, bool]] = []
    cursor = 0
    for match in _AUTO_LINK_SKIP_RE.finditer(markdown):
        if match.start() > cursor:
            parts.append((markdown[cursor : match.start()], True))
        parts.append((match.group(0), False))
        cursor = match.end()
    if cursor < len(markdown):
        parts.append((markdown[cursor:], True))
    return parts


def _strip_decorator(token: str) -> str:
    return _DECORATOR_PREFIX_RE.sub("", token).strip()


def _collect_auto_link_candidates(
    markdown: str,
) -> tuple[set[str], set[str]]:
    """Scan safe regions for two candidate buckets.

    Returns:
        - `dotted_qns`: dotted qualified-name candidates (must match
          `code_nodes.qualified_name` exactly).
        - `backtick_singles`: single-token candidates (looked up by `name`
          scoped to the page's source nodes).
    """
    dotted_qns: set[str] = set()
    backtick_singles: set[str] = set()
    for text, is_safe in _split_safe_unsafe(markdown):
        if not is_safe:
            continue
        for backtick_match in _BACKTICK_RE.finditer(text):
            inner = _strip_decorator(backtick_match.group("inner").strip())
            if not inner:
                continue
            if _DOTTED_TOKEN_RE.match(inner):
                dotted_qns.add(inner)
            elif _IDENT_TOKEN_RE.match(inner) and inner not in _AUTO_LINK_STOPLIST:
                backtick_singles.add(inner)
        for bare_match in _BARE_DOTTED_RE.finditer(text):
            value = bare_match.group(0)
            if value in _AUTO_LINK_STOPLIST:
                continue
            dotted_qns.add(value)
    return dotted_qns, backtick_singles


async def auto_link_qualified_names(
    *,
    session: AsyncSession,
    repository_id: UUID,
    markdown: str,
    page_node_ids: list[UUID] | None = None,
    max_links: int = 30,
) -> tuple[str, int]:
    """Wrap recognized qualified names in `[[node:…]]` placeholders so the
    citation resolver renders them as anchors.

    Strictness rules (per the PR3 plan, to avoid false positives):

      * Dotted forms (`Foo.Bar`, `pkg.Type.Member`) — backticked or bare —
        must match `code_nodes.qualified_name` exactly.
      * Backticked single-token identifiers — must match a `code_nodes.name`
        scoped to `page_node_ids`. Single-token bare names are NEVER
        auto-linked (too noisy).
      * Common primitive types and English words (`Object`, `String`, …)
        sit on a stoplist and are skipped regardless.

    Returns `(rendered_markdown, links_added)`. `links_added` feeds the
    `WikiPageQuality.auto_links_added` chip.
    """
    if not markdown:
        return markdown, 0

    dotted_qns, backtick_singles = _collect_auto_link_candidates(markdown)
    if not dotted_qns and not backtick_singles:
        return markdown, 0

    qn_to_resolved: dict[str, str] = {}
    if dotted_qns:
        stmt = select(CodeNode.qualified_name).where(
            CodeNode.repository_id == repository_id,
            CodeNode.qualified_name.in_(dotted_qns),
        )
        rows = (await session.execute(stmt)).all()
        for row in rows:
            qn_to_resolved[row.qualified_name] = row.qualified_name

    name_to_resolved: dict[str, str] = {}
    if backtick_singles and page_node_ids:
        # Look up by `name` only when the candidate appears as a code_node
        # within the page's source-node scope. Multi-match (>1 row for the
        # same leaf name) → we drop it as ambiguous to avoid wrong-target
        # auto-links.
        stmt = select(CodeNode.name, CodeNode.qualified_name).where(
            CodeNode.repository_id == repository_id,
            CodeNode.id.in_(page_node_ids),
            CodeNode.name.in_(backtick_singles),
        )
        rows = (await session.execute(stmt)).all()
        seen_names: dict[str, str] = {}
        ambiguous: set[str] = set()
        for row in rows:
            if row.name in seen_names and seen_names[row.name] != row.qualified_name:
                ambiguous.add(row.name)
                continue
            seen_names[row.name] = row.qualified_name
        for name, qn in seen_names.items():
            if name not in ambiguous:
                name_to_resolved[name] = qn

    if not qn_to_resolved and not name_to_resolved:
        return markdown, 0

    counter = {"n": 0}

    def _replace_in_safe(safe_text: str) -> str:
        if counter["n"] >= max_links:
            return safe_text

        def _on_backtick(match: re.Match[str]) -> str:
            if counter["n"] >= max_links:
                return match.group(0)
            inner_raw = match.group("inner").strip()
            inner = _strip_decorator(inner_raw)
            if not inner:
                return match.group(0)
            target: str | None = None
            if _DOTTED_TOKEN_RE.match(inner):
                target = qn_to_resolved.get(inner)
            elif _IDENT_TOKEN_RE.match(inner) and inner not in _AUTO_LINK_STOPLIST:
                target = name_to_resolved.get(inner)
            if target is None:
                return match.group(0)
            counter["n"] += 1
            return f"[[node:{target}]]"

        safe_text = _BACKTICK_RE.sub(_on_backtick, safe_text)
        if counter["n"] >= max_links:
            return safe_text

        def _on_bare(match: re.Match[str]) -> str:
            if counter["n"] >= max_links:
                return match.group(0)
            value = match.group(0)
            target = qn_to_resolved.get(value)
            if target is None:
                return value
            counter["n"] += 1
            return f"[[node:{target}]]"

        # Re-split so the bare pass skips `[[node:…]]` placeholders we
        # just inserted (otherwise the bare regex re-matches the dotted
        # name inside the placeholder we wrote).
        out_parts: list[str] = []
        for sub_text, sub_safe in _split_safe_unsafe(safe_text):
            if sub_safe:
                out_parts.append(_BARE_DOTTED_RE.sub(_on_bare, sub_text))
            else:
                out_parts.append(sub_text)
        return "".join(out_parts)

    rebuilt: list[str] = []
    for text, is_safe in _split_safe_unsafe(markdown):
        rebuilt.append(_replace_in_safe(text) if is_safe else text)
    return "".join(rebuilt), counter["n"]


async def _load_doc_chunks(
    *, session: AsyncSession, repository_id: UUID, paths: set[str]
) -> dict[str, list[_DocRow]]:
    if not paths:
        return {}
    # Only join chunks whose underlying document is a markdown-flavoured
    # file. The repo-doc indexer ingests every text file in the checkout
    # (.go, go.mod, .yaml, etc.) so without this filter the resolver would
    # happily emit `/repos/:host/:owner/:name/docs/<some.go>` URLs that render as a broken
    # FE page (the docs route only has a markdown renderer wired up).
    markdown_paths = {p for p in paths if _has_markdown_extension(p)}
    if not markdown_paths:
        return {}
    stmt = (
        select(
            RepoDocumentChunk.id,
            RepoDocumentChunk.heading_path,
            RepoDocumentChunk.chunk_index,
            RepoDocument.file_path,
            RepoDocument.title,
        )
        .join(RepoDocument, RepoDocumentChunk.document_id == RepoDocument.id)
        .where(
            RepoDocument.repository_id == repository_id,
            RepoDocument.file_path.in_(markdown_paths),
        )
        .order_by(RepoDocument.file_path, RepoDocumentChunk.chunk_index)
    )
    rows = (await session.execute(stmt)).all()
    grouped: dict[str, list[_DocRow]] = {}
    for row in rows:
        grouped.setdefault(row.file_path, []).append(
            _DocRow(
                chunk_id=row.id,
                file_path=row.file_path,
                title=row.title,
                heading_path=list(row.heading_path or []),
            )
        )
    return grouped


def _has_markdown_extension(file_path: str) -> bool:
    lowered = file_path.lower()
    return any(lowered.endswith(ext) for ext in _DOC_CITATION_EXTENSIONS)


async def _load_doc_slug_map(
    *, session: AsyncSession, repository_id: UUID
) -> dict[str, str]:
    """Return a `file_path → slug` map for every doc in the repo.

    The FE docs route is `/repos/:host/:owner/:name/docs/:slug` (`get_doc_by_slug`); the
    slug is derived deterministically by `repo_docs.slug.build_slug_map`,
    which appends a UUID-based suffix on collision. We rebuild the same
    map here so the URLs we emit hit the same row the API returns.

    Loading the full doc set is O(N) per page resolution but the rows are
    tiny (id + file_path) and N is bounded by the indexer; fine for the
    handful of resolutions the pipeline runs per page.
    """
    stmt = (
        select(RepoDocument.id, RepoDocument.file_path)
        .where(RepoDocument.repository_id == repository_id)
        .order_by(RepoDocument.file_path.asc())
    )
    rows = (await session.execute(stmt)).all()
    items = [(row.id, row.file_path) for row in rows]
    slug_to_id = build_slug_map(items)
    id_to_slug = {doc_id: slug for slug, doc_id in slug_to_id.items()}
    return {file_path: id_to_slug[doc_id] for doc_id, file_path in items}
