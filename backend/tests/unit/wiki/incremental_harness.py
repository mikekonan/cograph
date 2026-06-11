"""Test harness for incremental-wiki equivalence testing.

The equivalence claim under test: with a deterministic provider, embedder,
and retriever, for any mutation M over repo state S0,

    business_view(full(S0); incremental(M(S0)))
        == business_view(full(M(S0)))

and the incremental run makes ONLY the LLM calls its dirty set justifies.

Pieces:

- `ScriptedRepo` — declarative repo state with ingest-faithful mutators:
  `change_node`/`delete_node`/`add_node` delete + recreate the row (new
  UUID — exactly what `ingest` does for a changed file), while
  `change_node_summary` updates in place (a neighbor-change regeneration
  keeps the node UUID alive — the case content hashes in the fingerprint
  exist for).

- `DeterministicDbHybrid` — a fake `HybridRetriever` that is a pure
  function of DB state: token-overlap scoring, zero-overlap candidates
  excluded (BM25-ish), ties broken by qualified_name. No rank jitter, so
  any fingerprint change in a test is a real evidence change.

- Scripted provider queues — page bodies are templates over the *current
  content* of the page's cited nodes, so a content change that must be
  rewritten produces a different body and `business_view` equality is a
  meaningful check, not a tautology. Each body cites `[[node:qn]]` after
  a queued `read_node_by_qn` tool call, so the T3 citation gate passes
  through the same ledger mechanics as production.

- `StrictProvider` — hard-fails on any LLM call.
  `FakeStructuredProvider.complete_with_tools` silently returns
  `budget_exhausted` on an empty queue; zero-call assertions MUST use
  StrictProvider instead of an empty fake.

- `business_view` — the cross-repo comparison projection. Node UUIDs and
  the repo base path are normalised out of rendered content (citation
  anchors embed both), source ids map back to qualified names / doc
  positions, and run-local noise (row ids, sync_run_id, timestamps,
  source_hash, agent telemetry) is excluded.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.document import Document
from backend.app.models.enums import CodeNodeType
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.rag.retriever import RetrievedChunk
from backend.app.wiki.llm_client import FakeStructuredProvider
from backend.app.wiki.pipeline import WikiGenerationConfig, run_wiki_generation
from backend.app.wiki.retrieval import WikiRetrievalService
from backend.app.wiki.schemas import (
    MindMap,
    PagePlan,
    RepoOverview,
    WikiGenerationResult,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


# ---------------------------------------------------------------------------
# ScriptedRepo
# ---------------------------------------------------------------------------


class ScriptedRepo:
    """One repository's worth of declarative state + ingest-faithful mutators."""

    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.node_ids: dict[str, UUID] = {}
        self.node_contents: dict[str, str] = {}
        self.node_summaries: dict[str, str | None] = {}
        self.doc_ids: dict[str, UUID] = {}
        self.chunk_ids: dict[tuple[str, int], UUID] = {}

    @property
    def id(self) -> UUID:
        return self.repo.id

    @classmethod
    async def create(cls, session: AsyncSession, name: str) -> "ScriptedRepo":
        repo = Repository(
            host="example.com",
            git_url=f"https://github.com/test/{name}",
            name=name,
            owner="test",
            branch="main",
            status="ready",
            sync_schedule="manual",
            last_commit="seed",
        )
        session.add(repo)
        await session.flush()
        return cls(repo)

    # -- code nodes ---------------------------------------------------------

    async def add_node(
        self,
        session: AsyncSession,
        qualified_name: str,
        *,
        content: str,
        summary: str | None = None,
        language: str = "python",
        node_type: CodeNodeType = CodeNodeType.FUNCTION,
        file_path: str | None = None,
    ) -> UUID:
        leaf = qualified_name.rsplit(".", 1)[-1]
        node = CodeNode(
            repository_id=self.repo.id,
            file_path=file_path or f"src/{leaf}.py",
            qualified_name=qualified_name,
            node_type=node_type,
            name=leaf,
            language=language,
            start_line=1,
            end_line=max(1, content.count("\n") + 1),
            content=content,
            content_hash=_sha(content),
        )
        session.add(node)
        await session.flush()
        if summary is not None:
            session.add(
                CodeNodeSummary(
                    code_node_id=node.id,
                    repository_id=self.repo.id,
                    summary=summary,
                    importance=1.0,
                    content_hash=_sha(content),
                    neighbor_hash="",
                    model="fake-summarizer-v1",
                )
            )
            await session.flush()
        self.node_ids[qualified_name] = node.id
        self.node_contents[qualified_name] = content
        self.node_summaries[qualified_name] = summary
        return node.id

    async def change_node(
        self,
        session: AsyncSession,
        qualified_name: str,
        *,
        content: str,
        summary: str | None = None,
    ) -> UUID:
        """Ingest semantics for a changed file: delete + recreate ⇒ new UUID."""
        await self.delete_node(session, qualified_name)
        return await self.add_node(
            session,
            qualified_name,
            content=content,
            summary=(
                summary
                if summary is not None
                else self.node_summaries.get(qualified_name)
            ),
        )

    async def change_node_summary(
        self, session: AsyncSession, qualified_name: str, summary: str
    ) -> None:
        """Neighbor-change regeneration: summary text moves, node UUID lives."""
        node_id = self.node_ids[qualified_name]
        row = (
            await session.execute(
                select(CodeNodeSummary).where(CodeNodeSummary.code_node_id == node_id)
            )
        ).scalar_one()
        row.summary = summary
        await session.flush()
        self.node_summaries[qualified_name] = summary

    async def delete_node(self, session: AsyncSession, qualified_name: str) -> None:
        node_id = self.node_ids.pop(qualified_name)
        node = await session.get(CodeNode, node_id)
        assert node is not None
        await session.execute(
            sa_delete(CodeNodeSummary).where(CodeNodeSummary.code_node_id == node_id)
        )
        await session.delete(node)
        await session.flush()
        self.node_contents.pop(qualified_name, None)
        self.node_summaries.pop(qualified_name, None)

    # -- repo docs ------------------------------------------------------------

    async def add_doc(
        self,
        session: AsyncSession,
        file_path: str,
        *,
        title: str,
        chunks: list[str],
        heading: str | None = None,
    ) -> None:
        content = "\n\n".join(chunks)
        doc = RepoDocument(
            repository_id=self.repo.id,
            file_path=file_path,
            title=title,
            content=content,
            content_hash=_sha(content),
            bytes=len(content.encode("utf-8")),
        )
        session.add(doc)
        await session.flush()
        self.doc_ids[file_path] = doc.id
        for index, chunk_content in enumerate(chunks):
            chunk = RepoDocumentChunk(
                document_id=doc.id,
                chunk_index=index,
                heading_path=[heading or title],
                content=chunk_content,
                content_hash=_sha(chunk_content),
            )
            session.add(chunk)
            await session.flush()
            self.chunk_ids[(file_path, index)] = chunk.id

    async def change_doc_chunk(
        self, session: AsyncSession, file_path: str, index: int, content: str
    ) -> None:
        """Ingest semantics: the chunk row is recreated (new UUID); the
        parent document's content/hash move but title + heading stay, so
        the change is retrieval-visible without being structural."""
        old = await session.get(RepoDocumentChunk, self.chunk_ids[(file_path, index)])
        assert old is not None
        heading_path = list(old.heading_path)
        await session.delete(old)
        await session.flush()
        chunk = RepoDocumentChunk(
            document_id=self.doc_ids[file_path],
            chunk_index=index,
            heading_path=heading_path,
            content=content,
            content_hash=_sha(content),
        )
        session.add(chunk)
        doc = await session.get(RepoDocument, self.doc_ids[file_path])
        assert doc is not None
        doc.content = doc.content + f"\n<!-- rev {_sha(content)[:8]} -->"
        doc.content_hash = _sha(doc.content)
        await session.flush()
        self.chunk_ids[(file_path, index)] = chunk.id


