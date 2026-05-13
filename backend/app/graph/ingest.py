from __future__ import annotations

import asyncio
from collections import defaultdict
import hashlib
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.graph._chunking import chunked
from backend.app.graph.builder import GraphBuilder
from backend.app.graph.extractor import ExtractedGraph, GraphExtractor, GraphNodeType
from backend.app.graph.ingest_cache import GraphIngestCache
from backend.app.graph.go_variants import (
    GoBuildVariantConflictError,
    GoIndexProfile,
    GoPackageSelection,
    resolve_go_index_profile,
    select_go_package_files,
)
from backend.app.graph.languages import GraphLanguage, detect_graph_language
from backend.app.graph.parser import GraphParser
from backend.app.graph.temporal import collect_node_temporal_metadata
from backend.app.models.code_edge import CodeEdge
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType
from backend.app.models.source_file import SourceFile

logger = logging.getLogger(__name__)

_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")

# Periodic progress every N files. 50 keeps log volume sane on
# 10k-file monorepos (≈200 lines per parse) while still giving
# users a heartbeat every few seconds in practice.
_PROGRESS_LOG_EVERY = 50


def _log_ingest_progress(
    *,
    repository_id: UUID,
    mode: str,
    processed: int,
    total: int,
    current_file: str,
) -> None:
    logger.info(
        "ingest_progress repo=%s mode=%s files_done=%d/%d current=%s",
        repository_id,
        mode,
        processed,
        total,
        current_file,
        extra={
            "event": "ingest_progress",
            "repository_id": str(repository_id),
            "mode": mode,
            "files_done": processed,
            "files_total": total,
            "current_file": current_file,
        },
    )


@dataclass(slots=True, kw_only=True)
class GraphIngestResult:
    processed_files: int
    inserted_nodes: int
    replaced_files: tuple[str, ...]
    resolved_calls: int
    unresolved_calls: int


@dataclass(slots=True, kw_only=True)
class GitFileChange:
    kind: str  # 'A' | 'M' | 'D' | 'R'
    file_path: str
    old_file_path: str | None = None


