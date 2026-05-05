"""Tool handlers + Pydantic input schemas for the wiki writer agent.

Each tool is a thin async function `(context, **input) -> dict` that the
dispatcher (`agent_dispatcher.py`) wraps in an error envelope. The handlers
reuse existing services (`HybridRetriever`, `LexicalRetriever`,
`SymbolLookup`, `GraphTraversalService`) — no new retrieval code lives here.

Output is always a JSON-serialisable dict the LLM can read directly. Every
output field is intentionally short — the model paying per-token shouldn't
get a 5-KB blob when it asked for a one-line signature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.graph.traversal import GraphTraversalService, TraversalDirection
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup
from backend.app.wiki.concept_match import apply_domain_rerank
from backend.app.wiki.schemas import (
    BusinessContextConfidence,
    DomainConcept,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.app.llm.embedder import EmbedProvider
    from backend.app.wiki.checkout_fs import CheckoutFs

logger = logging.getLogger(__name__)


_SNIPPET_CHAR_CAP = 1_200


# ---------------------------------------------------------------------------
# Pydantic input schemas — these convert to JSON Schema for the LLM
# provider's `tools[*].input_schema` via `Model.model_json_schema()`.
# ---------------------------------------------------------------------------


class ReadNodeByQnInput(BaseModel):
    qualified_name: str = Field(
        ..., description="Fully qualified symbol name as it appears in the code graph."
    )


class FindByNameInput(BaseModel):
    name: str = Field(
        ...,
        description=(
            "Symbol name to fuzzy-match against the code graph. Uses lexical "
            "+ trigram match across qualified_name, name, and signature."
        ),
    )
    top_k: int = Field(
        default=10,
        description="How many ranked candidates to return (1–25).",
        ge=1,
        le=25,
    )


class ListChildrenInput(BaseModel):
    qualified_name: str = Field(
        ...,
        description=(
            "Qualified name of the parent symbol (struct/class/interface). "
            "Returns its direct child nodes (fields and methods)."
        ),
    )


class ListByFileInput(BaseModel):
    file_path: str = Field(
        ...,
        description="Repo-relative file path; returns every code node in that file.",
    )


class GetNeighborsInput(BaseModel):
    qualified_name: str = Field(..., description="Qualified name of the seed symbol.")
    depth: int = Field(default=1, description="Traversal depth (1–3).", ge=1, le=3)
    direction: str = Field(
        default="both",
        description="One of: `both`, `callers`, `callees`.",
    )


class SearchCodeInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Natural-language or symbol-flavoured query. Hybrid retrieval "
            "combines lexical, symbol, and embedding hits."
        ),
    )
    top_k: int = Field(
        default=8, description="How many code chunks to return (1–20).", ge=1, le=20
    )


class SearchDocsInput(BaseModel):
    query: str = Field(
        ...,
        description="Query against the in-repo documentation corpus (markdown chunks).",
    )
    top_k: int = Field(
        default=4, description="How many doc chunks to return (1–10).", ge=1, le=10
    )


class ReadFileInput(BaseModel):
    path: str = Field(..., description="Repo-relative file path under the checkout.")
    offset: int = Field(default=1, description="First line to read (1-indexed).", ge=1)
    limit: int = Field(
        default=200, description="Max number of lines to read.", ge=1, le=400
    )


class GrepInput(BaseModel):
    pattern: str = Field(
        ...,
        description="Regex pattern for ripgrep-compatible search across the checkout.",
    )
    glob: str | None = Field(
        default=None,
        description=(
            "Optional glob to scope the search (e.g. `**/*.go`, `pkg/auth/**`)."
        ),
    )


class ListFilesInput(BaseModel):
    glob: str = Field(
        default="**/*",
        description="Relative glob pattern; matches files under the checkout.",
    )


class WritePageInput(BaseModel):
    markdown: str = Field(
        ...,
        description=(
            "The completed wiki page as GitHub-flavored markdown. Calling "
            "this tool ENDS the agent loop and ships the markdown verbatim."
        ),
    )


# ---------------------------------------------------------------------------
# Tool context — the dispatcher hands each tool one of these.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgentToolContext:
    """All the runtime dependencies tool handlers can reach.

    `session_factory` builds a fresh `AsyncSession` per tool call (not per
    page) — the agent loop runs for minutes; sharing one session would
    pin a connection in the pool.

    `domain_concepts` and `business_confidence` are populated from the
    repo's BusinessContext so `search_code` can apply the same T6 rerank
    that `WikiRetrievalService.for_page` uses on the planned bundle.
    """

    session_factory: Callable[[], Awaitable[AsyncSession] | AsyncSession]
    repository_id: UUID
    checkout_fs: CheckoutFs | None
    hybrid: HybridRetriever
    lexical: LexicalRetriever
    symbol: SymbolLookup
    traversal: GraphTraversalService
    embedder: EmbedProvider | None
    domain_concepts: list[DomainConcept] = field(default_factory=list)
    business_confidence: BusinessContextConfidence | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, cap: int = _SNIPPET_CHAR_CAP) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + "\n... [truncated]"


def _node_to_dict(node: CodeNode, summary: str | None = None) -> dict[str, Any]:
    """Render a `CodeNode` as the LLM-facing dict.

    Snippet is capped at `_SNIPPET_CHAR_CAP` to stop one Foo.go file from
    blowing a turn's tokens. Empty fields are omitted so the agent doesn't
    waste cognition on `"docstring": null` placeholders.
    """
    out: dict[str, Any] = {
        "id": str(node.id),
        "qualified_name": node.qualified_name,
        "name": node.name,
        "node_type": node.node_type.value
        if hasattr(node.node_type, "value")
        else str(node.node_type),
        "file_path": node.file_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "language": node.language,
    }
    if node.signature:
        out["signature"] = node.signature
    if node.doc_comment:
        out["docstring"] = node.doc_comment
    if node.content:
        out["snippet"] = _truncate(node.content)
    if summary:
        out["summary"] = summary
    if node.parent_id:
        out["parent_id"] = str(node.parent_id)
    return out


async def _maybe_session(
    factory: Callable[[], Awaitable[AsyncSession] | AsyncSession],
) -> AsyncSession:
    """Resolve a session factory to a session.

    Supports both async and sync factories — production passes
    `session_manager.session()` (async context manager); tests typically
    pass a lambda returning an existing session. We don't manage the
    session lifecycle here; the dispatcher wraps the call so the session
    is closed correctly.
    """
    candidate = factory()
    if hasattr(candidate, "__await__"):
        return await candidate  # type: ignore[no-any-return]
    return candidate  # type: ignore[return-value]


async def _load_summary(session: AsyncSession, *, code_node_id: UUID) -> str | None:
    summary = await session.scalar(
        select(CodeNodeSummary.summary).where(
            CodeNodeSummary.code_node_id == code_node_id
        )
    )
    return summary  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def read_node_by_qn(
    ctx: AgentToolContext, session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = ReadNodeByQnInput.model_validate(payload)
    stmt = (
        select(CodeNode)
        .where(CodeNode.repository_id == ctx.repository_id)
        .where(CodeNode.qualified_name == args.qualified_name)
        .limit(1)
    )
    node = (await session.execute(stmt)).scalar_one_or_none()
    if node is None:
        # Fuzzy fallback so a slight QN mismatch ("pkg.foo" vs
        # "pkg.foo.Foo") doesn't waste a turn.
        hits = await ctx.symbol.search(
            session,
            query_text=args.qualified_name,
            repository_id=ctx.repository_id,
            top_k=3,
        )
        if not hits:
            return {
                "found": False,
                "qualified_name": args.qualified_name,
                "message": "no exact match; symbol search returned nothing",
            }
        return {
            "found": False,
            "qualified_name": args.qualified_name,
            "message": "no exact match; closest candidates listed",
            "candidates": [
                {
                    "qualified_name": str(hit.metadata.get("qualified_name", "")),
                    "file_path": str(hit.metadata.get("file_path", "")),
                    "start_line": int(hit.metadata.get("start_line") or 0),
                    "score": float(hit.score),
                }
                for hit in hits
            ],
        }

    summary = await _load_summary(session, code_node_id=node.id)
    body = _node_to_dict(node, summary=summary)
    body["found"] = True
    return body


async def find_by_name(
    ctx: AgentToolContext, session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = FindByNameInput.model_validate(payload)
    hits = await ctx.symbol.search(
        session,
        query_text=args.name,
        repository_id=ctx.repository_id,
        top_k=args.top_k,
    )
    return {
        "name": args.name,
        "candidates": [
            {
                "qualified_name": str(hit.metadata.get("qualified_name", "")),
                "file_path": str(hit.metadata.get("file_path", "")),
                "start_line": int(hit.metadata.get("start_line") or 0),
                "end_line": int(hit.metadata.get("end_line") or 0),
                "language": str(hit.metadata.get("language", "")),
                "score": float(hit.score),
            }
            for hit in hits
        ],
    }


async def list_children(
    ctx: AgentToolContext, session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = ListChildrenInput.model_validate(payload)
    parent_stmt = (
        select(CodeNode.id)
        .where(CodeNode.repository_id == ctx.repository_id)
        .where(CodeNode.qualified_name == args.qualified_name)
        .limit(1)
    )
    parent_id = await session.scalar(parent_stmt)
    if parent_id is None:
        return {
            "qualified_name": args.qualified_name,
            "found": False,
            "children": [],
            "message": "parent symbol not found",
        }

    children_stmt = (
        select(CodeNode)
        .where(CodeNode.repository_id == ctx.repository_id)
        .where(CodeNode.parent_id == parent_id)
        .order_by(CodeNode.start_line.asc(), CodeNode.qualified_name.asc())
    )
    children = (await session.execute(children_stmt)).scalars().all()
    return {
        "qualified_name": args.qualified_name,
        "found": True,
        "children": [_node_to_dict(child) for child in children],
    }


async def list_by_file(
    ctx: AgentToolContext, session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = ListByFileInput.model_validate(payload)
    stmt = (
        select(CodeNode)
        .where(CodeNode.repository_id == ctx.repository_id)
        .where(CodeNode.file_path == args.file_path)
        .order_by(CodeNode.start_line.asc())
    )
    nodes = (await session.execute(stmt)).scalars().all()
    return {
        "file_path": args.file_path,
        "nodes": [_node_to_dict(node) for node in nodes],
    }


async def get_neighbors(
    ctx: AgentToolContext, session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = GetNeighborsInput.model_validate(payload)
    seed_id = await session.scalar(
        select(CodeNode.id)
        .where(CodeNode.repository_id == ctx.repository_id)
        .where(CodeNode.qualified_name == args.qualified_name)
        .limit(1)
    )
    if seed_id is None:
        return {
            "qualified_name": args.qualified_name,
            "found": False,
            "callers": [],
            "callees": [],
            "message": "seed symbol not found",
        }
    direction_str = (args.direction or "both").lower()
    try:
        direction = TraversalDirection(direction_str)
    except ValueError:
        direction = TraversalDirection.BOTH

    response = await ctx.traversal.traverse(
        session=session,
        repository_id=ctx.repository_id,
        node_id=seed_id,
        depth=args.depth,
        direction=direction,
    )
    if response is None:
        return {
            "qualified_name": args.qualified_name,
            "found": True,
            "callers": [],
            "callees": [],
        }

    callers: list[dict[str, Any]] = []
    callees: list[dict[str, Any]] = []
    contains: list[dict[str, Any]] = []
    nodes_by_id = {str(n.id): n for n in response.nodes}
    for edge in response.edges:
        target_node = nodes_by_id.get(str(edge.target))
        source_node = nodes_by_id.get(str(edge.source))
        edge_type = edge.type.value if hasattr(edge.type, "value") else str(edge.type)
        if edge_type == "calls":
            if str(edge.source) == str(seed_id) and target_node is not None:
                callees.append(_traversal_node_dict(target_node))
            elif str(edge.target) == str(seed_id) and source_node is not None:
                callers.append(_traversal_node_dict(source_node))
        elif edge_type == "contains":
            other = target_node if str(edge.source) == str(seed_id) else source_node
            if other is not None:
                contains.append(_traversal_node_dict(other))

    return {
        "qualified_name": args.qualified_name,
        "found": True,
        "depth": args.depth,
        "direction": direction.value,
        "callers": callers,
        "callees": callees,
        "contains": contains,
    }


def _traversal_node_dict(node: Any) -> dict[str, Any]:
    return {
        "qualified_name": getattr(node, "name", ""),
        "node_type": getattr(node, "node_type", ""),
        "file_path": getattr(node, "file_path", ""),
        "start_line": int(getattr(node, "start_line", 0) or 0),
        "end_line": int(getattr(node, "end_line", 0) or 0),
        "signature": getattr(node, "signature", "") or "",
    }


async def search_code(
    ctx: AgentToolContext, session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = SearchCodeInput.model_validate(payload)
    embedding: list[float] | None = None
    if ctx.embedder is not None:
        try:
            embedding = (await ctx.embedder.embed([args.query]))[0]
        except Exception as exc:  # pragma: no cover — exercised in integration runs
            logger.warning(
                "search_code: embedding failed (%s); falling back to lexical-only",
                exc,
            )

    hits = await ctx.hybrid.retrieve(
        session,
        query_text=args.query,
        query_embedding=list(embedding or []),
        repository_id=ctx.repository_id,
        top_k=args.top_k,
        stores={"code"},
    )
    # T6: domain-concept-aware rerank (additive; no-op when concepts empty).
    hits = apply_domain_rerank(
        hits,
        concepts=ctx.domain_concepts,
        confidence=ctx.business_confidence,
    )
    return {
        "query": args.query,
        "results": [
            {
                "qualified_name": str(hit.metadata.get("qualified_name", "")),
                "file_path": str(hit.metadata.get("file_path", "")),
                "start_line": int(hit.metadata.get("start_line") or 0),
                "end_line": int(hit.metadata.get("end_line") or 0),
                "language": str(hit.metadata.get("language", "")),
                "score": float(hit.score),
                "snippet": _truncate(hit.content),
            }
            for hit in hits
        ],
    }


async def search_docs(
    ctx: AgentToolContext, session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = SearchDocsInput.model_validate(payload)
    hits = await ctx.lexical.search(
        session,
        store="repo_docs",
        query_text=args.query,
        repository_id=ctx.repository_id,
        top_k=args.top_k,
    )
    return {
        "query": args.query,
        "results": [
            {
                "file_path": str(hit.metadata.get("file_path", "")),
                "title": hit.metadata.get("title"),
                "heading_path": list(hit.metadata.get("heading_path") or []),
                "chunk_index": int(hit.metadata.get("chunk_index") or 0),
                "score": float(hit.score),
                "snippet": _truncate(hit.content),
            }
            for hit in hits
        ],
    }


async def read_file_tool(
    ctx: AgentToolContext, _session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = ReadFileInput.model_validate(payload)
    if ctx.checkout_fs is None:
        return {
            "error": "no checkout available — read_file is unavailable for this run"
        }
    return ctx.checkout_fs.read_file(args.path, offset=args.offset, limit=args.limit)


async def grep_tool(
    ctx: AgentToolContext, _session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = GrepInput.model_validate(payload)
    if ctx.checkout_fs is None:
        return {"error": "no checkout available — grep is unavailable for this run"}
    return await ctx.checkout_fs.grep(args.pattern, glob=args.glob)


async def list_files_tool(
    ctx: AgentToolContext, _session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    args = ListFilesInput.model_validate(payload)
    if ctx.checkout_fs is None:
        return {
            "error": "no checkout available — list_files is unavailable for this run"
        }
    return ctx.checkout_fs.list_files(args.glob)


# `write_page` is special — it captures the markdown on the dispatcher's
# state object instead of returning data the LLM will use further. The
# dispatcher (`agent_dispatcher.py`) wires it into its `captured_markdown`
# slot and returns a plain ack to the model.


__all__ = [
    "AgentToolContext",
    "FindByNameInput",
    "GetNeighborsInput",
    "GrepInput",
    "ListByFileInput",
    "ListChildrenInput",
    "ListFilesInput",
    "ReadFileInput",
    "ReadNodeByQnInput",
    "SearchCodeInput",
    "SearchDocsInput",
    "WritePageInput",
    "find_by_name",
    "get_neighbors",
    "grep_tool",
    "list_by_file",
    "list_children",
    "list_files_tool",
    "read_file_tool",
    "read_node_by_qn",
    "search_code",
    "search_docs",
]