# ---------------------------------------------------------------------------
# DeterministicDbHybrid
# ---------------------------------------------------------------------------


class DeterministicDbHybrid:
    """Fake `HybridRetriever`: pure function of DB state.

    Scores by token overlap between the query text and the candidate's
    content; zero-overlap candidates are excluded (like BM25); ties break
    on qualified_name / (file_path, chunk_index). Embeddings are ignored —
    determinism lives here, not in vector space.
    """

    async def retrieve(
        self,
        session: AsyncSession,
        *,
        query_text: str,
        query_embedding: list[float],
        repository_id: UUID | None = None,
        top_k: int = 10,
        stores: set[str] | None = None,
        **_: Any,
    ) -> list[RetrievedChunk]:
        del query_embedding
        query_tokens = _tokens(query_text)
        if stores == {"code"}:
            rows = (
                (
                    await session.execute(
                        select(CodeNode).where(CodeNode.repository_id == repository_id)
                    )
                )
                .scalars()
                .all()
            )
            scored = [
                (len(query_tokens & _tokens(node.content)), node) for node in rows
            ]
            ranked = sorted(
                ((s, n) for s, n in scored if s > 0),
                key=lambda pair: (-pair[0], pair[1].qualified_name),
            )[:top_k]
            return [
                RetrievedChunk(
                    store="code",
                    chunk_id=node.id,
                    content=node.content,
                    score=float(score),
                    metadata={
                        "qualified_name": node.qualified_name,
                        "file_path": node.file_path,
                        "start_line": node.start_line,
                        "end_line": node.end_line,
                        "language": node.language,
                    },
                )
                for score, node in ranked
            ]
        if stores == {"repo_docs"}:
            rows = (
                await session.execute(
                    select(RepoDocumentChunk, RepoDocument)
                    .join(
                        RepoDocument,
                        RepoDocumentChunk.document_id == RepoDocument.id,
                    )
                    .where(RepoDocument.repository_id == repository_id)
                )
            ).all()
            scored_docs = [
                (len(query_tokens & _tokens(chunk.content)), chunk, doc)
                for chunk, doc in rows
            ]
            ranked_docs = sorted(
                ((s, c, d) for s, c, d in scored_docs if s > 0),
                key=lambda triple: (
                    -triple[0],
                    triple[2].file_path,
                    triple[1].chunk_index,
                ),
            )[:top_k]
            return [
                RetrievedChunk(
                    store="repo_docs",
                    chunk_id=chunk.id,
                    content=chunk.content,
                    score=float(score),
                    metadata={
                        "file_path": doc.file_path,
                        "title": doc.title,
                        "heading_path": list(chunk.heading_path),
                        "chunk_index": chunk.chunk_index,
                    },
                )
                for score, chunk, doc in ranked_docs
            ]
        return []