class GraphIngestService:
    def __init__(
        self,
        *,
        parser: GraphParser | None = None,
        extractor: GraphExtractor | None = None,
        builder: GraphBuilder | None = None,
    ) -> None:
        self._parser = parser or GraphParser()
        self._extractor = extractor or GraphExtractor()
        self._builder = builder or GraphBuilder()

    async def ingest_checkout(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        checkout_path: str | Path,
        last_commit: str | None = None,
        commit_sha: str | None = None,
        force_full: bool = False,
    ) -> GraphIngestResult:
        root_path = Path(checkout_path).resolve()
        go_module_path = await asyncio.to_thread(_detect_go_module_path, root_path)
        go_profile = await asyncio.to_thread(resolve_go_index_profile, root_path)

        git_changes: list[GitFileChange] | None = None
        if not force_full and last_commit and (root_path / ".git").exists():
            git_changes = await asyncio.to_thread(
                _detect_git_changes_safely, root_path, last_commit
            )

        mode = "incremental" if git_changes is not None else "full"
        start = time.monotonic()
        logger.info(
            "ingest_start repo=%s mode=%s commit=%s",
            repository_id,
            mode,
            commit_sha or "n/a",
            extra={
                "event": "ingest_start",
                "repository_id": str(repository_id),
                "mode": mode,
                "commit_sha": commit_sha,
                "git_changes": len(git_changes) if git_changes is not None else None,
            },
        )

        if git_changes is not None:
            result = await self._ingest_incremental_from_git(
                session=session,
                repository_id=repository_id,
                root_path=root_path,
                git_changes=git_changes,
                commit_sha=commit_sha,
                go_module_path=go_module_path,
                go_profile=go_profile,
            )
        else:
            result = await self._ingest_full_walk(
                session=session,
                repository_id=repository_id,
                root_path=root_path,
                commit_sha=commit_sha,
                go_module_path=go_module_path,
                go_profile=go_profile,
            )

        duration_s = time.monotonic() - start
        logger.info(
            "ingest_done repo=%s mode=%s files=%d inserted=%d replaced=%d "
            "resolved=%d unresolved=%d duration_s=%.1f",
            repository_id,
            mode,
            result.processed_files,
            result.inserted_nodes,
            len(result.replaced_files),
            result.resolved_calls,
            result.unresolved_calls,
            duration_s,
            extra={
                "event": "ingest_done",
                "repository_id": str(repository_id),
                "mode": mode,
                "files_processed": result.processed_files,
                "inserted_nodes": result.inserted_nodes,
                "replaced_files": len(result.replaced_files),
                "resolved_calls": result.resolved_calls,
                "unresolved_calls": result.unresolved_calls,
                "duration_s": round(duration_s, 1),
            },
        )
        return result

    async def _ingest_full_walk(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        root_path: Path,
        commit_sha: str | None,
        go_module_path: str | None,
        go_profile: GoIndexProfile,
    ) -> GraphIngestResult:
        source_files = await asyncio.to_thread(self._discover_source_files, root_path)
        non_go_files = tuple(
            source_file
            for source_file in source_files
            if detect_graph_language(source_file.relative_to(root_path)) is not GraphLanguage.GO
        )
        go_package_selections = await asyncio.to_thread(
            _select_go_packages_from_files,
            root_path,
            tuple(
                source_file
                for source_file in source_files
                if detect_graph_language(source_file.relative_to(root_path)) is GraphLanguage.GO
            ),
            go_profile,
        )
        relative_paths = tuple(
            sorted(
                [
                    *(source_file.relative_to(root_path).as_posix() for source_file in non_go_files),
                    *(
                        selected_file.relative_path
                        for package in go_package_selections
                        for selected_file in package.selected_files
                    ),
                ]
            )
        )

        existing_module_hashes = {
            file_path: content_hash
            for file_path, content_hash in (
                await session.execute(
                    select(CodeNode.file_path, CodeNode.content_hash).where(
                        CodeNode.repository_id == repository_id,
                        CodeNode.node_type == CodeNodeType.MODULE,
                    )
                )
            ).all()
        }

        await self._prune_missing_files(
            session=session,
            repository_id=repository_id,
            present_paths=relative_paths,
        )
        pruned_files = set(relative_paths) != set(existing_module_hashes)

        # Build a per-ingest cache from a single repo-wide SELECT. Each
        # `persist_graph` call mutates the cache in-place so subsequent
        # files see the freshest state without re-hitting the database.
        # Before this we did the same full SELECT inside `persist_graph`
        # itself, which scaled O(F × N) on monorepos and was the root
        # cause of the parse-step hang on bookkeeping.
        cache = GraphIngestCache.from_nodes(
            list(
                (
                    await session.scalars(
                        select(CodeNode).where(CodeNode.repository_id == repository_id)
                    )
                ).all()
            )
        )

        inserted_nodes = 0
        replaced_files: list[str] = []
        processed_files = 0
        resolved_calls = 0
        unresolved_calls = 0
        total_files = len(relative_paths)
        logger.info(
            "ingest_full_walk_plan repo=%s files=%d packages=%d existing_modules=%d",
            repository_id,
            total_files,
            len(go_package_selections),
            len(existing_module_hashes),
            extra={
                "event": "ingest_full_walk_plan",
                "repository_id": str(repository_id),
                "files_total": total_files,
                "packages": len(go_package_selections),
                "existing_modules": len(existing_module_hashes),
            },
        )

        for source_file in non_go_files:
            relative_path = source_file.relative_to(root_path)
            source_text = await asyncio.to_thread(source_file.read_text, encoding="utf-8")
            content_hash = _content_hash(source_text)
            if existing_module_hashes.get(relative_path.as_posix()) == content_hash:
                continue
            build_result = await self._parse_and_persist(
                session=session,
                repository_id=repository_id,
                root_path=root_path,
                relative_path=relative_path,
                source_text=source_text,
                commit_sha=commit_sha,
                go_module_path=go_module_path,
                go_profile=None,
                cache=cache,
            )
            inserted_nodes += build_result.inserted_nodes
            replaced_files.extend(build_result.replaced_files)
            resolved_calls += build_result.resolved_calls
            unresolved_calls += build_result.unresolved_calls
            processed_files += 1
            if processed_files % _PROGRESS_LOG_EVERY == 0:
                _log_ingest_progress(
                    repository_id=repository_id,
                    mode="full",
                    processed=processed_files,
                    total=total_files,
                    current_file=relative_path.as_posix(),
                )

        for package in go_package_selections:
            changed_selected_files = [
                selected_file
                for selected_file in package.selected_files
                if existing_module_hashes.get(selected_file.relative_path)
                != selected_file.content_hash
            ]
            if not changed_selected_files:
                continue

            parsed_graphs = await self._parse_go_package_graphs(
                package=package,
                go_module_path=go_module_path,
                go_profile=go_profile,
            )
            for selected_file in changed_selected_files:
                build_result = await self._persist_preparsed_graph(
                    session=session,
                    repository_id=repository_id,
                    root_path=root_path,
                    relative_path=Path(selected_file.relative_path),
                    extracted_graph=parsed_graphs[selected_file.relative_path],
                    commit_sha=commit_sha,
                    cache=cache,
                )
                inserted_nodes += build_result.inserted_nodes
                replaced_files.extend(build_result.replaced_files)
                resolved_calls += build_result.resolved_calls
                unresolved_calls += build_result.unresolved_calls
                processed_files += 1
                if processed_files % _PROGRESS_LOG_EVERY == 0:
                    _log_ingest_progress(
                        repository_id=repository_id,
                        mode="full",
                        processed=processed_files,
                        total=total_files,
                        current_file=selected_file.relative_path,
                    )

        if pruned_files:
            await self._builder.rebuild_relationships(
                session=session,
                repository_id=repository_id,
                cache=cache,
            )

        return GraphIngestResult(
            processed_files=processed_files,
            inserted_nodes=inserted_nodes,
            replaced_files=tuple(replaced_files),
            resolved_calls=resolved_calls,
            unresolved_calls=unresolved_calls,
        )

    async def _ingest_incremental_from_git(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        root_path: Path,
        git_changes: list[GitFileChange],
        commit_sha: str | None,
        go_module_path: str | None,
        go_profile: GoIndexProfile,
    ) -> GraphIngestResult:
        if any(_touches_root_go_mod(change) for change in git_changes):
            return await self._ingest_full_walk(
                session=session,
                repository_id=repository_id,
                root_path=root_path,
                commit_sha=commit_sha,
                go_module_path=go_module_path,
                go_profile=go_profile,
            )

        existing_module_hashes = {
            file_path: content_hash
            for file_path, content_hash in (
                await session.execute(
                    select(CodeNode.file_path, CodeNode.content_hash).where(
                        CodeNode.repository_id == repository_id,
                        CodeNode.node_type == CodeNodeType.MODULE,
                    )
                )
            ).all()
        }
        existing_go_paths_by_package: dict[str, set[str]] = defaultdict(set)
        for file_path in existing_module_hashes:
            if Path(file_path).suffix != ".go":
                continue
            existing_go_paths_by_package[_package_key(Path(file_path))].add(file_path)

        inserted_nodes = 0
        replaced_files: list[str] = []
        processed_files = 0
        resolved_delta = 0
        unresolved_delta = 0
        delete_peer_ids: set[UUID] = set()
        go_package_keys: set[str] = set()
        non_go_changes: list[GitFileChange] = []
        total_changes = len(git_changes)
        logger.info(
            "ingest_incremental_plan repo=%s git_changes=%d existing_modules=%d",
            repository_id,
            total_changes,
            len(existing_module_hashes),
            extra={
                "event": "ingest_incremental_plan",
                "repository_id": str(repository_id),
                "git_changes": total_changes,
                "existing_modules": len(existing_module_hashes),
            },
        )

        for change in git_changes:
            if _change_touches_go_package(change):
                for package_key in _change_go_package_keys(change):
                    go_package_keys.add(package_key)
                continue

            if detect_graph_language(Path(change.file_path)) is None:
                continue
            non_go_changes.append(change)

        for change in non_go_changes:
            if change.kind == "D":
                peers = await self._collect_delete_peer_ids(
                    session=session,
                    repository_id=repository_id,
                    file_path=change.file_path,
                )
                delete_peer_ids.update(peers)
                await session.execute(
                    delete(SourceFile).where(
                        SourceFile.repository_id == repository_id,
                        SourceFile.file_path == change.file_path,
                    )
                )
                continue

            if change.kind == "R" and change.old_file_path:
                peers = await self._collect_delete_peer_ids(
                    session=session,
                    repository_id=repository_id,
                    file_path=change.old_file_path,
                )
                delete_peer_ids.update(peers)
                await session.execute(
                    delete(SourceFile).where(
                        SourceFile.repository_id == repository_id,
                        SourceFile.file_path == change.old_file_path,
                    )
                )

            absolute = root_path / change.file_path
            if not absolute.exists():
                continue

            source_text = await asyncio.to_thread(absolute.read_text, encoding="utf-8")
            build_result = await self._parse_and_persist(
                session=session,
                repository_id=repository_id,
                root_path=root_path,
                relative_path=Path(change.file_path),
                source_text=source_text,
                commit_sha=commit_sha,
                go_module_path=go_module_path,
                go_profile=None,
            )
            inserted_nodes += build_result.inserted_nodes
            replaced_files.extend(build_result.replaced_files)
            resolved_delta += build_result.resolved_calls
            unresolved_delta += build_result.unresolved_calls
            processed_files += 1
            if processed_files % _PROGRESS_LOG_EVERY == 0:
                _log_ingest_progress(
                    repository_id=repository_id,
                    mode="incremental",
                    processed=processed_files,
                    total=total_changes,
                    current_file=change.file_path,
                )

        go_package_selections = await asyncio.to_thread(
            _select_go_packages_from_keys,
            root_path,
            tuple(sorted(go_package_keys)),
            go_profile,
        )
        selections_by_key = {
            package.package_key: package for package in go_package_selections
        }

        for package_key in sorted(go_package_keys):
            package = selections_by_key.get(
                package_key,
                GoPackageSelection(package_key=package_key, selected_files=()),
            )
            selected_paths = {selected_file.relative_path for selected_file in package.selected_files}
            stale_paths = sorted(existing_go_paths_by_package.get(package_key, set()) - selected_paths)
            for stale_path in stale_paths:
                peers = await self._collect_delete_peer_ids(
                    session=session,
                    repository_id=repository_id,
                    file_path=stale_path,
                )
                delete_peer_ids.update(peers)
                await session.execute(
                    delete(SourceFile).where(
                        SourceFile.repository_id == repository_id,
                        SourceFile.file_path == stale_path,
                    )
                )

            changed_selected_files = [
                selected_file
                for selected_file in package.selected_files
                if existing_module_hashes.get(selected_file.relative_path)
                != selected_file.content_hash
            ]
            if not changed_selected_files:
                continue

            parsed_graphs = await self._parse_go_package_graphs(
                package=package,
                go_module_path=go_module_path,
                go_profile=go_profile,
            )
            for selected_file in changed_selected_files:
                build_result = await self._persist_preparsed_graph(
                    session=session,
                    repository_id=repository_id,
                    root_path=root_path,
                    relative_path=Path(selected_file.relative_path),
                    extracted_graph=parsed_graphs[selected_file.relative_path],
                    commit_sha=commit_sha,
                )
                inserted_nodes += build_result.inserted_nodes
                replaced_files.extend(build_result.replaced_files)
                resolved_delta += build_result.resolved_calls
                unresolved_delta += build_result.unresolved_calls
                processed_files += 1
                if processed_files % _PROGRESS_LOG_EVERY == 0:
                    _log_ingest_progress(
                        repository_id=repository_id,
                        mode="incremental",
                        processed=processed_files,
                        total=total_changes,
                        current_file=selected_file.relative_path,
                    )

        if delete_peer_ids:
            await self._builder.rebuild_relationships_scoped(
                session=session,
                repository_id=repository_id,
                touched_ids=delete_peer_ids,
            )

        await session.flush()

        return GraphIngestResult(
            processed_files=processed_files,
            inserted_nodes=inserted_nodes,
            replaced_files=tuple(replaced_files),
            resolved_calls=resolved_delta,
            unresolved_calls=unresolved_delta,
        )

    async def _collect_delete_peer_ids(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        file_path: str,
    ) -> set[UUID]:
        doomed_ids = list(
            (
                await session.scalars(
                    select(CodeNode.id).where(
                        CodeNode.repository_id == repository_id,
                        CodeNode.file_path == file_path,
                    )
                )
            ).all()
        )
        if not doomed_ids:
            return set()
        peers: set[UUID | None] = set()
        # Chunked to keep the IN-list under the asyncpg 32767-placeholder
        # cap. `doomed_ids` is per-file (small) today, but the helper is
        # defensive against generated files with very large node counts.
        for batch in chunked(doomed_ids):
            peers.update(
                (
                    await session.scalars(
                        select(CodeEdge.target_node_id).where(
                            CodeEdge.repository_id == repository_id,
                            CodeEdge.source_node_id.in_(batch),
                            CodeEdge.target_node_id.is_not(None),
                        )
                    )
                ).all()
            )
            peers.update(
                (
                    await session.scalars(
                        select(CodeEdge.source_node_id).where(
                            CodeEdge.repository_id == repository_id,
                            CodeEdge.target_node_id.in_(batch),
                        )
                    )
                ).all()
            )
        peers.discard(None)
        # Peers themselves must survive the delete — drop anyone in the doomed set.
        peers.difference_update(doomed_ids)
        return {p for p in peers if p is not None}

    async def _parse_and_persist(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        root_path: Path,
        relative_path: Path,
        source_text: str,
        commit_sha: str | None,
        go_module_path: str | None,
        go_profile: GoIndexProfile | None,
        cache: GraphIngestCache | None = None,
    ):
        extracted_graph = await self._parse_source_graph(
            relative_path=relative_path,
            source_text=source_text,
            go_module_path=go_module_path,
            go_profile=go_profile,
        )
        return await self._persist_preparsed_graph(
            session=session,
            repository_id=repository_id,
            root_path=root_path,
            relative_path=relative_path,
            extracted_graph=extracted_graph,
            commit_sha=commit_sha,
            cache=cache,
        )

    async def _parse_source_graph(
        self,
        *,
        relative_path: Path,
        source_text: str,
        go_module_path: str | None,
        go_profile: GoIndexProfile | None,
    ) -> ExtractedGraph:
        parsed_file = await asyncio.to_thread(
            self._parser.parse_source,
            file_path=relative_path,
            source_text=source_text,
        )
        extracted_graph = await asyncio.to_thread(
            self._extractor.extract,
            parsed_file,
            go_module_path=go_module_path,
        )
        if parsed_file.language is GraphLanguage.GO and go_profile is not None:
            for node in extracted_graph.nodes:
                node.metadata = {
                    **node.metadata,
                    "effective_go_version": go_profile.effective_go_version,
                    "effective_goos": go_profile.effective_goos,
                    "effective_goarch": go_profile.effective_goarch,
                    "effective_cgo": go_profile.effective_cgo,
                }
        return extracted_graph

    async def _persist_preparsed_graph(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        root_path: Path,
        relative_path: Path,
        extracted_graph: ExtractedGraph,
        commit_sha: str | None,
        cache: GraphIngestCache | None = None,
    ):
        temporal_by_key = (
            await asyncio.to_thread(
                collect_node_temporal_metadata,
                root_path=root_path,
                file_path=relative_path,
                nodes=extracted_graph.nodes,
            )
            if (root_path / ".git").exists()
            else {}
        )
        return await self._builder.persist_graph(
            session=session,
            repository_id=repository_id,
            extracted_graph=extracted_graph,
            commit_sha=commit_sha,
            temporal_by_key=temporal_by_key,
            cache=cache,
        )

    async def _parse_go_package_graphs(
        self,
        *,
        package: GoPackageSelection,
        go_module_path: str | None,
        go_profile: GoIndexProfile,
    ) -> dict[str, ExtractedGraph]:
        parsed_graphs: dict[str, ExtractedGraph] = {}
        seen_qualified_names: dict[str, str] = {}
        for selected_file in package.selected_files:
            relative_path = Path(selected_file.relative_path)
            extracted_graph = await self._parse_source_graph(
                relative_path=relative_path,
                source_text=selected_file.source_text,
                go_module_path=go_module_path,
                go_profile=go_profile,
            )
            for node in extracted_graph.nodes:
                # `func init()` and `func _()` are legitimately allowed to
                # appear once per file in a Go package (migrations, registry
                # self-registration). The extractor already pins each one to
                # the file stem (`<pkg>.init@<file_stem>`), so QNs are unique
                # by construction — the collision guard below is for genuine
                # build-tag conflicts on regular symbols (e.g. two files
                # defining `type Config struct` that the build profile
                # failed to disambiguate). Skip init/blank here so the
                # guard doesn't trip on the per-file disambiguator.
                if (
                    node.node_type is GraphNodeType.FUNCTION
                    and node.name in {"init", "_"}
                ):
                    continue
                existing_path = seen_qualified_names.get(node.qualified_name)
                if existing_path is None:
                    seen_qualified_names[node.qualified_name] = node.file_path
                    continue
                if existing_path == node.file_path:
                    continue
                raise GoBuildVariantConflictError(
                    f"Go variant collision for {node.qualified_name}: "
                    f"{existing_path} vs {node.file_path}"
                )
            parsed_graphs[selected_file.relative_path] = extracted_graph
        return parsed_graphs

    async def _prune_missing_files(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        present_paths: tuple[str, ...],
    ) -> None:
        if present_paths:
            await session.execute(
                delete(SourceFile).where(
                    SourceFile.repository_id == repository_id,
                    SourceFile.file_path.not_in(present_paths),
                )
            )
            await session.execute(
                delete(CodeNode).where(
                    CodeNode.repository_id == repository_id,
                    CodeNode.file_path.not_in(present_paths),
                )
            )
            return

        await session.execute(
            delete(SourceFile).where(SourceFile.repository_id == repository_id)
        )
        await session.execute(
            delete(CodeNode).where(CodeNode.repository_id == repository_id)
        )

    def _discover_source_files(self, root_path: Path) -> tuple[Path, ...]:
        if not root_path.exists():
            raise FileNotFoundError(f"Checkout path not found: {root_path}")
        if not root_path.is_dir():
            raise NotADirectoryError(f"Checkout path is not a directory: {root_path}")

        return tuple(
            sorted(
                path
                for path in root_path.rglob("*")
                if path.is_file()
                and detect_graph_language(path.relative_to(root_path)) is not None
            )
        )


