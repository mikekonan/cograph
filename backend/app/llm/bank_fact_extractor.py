"""Upload-time derived bank layer extraction for phase 8b."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.completion import CompletionProvider
from backend.app.llm.embedder import EmbedProvider
from backend.app.models.bank import (
    BankDocument,
    BankDocumentChunk,
    BankEntity,
    BankFact,
    BankObservation,
)

_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_MAX_FACTS_PER_CHUNK = 6


@dataclass(slots=True, kw_only=True)
class BankFactExtractionResult:
    extracted_facts: int
    extracted_entities: int
    extracted_observations: int
    completion_model: str
    embedding_model: str | None


@dataclass(slots=True, kw_only=True)
class _ChunkRecord:
    bank_id: UUID
    document_id: UUID
    chunk_id: UUID
    title: str
    heading_path: list[str]
    content: str


@dataclass(slots=True, kw_only=True)
class _EntityDraft:
    name: str
    canonical_name: str
    entity_type: str
    role: str | None


@dataclass(slots=True, kw_only=True)
class _FactDraft:
    bank_id: UUID
    document_id: UUID
    chunk_id: UUID
    statement: str
    source_excerpt: str | None
    heading_path: list[str]
    entities: list[_EntityDraft]
    embedding: list[float] | None = None


class BankFactExtractorService:
    def __init__(
        self,
        *,
        llm: CompletionProvider,
        embed_provider: EmbedProvider | None = None,
        concurrency: int = 4,
    ) -> None:
        self._llm = llm
        self._embed_provider = embed_provider
        self._concurrency = concurrency

    async def extract_documents(
        self,
        *,
        session: AsyncSession,
        document_ids: list[UUID],
    ) -> BankFactExtractionResult:
        if not document_ids:
            return self._empty_result()

        rows = (
            await session.execute(
                select(
                    BankDocument.bank_id,
                    BankDocument.id,
                    BankDocumentChunk.id,
                    BankDocument.title,
                    BankDocumentChunk.heading_path,
                    BankDocumentChunk.content,
                )
                .join(BankDocumentChunk, BankDocumentChunk.document_id == BankDocument.id)
                .where(BankDocument.id.in_(document_ids))
                .order_by(BankDocument.id, BankDocumentChunk.chunk_index.asc())
            )
        ).all()
        if not rows:
            return self._empty_result()

        chunks = [
            _ChunkRecord(
                bank_id=bank_id,
                document_id=document_id,
                chunk_id=chunk_id,
                title=title,
                heading_path=list(heading_path or []),
                content=content,
            )
            for bank_id, document_id, chunk_id, title, heading_path, content in rows
        ]

        await session.commit()

        fact_drafts = await self._extract_chunk_facts(chunks)
        if self._embed_provider is not None and fact_drafts:
            vectors = await self._embed_provider.embed([draft.statement for draft in fact_drafts])
            for draft, vector in zip(fact_drafts, vectors, strict=True):
                draft.embedding = vector

        await session.execute(delete(BankFact).where(BankFact.document_id.in_(document_ids)))
        await session.execute(delete(BankEntity).where(BankEntity.document_id.in_(document_ids)))

        entity_count = 0
        observation_count = 0
        for document_id in list(dict.fromkeys(document_ids)):
            document_facts = [draft for draft in fact_drafts if draft.document_id == document_id]
            entity_by_key: dict[tuple[str, str], BankEntity] = {}
            for draft in document_facts:
                fact = BankFact(
                    bank_id=draft.bank_id,
                    document_id=draft.document_id,
                    chunk_id=draft.chunk_id,
                    statement=draft.statement,
                    source_excerpt=draft.source_excerpt,
                    heading_path=list(draft.heading_path),
                    content_hash=_content_hash(draft.statement),
                    extraction_model=self._llm.model,
                    embedding=draft.embedding,
                    model=self._embed_provider.model if self._embed_provider else "",
                )
                session.add(fact)
                await session.flush()

                for entity_draft in draft.entities:
                    entity_key = (entity_draft.canonical_name, entity_draft.entity_type)
                    entity = entity_by_key.get(entity_key)
                    if entity is None:
                        entity = BankEntity(
                            bank_id=draft.bank_id,
                            document_id=draft.document_id,
                            name=entity_draft.name,
                            canonical_name=entity_draft.canonical_name,
                            entity_type=entity_draft.entity_type,
                        )
                        session.add(entity)
                        await session.flush()
                        entity_by_key[entity_key] = entity
                        entity_count += 1

                    session.add(
                        BankObservation(
                            fact_id=fact.id,
                            entity_id=entity.id,
                            role=entity_draft.role,
                            content=draft.source_excerpt or draft.statement,
                        )
                    )
                    observation_count += 1

        await session.commit()
        return BankFactExtractionResult(
            extracted_facts=len(fact_drafts),
            extracted_entities=entity_count,
            extracted_observations=observation_count,
            completion_model=self._llm.model,
            embedding_model=self._embed_provider.model if self._embed_provider else None,
        )

    async def _extract_chunk_facts(self, chunks: list[_ChunkRecord]) -> list[_FactDraft]:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def _extract(chunk: _ChunkRecord) -> list[_FactDraft]:
            async with semaphore:
                response = await self._llm.complete(_chunk_prompt(chunk))
            payload = _parse_chunk_payload(response)
            drafts: list[_FactDraft] = []
            for fact in payload:
                drafts.append(
                    _FactDraft(
                        bank_id=chunk.bank_id,
                        document_id=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                        statement=fact["statement"],
                        source_excerpt=fact.get("observation"),
                        heading_path=list(chunk.heading_path),
                        entities=[
                            _EntityDraft(
                                name=entity["name"],
                                canonical_name=_canonicalize(entity["name"]),
                                entity_type=_normalize_label(entity.get("type"), default="other"),
                                role=_normalize_role(entity.get("role")),
                            )
                            for entity in fact.get("entities", [])
                            if _canonicalize(entity.get("name", ""))
                        ],
                    )
                )
            return drafts

        results = await asyncio.gather(*[_extract(chunk) for chunk in chunks])
        return [draft for batch in results for draft in batch]

    def _empty_result(self) -> BankFactExtractionResult:
        return BankFactExtractionResult(
            extracted_facts=0,
            extracted_entities=0,
            extracted_observations=0,
            completion_model=self._llm.model,
            embedding_model=self._embed_provider.model if self._embed_provider else None,
        )


def _chunk_prompt(chunk: _ChunkRecord) -> str:
    heading = " > ".join(chunk.heading_path) if chunk.heading_path else "(root)"
    return (
        "Extract durable, retrieval-friendly knowledge from this markdown chunk.\n"
        "Return strict JSON only with the shape:\n"
        "{\n"
        '  "facts": [\n'
        "    {\n"
        '      "statement": "single standalone fact",\n'
        '      "observation": "short evidence snippet",\n'
        '      "entities": [\n'
        '        {"name": "entity name", "type": "team|service|system|concept|person|other", "role": "owner|subject|object|related"}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        f"Return at most {_MAX_FACTS_PER_CHUNK} facts.\n"
        "Do not include markdown, commentary, or trailing prose.\n"
        f"Document title: {chunk.title}\n"
        f"Heading path: {heading}\n"
        "Chunk:\n"
        f"{chunk.content}"
    )


def _parse_chunk_payload(response_text: str) -> list[dict[str, object]]:
    candidate = response_text.strip()
    block_match = _JSON_BLOCK_PATTERN.search(candidate)
    if block_match is not None:
        candidate = block_match.group(1)
    elif "{" in candidate and "}" in candidate:
        candidate = candidate[candidate.find("{") : candidate.rfind("}") + 1]

    payload = json.loads(candidate)
    facts = payload.get("facts", [])
    if not isinstance(facts, list):
        return []

    cleaned: list[dict[str, object]] = []
    for raw_fact in facts[:_MAX_FACTS_PER_CHUNK]:
        if not isinstance(raw_fact, dict):
            continue
        statement = _clean_text(raw_fact.get("statement"))
        if not statement:
            continue
        entities = raw_fact.get("entities", [])
        cleaned.append(
            {
                "statement": statement,
                "observation": _clean_text(raw_fact.get("observation")) or None,
                "entities": list(entities) if isinstance(entities, list) else [],
            }
        )
    return cleaned


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("`", "").split()).strip()


def _canonicalize(value: str) -> str:
    return " ".join(value.lower().split())


def _normalize_label(value: object, *, default: str) -> str:
    cleaned = _clean_text(value)
    return cleaned.lower() if cleaned else default


def _normalize_role(value: object) -> str | None:
    cleaned = _clean_text(value)
    return cleaned.lower() if cleaned else None


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