def make_retriever() -> WikiRetrievalService:
    return WikiRetrievalService(
        hybrid=DeterministicDbHybrid(),  # type: ignore[arg-type]
        embedder=FakeEmbedProvider(dims=8),
    )


# ---------------------------------------------------------------------------
# Scripted provider
# ---------------------------------------------------------------------------


FAKE_MODEL = "fake-structured-v1"

# Identical for every page: Stage 4b synthesises diagrams concurrently, so
# per-page bodies popped from the shared FIFO could interleave.
MERMAID_BLOCK = "```mermaid\nflowchart LR\n  a --> b\n```"


@dataclass(frozen=True)
class ScriptedPage:
    """One planned page: which nodes it cites and how retrieval finds it.

    `purpose` doubles as the retrieval query — design its tokens to
    overlap exactly the node/doc contents the page should retrieve.
    """

    slug: str
    title: str
    purpose: str
    cites: tuple[str, ...] = ()
    parent_slug: str | None = None
    diagram: bool = False


def plan_for(pages: list[ScriptedPage]) -> PagePlan:
    return PagePlan.model_validate(
        {
            "pages": [
                {
                    "slug": page.slug,
                    "title": page.title,
                    "purpose": page.purpose,
                    "parent_slug": page.parent_slug,
                    "sources_hint": [],
                    "covers_questions": [],
                    "diagram": page.diagram,
                }
                for page in pages
            ]
        }
    )


