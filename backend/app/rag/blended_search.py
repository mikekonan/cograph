"""Blended repository search for API and MCP clients.

Single-variant: only `doc_type='wiki'` documents (LLM-driven pipeline). The
legacy preview/default `variant` column was dropped in migration 0028.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.document import Document
from backend.app.rag.fusion import rrf_merge_streams
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup


class BlendedSearchGroupKind(StrEnum):
    WIKI = "wiki"
    CODE = "code"


class BlendedSearchCitationKind(StrEnum):
    WIKI = "wiki"
    CODE = "code"
    REPO_DOC = "repo_doc"


class BlendedSearchCitation(BaseModel):
    kind: BlendedSearchCitationKind
    label: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    node_id: UUID | None = None
    document_id: UUID | None = None
    chunk_id: UUID | None = None
    wiki_slug: str | None = None
    heading_path: list[str] = Field(default_factory=list)


class BlendedSearchResult(BaseModel):
    id: str
    title: str
    snippet: str
    score: float
    citations: list[BlendedSearchCitation] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class BlendedSearchGroup(BaseModel):
    kind: BlendedSearchGroupKind
    title: str
    rank: int
    score: float
    results: list[BlendedSearchResult] = Field(default_factory=list)


class BlendedSearchResponse(BaseModel):
    query: str
    repository_id: UUID
    groups: list[BlendedSearchGroup] = Field(default_factory=list)


_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "does",
    "for",
    "how",
    "in",
    "is",
    "of",
    "or",
    "the",
    "to",
    "where",
}
_WIKI_CANDIDATE_LIMIT = 48
_WIKI_DOC_TYPE = "wiki"


class BlendedSearchService:
    def __init__(
        self,
        *,
        lexical: LexicalRetriever | None = None,
        symbol: SymbolLookup | None = None,
        rrf_k: int = 60,
        candidate_cap: int = 300,
    ) -> None:
        self._lexical = lexical or LexicalRetriever()
        self._symbol = symbol or SymbolLookup()
        self._rrf_k = int(rrf_k)
        self._candidate_cap = int(candidate_cap)

    async def search(
        self,
        session: AsyncSession,
        *,
        repository_id: UUID,
        repo_slug_path: str,
        query: str,
        top_k: int = 10,
    ) -> BlendedSearchResponse:
        limit = max(1, min(int(top_k), 50))
        terms = _query_terms(query)

        wiki_results = await self._search_wiki(
            session=session,
            repository_id=repository_id,
            repo_slug_path=repo_slug_path,
            query=query,
            terms=terms,
            top_k=limit,
        )
        code_results = await self._search_code(
            session=session,
            repository_id=repository_id,
            query=query,
            top_k=limit,
        )

        groups = [
            _group(
                kind=BlendedSearchGroupKind.WIKI,
                title="Wiki orientation",
                results=wiki_results,
            ),
            _group(
                kind=BlendedSearchGroupKind.CODE,
                title="Code symbols",
                results=code_results,
            ),
        ]
        ranked_groups = rank_blended_groups(
            [group for group in groups if group.results],
            query=query,
        )
        return BlendedSearchResponse(
            query=query,
            repository_id=repository_id,
            groups=ranked_groups,
        )

    async def _search_wiki(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        repo_slug_path: str,
        query: str,
        terms: list[str],
        top_k: int,
    ) -> list[BlendedSearchResult]:
        predicates = [
            Document.repository_id == repository_id,
            Document.doc_type == _WIKI_DOC_TYPE,
        ]

        term_predicates = []
        for term in terms:
            pattern = f"%{term}%"
            term_predicates.append(
                or_(
                    Document.slug.ilike(pattern),
                    Document.title.ilike(pattern),
                    Document.content.ilike(pattern),
                )
            )
        if term_predicates:
            predicates.append(or_(*term_predicates))

        rows = list(
            (
                await session.scalars(
                    select(Document)
                    .where(*predicates)
                    .order_by(Document.sort_order.asc(), Document.title.asc())
                    .limit(max(_WIKI_CANDIDATE_LIMIT, top_k * 4))
                )
            ).all()
        )

        results: list[BlendedSearchResult] = []
        for document in rows:
            score = _score_text(
                query=query,
                terms=terms,
                title=f"{document.title} {document.slug}",
                text=document.content,
            )
            if score <= 0:
                continue
            results.append(
                BlendedSearchResult(
                    id=f"wiki:{document.slug}",
                    title=document.title,
                    snippet=_best_snippet(document.content, terms=terms),
                    score=score,
                    citations=[
                        BlendedSearchCitation(
                            kind=BlendedSearchCitationKind.WIKI,
                            label=document.title,
                            document_id=document.id,
                            wiki_slug=document.slug,
                        ),
                        *_wiki_citations_from_document(
                            document=document,
                            display_slug=document.slug,
                        ),
                    ][:6],
                    metadata={
                        "slug": document.slug,
                        "resource_uri": (
                            f"cograph://repo/{repo_slug_path}/wiki/{document.slug}"
                        ),
                        "web_path": f"/repos/{repo_slug_path}/wiki/{document.slug}",
                        "model": document.model,
                        "source_commit": document.source_commit,
                    },
                )
            )
        return _sort_results(results)[:top_k]

    async def _search_code(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        query: str,
        top_k: int,
    ) -> list[BlendedSearchResult]:
        search_k = min(max(top_k * 2, top_k), self._candidate_cap)
        lexical_hits = await self._lexical.search(
            session,
            store="code",
            query_text=query,
            repository_id=repository_id,
            top_k=search_k,
        )
        symbol_hits = await self._symbol.search(
            session,
            query_text=query,
            repository_id=repository_id,
            top_k=search_k,
        )
        merged = rrf_merge_streams(
            [lexical_hits, symbol_hits],
            k=self._rrf_k,
            candidate_cap=self._candidate_cap,
            stream_names=["lexical", "symbol"],
        )[:top_k]
        if not merged:
            return []

        node_ids = [chunk.chunk_id for chunk in merged]
        nodes = {
            node.id: node
            for node in (
                await session.scalars(select(CodeNode).where(CodeNode.id.in_(node_ids)))
            ).all()
        }
        summaries = {
            row.code_node_id: row.summary
            for row in (
                await session.scalars(
                    select(CodeNodeSummary).where(
                        CodeNodeSummary.code_node_id.in_(node_ids)
                    )
                )
            ).all()
        }

        results: list[BlendedSearchResult] = []
        for chunk in merged:
            node = nodes.get(chunk.chunk_id)
            if node is None:
                continue
            exact_match = _is_exact_symbol_match(query=query, node=node)
            score = float(chunk.score) + (2.0 if exact_match else 0.0)
            summary = summaries.get(node.id)
            snippet = summary or node.signature or node.content
            results.append(
                BlendedSearchResult(
                    id=f"code:{node.id}",
                    title=node.qualified_name,
                    snippet=_best_snippet(snippet, terms=_query_terms(query)),
                    score=score,
                    citations=[
                        BlendedSearchCitation(
                            kind=BlendedSearchCitationKind.CODE,
                            label=node.qualified_name,
                            file_path=node.file_path,
                            start_line=node.start_line,
                            end_line=node.end_line,
                            node_id=node.id,
                        )
                    ],
                    metadata={
                        "node_id": str(node.id),
                        "qualified_name": node.qualified_name,
                        "node_type": node.node_type.value,
                        "language": node.language,
                        "candidate_from": _candidate_from(chunk.metadata),
                        "exact_symbol_match": exact_match,
                    },
                )
            )
        return _sort_results(results)[:top_k]

def rank_blended_groups(
    groups: list[BlendedSearchGroup],
    *,
    query: str,
) -> list[BlendedSearchGroup]:
    symbolish_query = _is_symbolish_query(query)

    def sort_key(group: BlendedSearchGroup) -> tuple[int, float, str]:
        exact_code = (
            symbolish_query
            and group.kind is BlendedSearchGroupKind.CODE
            and any(
                result.metadata.get("exact_symbol_match") is True
                for result in group.results
            )
        )
        if exact_code:
            return (0, -group.score, group.kind.value)
        return (_default_group_priority(group.kind), -group.score, group.kind.value)

    ranked = sorted(groups, key=sort_key)
    return [
        group.model_copy(update={"rank": rank})
        for rank, group in enumerate(ranked, start=1)
    ]


def _group(
    *,
    kind: BlendedSearchGroupKind,
    title: str,
    results: list[BlendedSearchResult],
) -> BlendedSearchGroup:
    return BlendedSearchGroup(
        kind=kind,
        title=title,
        rank=0,
        score=max((result.score for result in results), default=0.0),
        results=results,
    )


def _default_group_priority(kind: BlendedSearchGroupKind) -> int:
    if kind is BlendedSearchGroupKind.WIKI:
        return 1
    return 2


def _sort_results(results: list[BlendedSearchResult]) -> list[BlendedSearchResult]:
    return sorted(results, key=lambda item: (-item.score, item.title, item.id))


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_RE.findall(query):
        term = raw.lower().strip("._-:/")
        if len(term) < 2 or term in _STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= 8:
            break
    return terms


def _score_text(
    *,
    query: str,
    terms: list[str],
    title: str,
    text: str,
) -> float:
    title_lower = title.lower()
    haystack = f"{title}\n{text}".lower()
    query_lower = query.lower().strip()
    score = 0.0
    if query_lower and query_lower in haystack:
        score += 5.0
    for term in terms:
        if term in title_lower:
            score += 3.0
        if term in haystack:
            score += 1.0 + min(haystack.count(term), 3) * 0.25
    return score


def _best_snippet(text: str | None, *, terms: list[str], limit: int = 420) -> str:
    value = _collapse_whitespace(text or "")
    if len(value) <= limit:
        return value
    lower = value.lower()
    first_match = min(
        (idx for term in terms if (idx := lower.find(term)) >= 0),
        default=0,
    )
    start = max(0, first_match - 120)
    end = min(len(value), start + limit)
    snippet = value[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(value):
        snippet = snippet.rstrip() + "..."
    return snippet


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _wiki_citations_from_document(
    *,
    document: Document,
    display_slug: str,
) -> list[BlendedSearchCitation]:
    """Extract code/repo-doc citations from `document.citations` JSON."""
    citations: list[BlendedSearchCitation] = []
    seen: set[tuple[str, str]] = set()
    for ref in document.citations or []:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "")
        ref_id = str(ref.get("id") or "")
        label = ref.get("label")
        file_path = ref.get("file_path")
        start_line = _optional_int(ref.get("start_line"))
        end_line = _optional_int(ref.get("end_line"))
        heading_path = [
            str(item) for item in (ref.get("heading_path") or []) if item is not None
        ]
        if kind == "node":
            key = ("code", ref_id)
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                BlendedSearchCitation(
                    kind=BlendedSearchCitationKind.CODE,
                    label=str(label) if label else None,
                    file_path=str(file_path) if file_path else None,
                    start_line=start_line,
                    end_line=end_line,
                    node_id=_uuid_or_none(ref_id),
                    wiki_slug=display_slug,
                    heading_path=heading_path,
                )
            )
        elif kind == "repo_doc_chunk":
            key = ("repo_doc", ref_id)
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                BlendedSearchCitation(
                    kind=BlendedSearchCitationKind.REPO_DOC,
                    label=str(label) if label else None,
                    file_path=str(file_path) if file_path else None,
                    start_line=start_line,
                    end_line=end_line,
                    chunk_id=_uuid_or_none(ref_id),
                    wiki_slug=display_slug,
                    heading_path=heading_path,
                )
            )
    return citations


def _uuid_or_none(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _candidate_from(metadata: dict[str, object]) -> list[Literal["lexical", "symbol"]]:
    candidates: list[Literal["lexical", "symbol"]] = []
    if metadata.get("lexical_rank") is not None:
        candidates.append("lexical")
    if metadata.get("symbol_rank") is not None:
        candidates.append("symbol")
    return candidates


def _is_exact_symbol_match(*, query: str, node: CodeNode) -> bool:
    normalized_query = _normalize_symbol(query)
    if not normalized_query:
        return False
    return normalized_query in {
        _normalize_symbol(node.name),
        _normalize_symbol(node.qualified_name),
        _normalize_symbol(node.symbol_key or ""),
    }


def _is_symbolish_query(query: str) -> bool:
    terms = _query_terms(query)
    if len(terms) > 2:
        return False
    stripped = query.strip()
    return bool(stripped) and (
        "." in stripped or "::" in stripped or "_" in stripped or stripped[:1].isupper()
    )


def _normalize_symbol(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