def _select_go_packages_from_files(
    root_path: Path,
    go_files: tuple[Path, ...],
    go_profile: GoIndexProfile,
) -> tuple[GoPackageSelection, ...]:
    files_by_package: dict[str, list[Path]] = defaultdict(list)
    for file_path in go_files:
        files_by_package[_package_key(file_path.relative_to(root_path))].append(file_path)
    return tuple(
        select_go_package_files(
            root_path=root_path,
            package_key=package_key,
            files=tuple(sorted(files)),
            profile=go_profile,
        )
        for package_key, files in sorted(files_by_package.items())
    )


def _select_go_packages_from_keys(
    root_path: Path,
    package_keys: tuple[str, ...],
    go_profile: GoIndexProfile,
) -> tuple[GoPackageSelection, ...]:
    selections: list[GoPackageSelection] = []
    for package_key in package_keys:
        package_dir = root_path if package_key == "." else root_path / package_key
        if not package_dir.exists():
            files = ()
        else:
            files = tuple(
                sorted(
                    path
                    for path in package_dir.iterdir()
                    if path.is_file()
                    and detect_graph_language(path.relative_to(root_path))
                    is GraphLanguage.GO
                )
            )
        selections.append(
            select_go_package_files(
                root_path=root_path,
                package_key=package_key,
                files=files,
                profile=go_profile,
            )
        )
    return tuple(selections)