def page_body(repo_state: ScriptedRepo, page: ScriptedPage) -> str:
    """Template over the CURRENT content of the page's cited nodes.

    The content digest in the body ties the page text to the node state:
    if a run was obliged to rewrite a page after a node change and didn't,
    `business_view` comparison fails on the body, not just on metadata.
    Cites are filtered to nodes that still exist — after a deletion, the
    writer (real or scripted) can only cite surviving evidence.
    """
    lines = [f"# {page.title}", "", page.purpose, ""]
    for qn in page.cites:
        if qn not in repo_state.node_contents:
            continue
        digest = _sha(repo_state.node_contents[qn])[:12]
        lines.append(f"The symbol [[node:{qn}]] currently implements `{digest}`.")
    return "\n".join(lines)


def queue_page_turns(
    provider: FakeStructuredProvider,
    repo_state: ScriptedRepo,
    page: ScriptedPage,
) -> None:
    tool_uses: list[tuple[str, dict[str, Any]]] = [
        ("read_node_by_qn", {"qualified_name": qn})
        for qn in page.cites
        if qn in repo_state.node_contents
    ]
    tool_uses.append(("write_page", {"markdown": page_body(repo_state, page)}))
    provider.queue_tool_turn(tool_uses=tool_uses)
    provider.queue_tool_turn(text="")


def queue_full_run(
    provider: FakeStructuredProvider,
    repo_state: ScriptedRepo,
    pages: list[ScriptedPage],
    *,
    write_slugs: set[str] | None = None,
) -> None:
    """Queue a full run: planning stages + page turns.

    `write_slugs` restricts the queued page turns — a full run after the
    salvage pass only rewrites dirty pages, so the queue must only
    contain those (extra/missing turns fail the drain check)."""
    provider.queue(
        RepoOverview(
            one_line="Scripted harness repo",
            long_description="Deterministic repo for incremental equivalence tests.",
        ).model_dump_json()
    )
    provider.queue(MindMap().model_dump_json())
    provider.queue(plan_for(pages).model_dump_json())
    written = [
        page for page in pages if write_slugs is None or page.slug in write_slugs
    ]
    for page in written:
        queue_page_turns(provider, repo_state, page)
    # Stage 4b runs after all page writes; one complete_text per written
    # diagram page. Clean diagram pages have no draft → no call.
    for page in written:
        if page.diagram:
            provider.queue(MERMAID_BLOCK)


def queue_incremental_run(
    provider: FakeStructuredProvider,
    repo_state: ScriptedRepo,
    pages: list[ScriptedPage],
    *,
    dirty_slugs: set[str],
) -> None:
    """Queue ONLY the dirty pages, in plan order. If the orchestrator
    rewrites a page outside this set the queue drains early and the run
    fails loudly; if it skips one of these, `assert_drained` fails."""
    dirty = [page for page in pages if page.slug in dirty_slugs]
    for page in dirty:
        queue_page_turns(provider, repo_state, page)
    for page in dirty:
        if page.diagram:
            provider.queue(MERMAID_BLOCK)


def assert_drained(provider: FakeStructuredProvider) -> None:
    assert provider._responses == [], (
        f"unconsumed structured responses: {len(provider._responses)}"
    )
    assert provider._tool_turns == [], (
        f"unconsumed tool turns: {len(provider._tool_turns)}"
    )


