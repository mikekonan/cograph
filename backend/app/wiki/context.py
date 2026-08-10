"""Stage 1: build a typed `RepoContext` from indexed repo state.

This stage does NO LLM work and NO retrieval-time embedding calls. It pulls
from already-populated tables: `code_nodes` (with `code_node_summaries`),
`repo_documents`, `source_files`, `repositories`. The output is a stable,
hashable snapshot consumed by every later prompt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.document import Document
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.source_file import SourceFile
from backend.app.wiki.manifests import RepoManifests, build_repo_manifests
from backend.app.wiki.schemas import BusinessContext, MindMap, RepoSignals
from backend.app.wiki.steering import WikiSteering, load_wiki_steering


class FileTreeEntry(BaseModel):
    file_path: str
    language: str
    bytes: int
    importance: float = 0.0


class TopSummary(BaseModel):
    """One row from `code_node_summaries` joined with `code_nodes`."""

    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    language: str
    summary: str
    importance: float


class RepoDocIndexEntry(BaseModel):
    file_path: str
    title: str | None = None
    first_heading: str | None = None


class RepoContext(BaseModel):
    """Snapshot of repository state at a specific commit."""

    repository_id: UUID
    commit_sha: str
    readme_text: str | None = None
    file_tree: list[FileTreeEntry] = Field(default_factory=list)
    top_summaries: list[TopSummary] = Field(default_factory=list)
    repo_doc_index: list[RepoDocIndexEntry] = Field(default_factory=list)
    manifests: RepoManifests = Field(default_factory=RepoManifests)
    code_node_count: int = 0

    file_tree_hash: str
    docs_hash: str
    summaries_hash: str
    identity_hash: str

    previous_run_slugs: list[str] = Field(default_factory=list)
    mindmap: MindMap | None = None
    business_context: BusinessContext | None = None
    steering: WikiSteering | None = None
    repo_signals: RepoSignals | None = None


_README_CANDIDATES: tuple[str, ...] = (
    "README.md",
    "readme.md",
    "Readme.md",
    "README.MD",
    "README",
    "README.rst",
    "README.txt",
)


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _first_heading(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def compute_structural_hash(context: RepoContext) -> str:
    """Commit-free hash of the repo's *shape* — the wiki-plan reuse key.

    Deliberately excludes everything that churns on ordinary commits:
    `commit_sha`, summaries (regenerate whenever code changes),
    `FileTreeEntry.bytes` / `.importance` (per-file code-node counts), and
    all line numbers / snippets inside manifests. What's left changes only
    when the repo's structure or public surface changes — files appear,
    move, or disappear; docs are added; an exported type gains a field —
    which is exactly when the page *plan* deserves a re-think.
    """
    manifests = context.manifests
    manifests_projection = {
        "runtimes": sorted((r.name, r.version or "") for r in manifests.runtimes),
        "run_commands": sorted(
            (c.label, c.kind) for c in manifests.run_commands
        ),
        "config_keys": sorted((k.key, k.kind) for k in manifests.config_keys),
        "dependencies": sorted(
            (d.name, d.version or "", d.ecosystem) for d in manifests.dependencies
        ),
        "public_api": sorted(
            (e.qualified_name, e.kind, e.file_path) for e in manifests.public_api
        ),
        "exported_types": sorted(
            (
                t.qualified_name,
                t.kind,
                t.file_path,
                tuple(sorted((f.name, f.type_signature or "") for f in t.fields)),
                tuple(sorted(t.methods)),
            )
            for t in manifests.exported_types
        ),
        "error_types": sorted(
            (e.qualified_name, e.file_path, e.language)
            for e in manifests.error_types
        ),
        "use_cases": sorted(u.label for u in manifests.use_cases),
    }
    payload = {
        "files": sorted(
            (entry.file_path, entry.language) for entry in context.file_tree
        ),
        "docs": sorted(
            (d.file_path, d.title or "", d.first_heading or "")
            for d in context.repo_doc_index
        ),
        "manifests": manifests_projection,
        "readme_sha": hashlib.sha256(
            (context.readme_text or "").encode("utf-8")
        ).hexdigest(),
    }
    return _hash_payload(payload)


async def build_repo_context(
    *,
    session: AsyncSession,
    repository_id: UUID,
    commit_sha: str,
    checkout_path: Path | str | None = None,
    file_tree_cap: int = 300,
    top_summaries_cap: int = 30,
    repo_doc_cap: int = 30,
    readme_char_cap: int = 8_000,
) -> RepoContext:
    """Build a `RepoContext` from indexed Postgres state plus an on-disk
    checkout for filesystem-backed manifest extraction.

    SQL errors propagate (Stage 1 fatal — repo isn't ready for wiki gen).
    `checkout_path` is optional: when absent (e.g. dry-run CLI), only the
    DB-backed `public_api` manifest is populated.
    """
    readme_text = await _load_readme(
        session=session,
        repository_id=repository_id,
        char_cap=readme_char_cap,
    )

    code_node_counts = await _file_code_node_counts(
        session=session,
        repository_id=repository_id,
    )
    file_tree = await _build_file_tree(
        session=session,
        repository_id=repository_id,
        importance_by_path=code_node_counts,
        cap=file_tree_cap,
    )

    top_summaries = await _load_top_summaries(
        session=session,
        repository_id=repository_id,
        cap=top_summaries_cap,
    )

    repo_doc_index = await _load_repo_doc_index(
        session=session,
        repository_id=repository_id,
        cap=repo_doc_cap,
    )

    code_node_count_total = await session.scalar(
        select(func.count(CodeNode.id)).where(CodeNode.repository_id == repository_id)
    )

    previous_run_slugs = await _load_previous_run_slugs(
        session=session,
        repository_id=repository_id,
    )

    manifests = await build_repo_manifests(
        session=session,
        repository_id=repository_id,
        checkout_path=checkout_path,
    )

    steering = load_wiki_steering(checkout_path)

    file_tree_payload = [entry.model_dump() for entry in file_tree]
    docs_payload = [entry.model_dump() for entry in repo_doc_index]
    summaries_payload = [entry.model_dump() for entry in top_summaries]
    manifests_payload = manifests.model_dump()

    file_tree_hash = _hash_payload(file_tree_payload)
    docs_hash = _hash_payload(docs_payload)
    summaries_hash = _hash_payload(summaries_payload)
    identity_hash = _hash_payload(
        {
            "repository_id": str(repository_id),
            "commit_sha": commit_sha,
            "file_tree_hash": file_tree_hash,
            "docs_hash": docs_hash,
            "summaries_hash": summaries_hash,
            "manifests_hash": _hash_payload(manifests_payload),
            "readme_sha": hashlib.sha256(
                (readme_text or "").encode("utf-8")
            ).hexdigest(),
        }
    )

    return RepoContext(
        repository_id=repository_id,
        commit_sha=commit_sha,
        readme_text=readme_text,
        file_tree=file_tree,
        top_summaries=top_summaries,
        repo_doc_index=repo_doc_index,
        manifests=manifests,
        code_node_count=int(code_node_count_total or 0),
        file_tree_hash=file_tree_hash,
        docs_hash=docs_hash,
        summaries_hash=summaries_hash,
        identity_hash=identity_hash,
        previous_run_slugs=previous_run_slugs,
        steering=steering,
    )


async def _load_readme(
    *,
    session: AsyncSession,
    repository_id: UUID,
    char_cap: int,
) -> str | None:
    stmt = (
        select(SourceFile.file_path, SourceFile.raw_bytes)
        .where(SourceFile.repository_id == repository_id)
        .where(SourceFile.file_path.in_(_README_CANDIDATES))
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return None
    rows.sort(key=lambda row: _README_CANDIDATES.index(row.file_path))
    raw = rows[0].raw_bytes
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    if len(text) > char_cap:
        text = text[:char_cap]
    return text


async def _file_code_node_counts(
    *,
    session: AsyncSession,
    repository_id: UUID,
) -> dict[str, int]:
    stmt = (
        select(CodeNode.file_path, func.count(CodeNode.id))
        .where(CodeNode.repository_id == repository_id)
        .group_by(CodeNode.file_path)
    )
    rows = (await session.execute(stmt)).all()
    return {row[0]: int(row[1]) for row in rows}


async def _build_file_tree(
    *,
    session: AsyncSession,
    repository_id: UUID,
    importance_by_path: dict[str, int],
    cap: int,
) -> list[FileTreeEntry]:
    stmt = (
        select(SourceFile.file_path, SourceFile.language, SourceFile.bytes)
        .where(SourceFile.repository_id == repository_id)
        .where(SourceFile.kind == "code")
    )
    rows = (await session.execute(stmt)).all()
    entries = [
        FileTreeEntry(
            file_path=row.file_path,
            language=row.language,
            bytes=int(row.bytes),
            importance=float(importance_by_path.get(row.file_path, 0)),
        )
        for row in rows
    ]
    entries.sort(key=lambda e: (-e.importance, -e.bytes, e.file_path))
    return entries[:cap]


async def _load_top_summaries(
    *,
    session: AsyncSession,
    repository_id: UUID,
    cap: int,
) -> list[TopSummary]:
    stmt = (
        select(
            CodeNode.qualified_name,
            CodeNode.file_path,
            CodeNode.start_line,
            CodeNode.end_line,
            CodeNode.language,
            CodeNodeSummary.summary,
            CodeNodeSummary.importance,
        )
        .join(CodeNodeSummary, CodeNodeSummary.code_node_id == CodeNode.id)
        .where(CodeNode.repository_id == repository_id)
        .order_by(CodeNodeSummary.importance.desc(), CodeNode.qualified_name.asc())
        .limit(cap)
    )
    rows = (await session.execute(stmt)).all()
    return [
        TopSummary(
            qualified_name=row.qualified_name,
            file_path=row.file_path,
            start_line=int(row.start_line),
            end_line=int(row.end_line),
            language=row.language,
            summary=row.summary,
            importance=float(row.importance),
        )
        for row in rows
    ]


async def _load_repo_doc_index(
    *,
    session: AsyncSession,
    repository_id: UUID,
    cap: int,
) -> list[RepoDocIndexEntry]:
    stmt = (
        select(
            RepoDocument.id,
            RepoDocument.file_path,
            RepoDocument.title,
            RepoDocument.content,
        )
        .where(RepoDocument.repository_id == repository_id)
        .order_by(RepoDocument.bytes.desc(), RepoDocument.file_path.asc())
        .limit(cap)
    )
    docs = (await session.execute(stmt)).all()
    if not docs:
        return []

    doc_ids = [row.id for row in docs]
    head_stmt = (
        select(RepoDocumentChunk.document_id, RepoDocumentChunk.heading_path)
        .where(RepoDocumentChunk.document_id.in_(doc_ids))
        .where(RepoDocumentChunk.chunk_index == 0)
    )
    heading_rows = (await session.execute(head_stmt)).all()
    headings_by_doc: dict[UUID, list[str]] = {
        row.document_id: row.heading_path for row in heading_rows
    }

    entries: list[RepoDocIndexEntry] = []
    for row in docs:
        heading_list = headings_by_doc.get(row.id, [])
        first_heading = (
            heading_list[0] if heading_list else _first_heading(row.content or "")
        )
        entries.append(
            RepoDocIndexEntry(
                file_path=row.file_path,
                title=row.title,
                first_heading=first_heading,
            )
        )
    return entries


async def _load_previous_run_slugs(
    *,
    session: AsyncSession,
    repository_id: UUID,
) -> list[str]:
    stmt = (
        select(Document.slug)
        .where(Document.repository_id == repository_id)
        .where(Document.doc_type == "wiki")
        .order_by(Document.sort_order.asc(), Document.slug.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [row.slug for row in rows]
