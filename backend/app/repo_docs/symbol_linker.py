from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode

_BACKTICK_SYMBOL_PATTERN = re.compile(r"`([A-Za-z_][A-Za-z0-9_\.]*)`")
_PLAIN_QUALIFIED_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b")
_FUNCTION_CALL_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\(\)")


@dataclass(slots=True, kw_only=True)
class LinkedMention:
    node_id: UUID
    name: str
    file_path: str


class RepoDocumentSymbolLinker:
    async def link_chunk_mentions(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        document_file_path: str,
        chunk_content: str,
    ) -> list[LinkedMention]:
        raw_names = _extract_raw_names(chunk_content)
        if not raw_names:
            return []

        exact_names = {raw_name.rsplit(".", 1)[-1].removesuffix("()") for raw_name in raw_names}
        qualified_names = {raw_name.removesuffix("()") for raw_name in raw_names if "." in raw_name}

        all_nodes = list(
            (
                await session.scalars(
                    select(CodeNode)
                    .where(
                        CodeNode.repository_id == repository_id,
                        or_(
                            CodeNode.name.in_(exact_names),
                            CodeNode.qualified_name.in_(qualified_names),
                        ),
                    )
                    .order_by(CodeNode.file_path.asc(), CodeNode.start_line.asc())
                )
            ).all()
        )
        candidates_by_name: dict[str, list[CodeNode]] = {}
        candidates_by_qualified_name: dict[str, CodeNode] = {}
        for node in all_nodes:
            candidates_by_name.setdefault(node.name, []).append(node)
            candidates_by_qualified_name.setdefault(node.qualified_name, node)

        mentions: list[LinkedMention] = []
        for raw_name in raw_names:
            exact_name = raw_name.rsplit(".", 1)[-1].removesuffix("()")
            candidates = list(candidates_by_name.get(exact_name, []))
            exact_qualified = candidates_by_qualified_name.get(raw_name.removesuffix("()"))
            if exact_qualified is not None and exact_qualified not in candidates:
                candidates.insert(0, exact_qualified)
            if not candidates:
                continue

            scored_candidates = [
                (
                    _candidate_score(
                        raw_name=raw_name,
                        candidate=candidate,
                        document_file_path=document_file_path,
                    ),
                    candidate,
                )
                for candidate in candidates
            ]
            best_score, resolved = max(
                scored_candidates,
                key=lambda item: (
                    item[0],
                    _common_path_prefix_length(document_file_path, item[1].file_path),
                    -item[1].start_line,
                ),
            )
            if best_score <= 0:
                continue
            mentions.append(
                LinkedMention(
                    node_id=resolved.id,
                    name=resolved.name,
                    file_path=resolved.file_path,
                )
            )
        return mentions


def _extract_raw_names(chunk_content: str) -> list[str]:
    raw_names: list[str] = []
    for match in _BACKTICK_SYMBOL_PATTERN.findall(chunk_content):
        if match not in raw_names:
            raw_names.append(match)
    for match in _PLAIN_QUALIFIED_PATTERN.findall(chunk_content):
        if match not in raw_names:
            raw_names.append(match)
    for match in _FUNCTION_CALL_PATTERN.findall(chunk_content):
        if match not in raw_names:
            raw_names.append(match)
    return raw_names


def _candidate_score(
    *,
    raw_name: str,
    candidate: CodeNode,
    document_file_path: str,
) -> int:
    clean_raw = raw_name.removesuffix("()")
    raw_parts = clean_raw.split(".")
    exact_name = raw_parts[-1]
    score = 0
    if candidate.qualified_name == clean_raw:
        score += 120
    elif candidate.qualified_name.endswith(clean_raw):
        score += 90
    if candidate.name == exact_name:
        score += 50
    if len(raw_parts) > 1:
        qualifier = ".".join(raw_parts[:-1])
        package_name = candidate.node_metadata.get("package_qualified_name")
        if package_name == qualifier:
            score += 40
        if qualifier in candidate.qualified_name:
            score += 30
        candidate_path_parts = tuple(part.lower() for part in PurePosixPath(candidate.file_path).parts)
        if raw_parts[-2].lower() in candidate_path_parts:
            score += 20
    document_parts = {
        part.lower()
        for part in PurePosixPath(document_file_path).parts
        if part.lower() not in {"docs", "doc"}
    }
    candidate_parts = {
        part.lower()
        for part in PurePosixPath(candidate.file_path).parts
    }
    if document_parts & candidate_parts:
        score += 15
    score += _common_path_prefix_length(document_file_path, candidate.file_path) * 5
    return score


def _common_path_prefix_length(left: str, right: str) -> int:
    left_parts = PurePosixPath(left).parent.parts
    right_parts = PurePosixPath(right).parent.parts
    length = 0
    for left_part, right_part in zip(left_parts, right_parts, strict=False):
        if left_part != right_part:
            break
        length += 1
    return length