class StrictProvider:
    """Fails the test on ANY LLM traffic.

    Required for zero-call assertions: `FakeStructuredProvider`'s tool
    loop silently returns `budget_exhausted` on an empty queue, which a
    later quality gate converts into a degraded page instead of a test
    failure.
    """

    model = FAKE_MODEL

    async def complete_text(self, **kwargs: Any) -> str:
        raise AssertionError("StrictProvider: unexpected complete_text call")

    async def complete_json(self, **kwargs: Any) -> Any:
        raise AssertionError("StrictProvider: unexpected complete_json call")

    async def complete_with_tools(self, **kwargs: Any) -> Any:
        raise AssertionError("StrictProvider: unexpected complete_with_tools call")


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


def harness_config(**overrides: Any) -> WikiGenerationConfig:
    """Serial writes so queue order == plan order; small floor so 4-page
    plans pass validation."""
    defaults: dict[str, Any] = {"write_concurrency": 1, "page_count_min": 1}
    defaults.update(overrides)
    return WikiGenerationConfig(**defaults)


async def run_pipeline(
    session: AsyncSession,
    repo_state: ScriptedRepo,
    *,
    llm: Any,
    source_commit: str,
    config: WikiGenerationConfig | None = None,
    retriever: WikiRetrievalService | None = None,
) -> WikiGenerationResult:
    return await run_wiki_generation(
        session=session,
        repository_id=repo_state.id,
        source_commit=source_commit,
        sync_run_id=None,
        llm=llm,
        retriever=retriever or make_retriever(),
        config=config or harness_config(),
    )


async def run_full(
    session: AsyncSession,
    repo_state: ScriptedRepo,
    pages: list[ScriptedPage],
    *,
    source_commit: str,
    config: WikiGenerationConfig | None = None,
    retriever: WikiRetrievalService | None = None,
) -> WikiGenerationResult:
    provider = FakeStructuredProvider()
    queue_full_run(provider, repo_state, pages)
    result = await run_pipeline(
        session,
        repo_state,
        llm=provider,
        source_commit=source_commit,
        config=config,
        retriever=retriever,
    )
    assert result.errors == [], f"unexpected errors: {result.errors}"
    assert_drained(provider)
    return result


async def run_incremental(
    session: AsyncSession,
    repo_state: ScriptedRepo,
    pages: list[ScriptedPage],
    *,
    source_commit: str,
    expected_dirty: set[str],
    config: WikiGenerationConfig | None = None,
) -> WikiGenerationResult:
    """Run expecting incremental mode with exactly `expected_dirty` rewrites."""
    provider: Any
    if expected_dirty:
        provider = FakeStructuredProvider()
        queue_incremental_run(provider, repo_state, pages, dirty_slugs=expected_dirty)
    else:
        provider = StrictProvider()
    result = await run_pipeline(
        session,
        repo_state,
        llm=provider,
        source_commit=source_commit,
        config=config,
    )
    assert result.errors == [], f"unexpected errors: {result.errors}"
    assert result.mode == "incremental", f"expected incremental, got {result.mode}"
    assert set(result.dirty_reasons) == expected_dirty, (
        f"dirty set mismatch: {result.dirty_reasons} != {expected_dirty}"
    )
    if expected_dirty:
        assert_drained(provider)
    return result


# ---------------------------------------------------------------------------
# business_view
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