def _package_key(file_path: Path) -> str:
    parent = file_path.parent.as_posix()
    return parent or "."


def _touches_root_go_mod(change: GitFileChange) -> bool:
    return change.file_path == "go.mod" or change.old_file_path == "go.mod"


def _change_touches_go_package(change: GitFileChange) -> bool:
    if detect_graph_language(Path(change.file_path)) is GraphLanguage.GO:
        return True
    if change.old_file_path is None:
        return False
    return detect_graph_language(Path(change.old_file_path)) is GraphLanguage.GO


def _change_go_package_keys(change: GitFileChange) -> tuple[str, ...]:
    package_keys: list[str] = []
    if detect_graph_language(Path(change.file_path)) is GraphLanguage.GO:
        package_keys.append(_package_key(Path(change.file_path)))
    if (
        change.old_file_path is not None
        and detect_graph_language(Path(change.old_file_path)) is GraphLanguage.GO
    ):
        old_package_key = _package_key(Path(change.old_file_path))
        if old_package_key not in package_keys:
            package_keys.append(old_package_key)
    return tuple(package_keys)


def _detect_git_changes_safely(root_path: Path, since_commit: str) -> list[GitFileChange] | None:
    if not _SHA_PATTERN.match(since_commit):
        logger.warning(
            "git_diff_invalid_sha", extra={"since": since_commit, "repo": str(root_path)}
        )
        return None
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root_path),
                "diff",
                "--name-status",
                f"{since_commit}..HEAD",
                "--",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "git_diff_failed",
            extra={
                "since": since_commit,
                "repo": str(root_path),
                "returncode": exc.returncode,
                "stderr": exc.stderr[:500] if exc.stderr else "",
            },
        )
        return None
    except subprocess.TimeoutExpired:
        logger.warning(
            "git_diff_timeout", extra={"since": since_commit, "repo": str(root_path)}
        )
        return None
    except FileNotFoundError:
        logger.warning("git_diff_no_git_cli", extra={"repo": str(root_path)})
        return None

    changes: list[GitFileChange] = []
    for raw_line in completed.stdout.splitlines():
        parts = raw_line.split("\t")
        if not parts or not parts[0]:
            continue
        status = parts[0]
        head = status[0]
        if head == "R" and len(parts) >= 3:
            changes.append(
                GitFileChange(kind="R", old_file_path=parts[1], file_path=parts[2])
            )
            continue
        if head in {"A", "M", "D"} and len(parts) >= 2:
            changes.append(GitFileChange(kind=head, file_path=parts[1]))
    return changes


def _detect_go_module_path(root_path: Path) -> str | None:
    go_mod = root_path / "go.mod"
    if not go_mod.is_file():
        return None

    try:
        for line in go_mod.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("module "):
                module_path = stripped.removeprefix("module").strip()
                return module_path or None
    except OSError:
        return None

    return None


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