async def business_view(
    session: AsyncSession, repo_state: ScriptedRepo
) -> dict[str, dict[str, Any]]:
    """Reader-visible wiki state, normalised for cross-repo comparison.

    Excluded by design: row ids, sync_run_id, timestamps, source_hash,
    content_hash (the raw content embeds repo-local UUIDs), token/agent
    telemetry inside `quality` (only `quality_status` is compared).
    """
    repo = repo_state.repo
    base_path = f"/repos/{repo.host}/{repo.owner}/{repo.name}"

    node_label_by_id: dict[str, str] = {}
    rows = (
        (
            await session.execute(
                select(CodeNode).where(CodeNode.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    for node in rows:
        node_label_by_id[str(node.id)] = node.qualified_name
    chunk_label_by_id: dict[str, str] = {}
    chunk_rows = (
        await session.execute(
            select(RepoDocumentChunk, RepoDocument)
            .join(RepoDocument, RepoDocumentChunk.document_id == RepoDocument.id)
            .where(RepoDocument.repository_id == repo.id)
        )
    ).all()
    for chunk, doc in chunk_rows:
        chunk_label_by_id[str(chunk.id)] = f"{doc.file_path}#{chunk.chunk_index}"

    def _normalise_content(content: str) -> str:
        content = content.replace(base_path, "«repo»")
        return _UUID_RE.sub(
            lambda m: f"«{node_label_by_id.get(m.group(0), 'uuid')}»", content
        )

    docs = (
        (
            await session.execute(
                select(Document).where(
                    Document.repository_id == repo.id,
                    Document.doc_type == "wiki",
                )
            )
        )
        .scalars()
        .all()
    )
    view: dict[str, dict[str, Any]] = {}
    for row in docs:
        quality = row.quality if isinstance(row.quality, dict) else {}
        view[row.slug] = {
            "title": row.title,
            "parent_slug": row.parent_slug,
            "sort_order": row.sort_order,
            "content": _normalise_content(row.content),
            "citations": sorted(
                (
                    str(c.get("kind")),
                    str(c.get("label")),
                    str(c.get("file_path")),
                )
                for c in (row.citations or [])
            ),
            "source_nodes": sorted(
                node_label_by_id.get(str(nid), "missing")
                for nid in (row.source_node_ids or [])
            ),
            "source_chunks": sorted(
                chunk_label_by_id.get(str(cid), "missing")
                for cid in (row.source_repo_doc_chunk_ids or [])
            ),
            "quality_status": quality.get("quality_status"),
            "source_commit": row.source_commit,
        }
    return view


# ---------------------------------------------------------------------------
# Standard fixture: 4 pages over disjoint token namespaces
# ---------------------------------------------------------------------------

STANDARD_PAGES: list[ScriptedPage] = [
    ScriptedPage(
        slug="index",
        title="Index",
        purpose="Wiki entry navigation landing",
    ),
    ScriptedPage(
        slug="alpha",
        title="Alpha",
        purpose="Documents the alphaflow subsystem",
        cites=("pkg.alpha_main", "pkg._alpha_helper"),
    ),
    ScriptedPage(
        slug="beta",
        title="Beta",
        purpose="Documents the betaflow subsystem",
        cites=("pkg.beta_main",),
    ),
    ScriptedPage(
        slug="gamma",
        title="Gamma",
        purpose="Documents the gammaflow guidebook material",
        cites=("pkg.gamma_main",),
    ),
]


async def seed_standard(session: AsyncSession, repo_state: ScriptedRepo) -> None:
    """Three nodes + one doc, each owning a private token namespace so a
    page's retrieval bundle contains exactly its own evidence."""
    await repo_state.add_node(
        session,
        "pkg.alpha_main",
        content="def alpha_main():\n    return 'alphaflow v1'",
        summary="alpha summary v1",
    )
    # Private (underscore) leaf: not in the public-api manifest, so deleting
    # it later is an incremental change. Deleting an EXPORTED symbol moves
    # the manifest → structural hash → re-plan, by design.
    await repo_state.add_node(
        session,
        "pkg._alpha_helper",
        content="def _alpha_helper():\n    return 'alphaflow helper detail'",
        summary="alpha helper summary v1",
        file_path="src/alpha_main.py",
    )
    await repo_state.add_node(
        session,
        "pkg.beta_main",
        content="def beta_main():\n    return 'betaflow v1'",
        summary="beta summary v1",
    )
    await repo_state.add_node(
        session,
        "pkg.gamma_main",
        content="def gamma_main():\n    return 'gammaflow v1'",
        summary="gamma summary v1",
    )
    # No token shared with other pages' retrieval queries ("the",
    # "subsystem", "documents" would leak this chunk into every bundle and
    # make any doc edit drift all fingerprints).
    await repo_state.add_doc(
        session,
        "docs/guide.md",
        title="Guide",
        chunks=["gammaflow guidebook: gammaflow internals walkthrough."],
    )
