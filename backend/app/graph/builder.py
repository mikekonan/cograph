from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import Select, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.graph._chunking import chunked
from backend.app.graph.extractor import (
    ExtractedEdge,
    ExtractedGraph,
    ExtractedNode,
    GraphEdgeType,
    GraphNodeType,
)
from backend.app.graph.ingest_cache import GraphIngestCache
from backend.app.graph.temporal import NodeTemporalMetadata
from backend.app.models.code_edge import CodeEdge
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, SourceFileKind
from backend.app.models.source_file import SourceFile


@dataclass(slots=True, kw_only=True)
class GraphBuildResult:
    inserted_nodes: int
    replaced_files: tuple[str, ...]
    resolved_calls: int
    unresolved_calls: int


class GraphBuilder:
    async def persist_graph(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        extracted_graph: ExtractedGraph,
        commit_sha: str | None = None,
        temporal_by_key: dict[tuple[str, str], NodeTemporalMetadata] | None = None,
        cache: GraphIngestCache | None = None,
    ) -> GraphBuildResult:
        if not extracted_graph.nodes:
            return GraphBuildResult(
                inserted_nodes=0,
                replaced_files=(),
                resolved_calls=0,
                unresolved_calls=0,
            )

        # Collapse `typing.overload` stubs (and any other same-qn duplicates
        # within a file) into a single node. The UNIQUE
        # (repository_id, qualified_name) constraint otherwise rejects the
        # second stub, marking the whole ingest as ERROR. Python semantics
        # say the last definition wins, so the implementation survives and
        # the stub signatures move into metadata["overloads"].
        deduped_nodes = _dedupe_nodes_by_qualified_name(extracted_graph.nodes)

        nodes_by_file: dict[str, list[ExtractedNode]] = defaultdict(list)
        module_by_file: dict[str, ExtractedNode] = {}
        for extracted_node in deduped_nodes:
            nodes_by_file[extracted_node.file_path].append(extracted_node)
            if extracted_node.node_type is GraphNodeType.MODULE:
                module_by_file[extracted_node.file_path] = extracted_node

        changed_file_paths = tuple(sorted(nodes_by_file.keys()))

        source_file_id_by_path = await self._upsert_source_files(
            session=session,
            repository_id=repository_id,
            module_by_file=module_by_file,
            commit_sha=commit_sha,
        )

        existing_nodes_for_files = list(
            (
                await session.scalars(
                    select(CodeNode).where(
                        CodeNode.repository_id == repository_id,
                        CodeNode.file_path.in_(changed_file_paths),
                    )
                )
            ).all()
        )
        existing_by_key: dict[tuple[str, str], CodeNode] = {
            (node.file_path, node.symbol_key): node
            for node in existing_nodes_for_files
            if node.symbol_key
        }
        # A file has one module node. When the extractor renames it (an old
        # Go module qn had no `#module` suffix), the key misses — reuse the
        # row anyway so its id, embedding and wiki citations survive.
        extracted_keys = {(node.file_path, node.symbol_key) for node in deduped_nodes}
        orphan_module_by_file: dict[str, CodeNode] = {
            node.file_path: node
            for node in existing_nodes_for_files
            if node.node_type is CodeNodeType.MODULE
            and (node.file_path, node.symbol_key) not in extracted_keys
        }

        nodes_by_qualified_name: dict[str, CodeNode] = {}
        preserved_node_ids: list[UUID] = []
        replaced_or_removed_qns: set[str] = set()
        renamed_non_module = False
        kept_existing_ids: set[UUID] = set()
        pending_inserts: list[tuple[ExtractedNode, str, UUID]] = []

        for extracted_node in deduped_nodes:
            symbol_key = extracted_node.symbol_key
            key = (extracted_node.file_path, symbol_key)
            existing = existing_by_key.get(key)
            if existing is None and extracted_node.node_type is GraphNodeType.MODULE:
                existing = orphan_module_by_file.pop(extracted_node.file_path, None)
            content_hash = _content_hash(extracted_node.content)
            content_changed = existing is None or existing.content_hash != content_hash
            source_file_id = source_file_id_by_path[extracted_node.file_path]
            temporal = (temporal_by_key or {}).get(
                _temporal_lookup_key(
                    file_path=extracted_node.file_path,
                    symbol_key=extracted_node.symbol_key,
                    qualified_name=extracted_node.qualified_name,
                )
            )

            if existing is not None:
                old_qualified_name = existing.qualified_name
                if (
                    old_qualified_name != extracted_node.qualified_name
                    and extracted_node.node_type is not GraphNodeType.MODULE
                ):
                    renamed_non_module = True
                existing.qualified_name = extracted_node.qualified_name
                existing.symbol_key = extracted_node.symbol_key
                existing.node_type = _to_model_node_type(extracted_node.node_type)
                existing.name = extracted_node.name
                existing.language = extracted_node.language.value
                existing.start_line = extracted_node.start_line
                existing.end_line = extracted_node.end_line
                existing.start_byte = extracted_node.start_byte
                existing.end_byte = extracted_node.end_byte
                existing.content = extracted_node.content
                existing.signature = extracted_node.signature
                existing.doc_comment = extracted_node.doc_comment
                existing.role = extracted_node.role
                existing.source_file_id = source_file_id
                existing.content_hash = content_hash
                existing.node_metadata = dict(extracted_node.metadata)
                if temporal is not None:
                    if existing.first_seen_commit is None:
                        existing.first_seen_commit = temporal.first_seen_commit
                    if content_changed and commit_sha is not None:
                        existing.last_changed_commit = commit_sha
                    else:
                        existing.last_changed_commit = temporal.last_changed_commit
                    existing.last_changed_at = temporal.last_changed_at
                nodes_by_qualified_name[extracted_node.qualified_name] = existing
                preserved_node_ids.append(existing.id)
                kept_existing_ids.add(existing.id)
                if cache is not None:
                    cache.rename(existing, old_qualified_name)
                continue

            pending_inserts.append((extracted_node, content_hash, source_file_id))

        doomed_nodes = [
            node for node in existing_nodes_for_files if node.id not in kept_existing_ids
        ]
        doomed_ids = {node.id for node in doomed_nodes}
        await detach_surviving_children(
            session, repository_id=repository_id, doomed_ids=list(doomed_ids)
        )
        for existing in doomed_nodes:
            replaced_or_removed_qns.add(existing.qualified_name)
            await session.delete(existing)
            if cache is not None:
                cache.remove(existing)

        # Flush deletes before inserts so a freshly-rotated symbol_key
        # (with the same qualified_name as the row we just deleted)
        # doesn't trip the UNIQUE (repository_id, qualified_name) index.
        # SQLAlchemy doesn't reorder DELETE-then-INSERT for us when both
        # are queued in the same flush.
        if existing_nodes_for_files:
            await session.flush()

        for extracted_node, content_hash, source_file_id in pending_inserts:
            temporal = (temporal_by_key or {}).get(
                _temporal_lookup_key(
                    file_path=extracted_node.file_path,
                    symbol_key=extracted_node.symbol_key,
                    qualified_name=extracted_node.qualified_name,
                )
            )
            new_node = CodeNode(
                repository_id=repository_id,
                source_file_id=source_file_id,
                file_path=extracted_node.file_path,
                qualified_name=extracted_node.qualified_name,
                symbol_key=extracted_node.symbol_key,
                node_type=_to_model_node_type(extracted_node.node_type),
                name=extracted_node.name,
                language=extracted_node.language.value,
                start_line=extracted_node.start_line,
                end_line=extracted_node.end_line,
                start_byte=extracted_node.start_byte,
                end_byte=extracted_node.end_byte,
                content=extracted_node.content,
                signature=extracted_node.signature,
                doc_comment=extracted_node.doc_comment,
                role=extracted_node.role,
                node_metadata=dict(extracted_node.metadata),
                content_hash=content_hash,
                first_seen_commit=(
                    temporal.first_seen_commit if temporal is not None else commit_sha
                ),
                last_changed_commit=(
                    temporal.last_changed_commit if temporal is not None else commit_sha
                ),
                last_changed_at=temporal.last_changed_at
                if temporal is not None
                else None,
            )
            session.add(new_node)
            nodes_by_qualified_name[extracted_node.qualified_name] = new_node

        await session.flush()

        if cache is not None:
            # Add new nodes to the cache only after `flush()` has populated
            # the server-side default for `id` — the cache is keyed by id,
            # and pre-flush ids are None.
            for extracted_node, *_ in pending_inserts:
                cache.add(nodes_by_qualified_name[extracted_node.qualified_name])

        for extracted_node in deduped_nodes:
            db_node = nodes_by_qualified_name[extracted_node.qualified_name]
            if extracted_node.parent_qualified_name is None:
                db_node.parent_id = None
                continue
            parent_node = nodes_by_qualified_name.get(
                extracted_node.parent_qualified_name
            )
            if parent_node is not None:
                db_node.parent_id = parent_node.id

        grouped_edges = _group_extracted_edges(extracted_graph.edges)
        for source_qn, targets in grouped_edges[GraphEdgeType.IMPORTS].items():
            source_node = nodes_by_qualified_name.get(source_qn)
            if source_node is None:
                continue
            source_node.node_metadata = _set_metadata_list(
                source_node.node_metadata, "imports", targets
            )
        for source_qn, targets in grouped_edges[GraphEdgeType.INHERITS].items():
            source_node = nodes_by_qualified_name.get(source_qn)
            if source_node is None:
                continue
            source_node.node_metadata = _set_metadata_list(
                source_node.node_metadata, "inherits", targets
            )
        for source_qn, targets in grouped_edges[GraphEdgeType.CALLS].items():
            source_node = nodes_by_qualified_name.get(source_qn)
            if source_node is None:
                continue
            source_node.node_metadata = _set_metadata_list(
                source_node.node_metadata, "calls", targets
            )

        await session.flush()

        # Capture target peers from edges we're about to delete so we can
        # rebuild their back-compat arrays after the rewrite. Without this,
        # a peer whose incoming edge was removed keeps a stale UUID in
        # callers/callees.
        peers_from_deleted_outbound: set[UUID] = set()
        if preserved_node_ids:
            # Chunked to dodge the asyncpg 32767-placeholder cap on large repos
            # where `preserved_node_ids` can exceed it on a full re-resolve.
            old_target_ids: list[UUID | None] = []
            for batch in chunked(preserved_node_ids):
                old_target_ids.extend(
                    (
                        await session.scalars(
                            select(CodeEdge.target_node_id).where(
                                CodeEdge.source_node_id.in_(batch),
                                CodeEdge.target_node_id.is_not(None),
                            )
                        )
                    ).all()
                )
            peers_from_deleted_outbound = {
                tid for tid in old_target_ids if tid is not None
            }
            for batch in chunked(preserved_node_ids):
                await session.execute(
                    delete(CodeEdge).where(CodeEdge.source_node_id.in_(batch))
                )

        # The cache (when provided by the ingest loop) mirrors every CodeNode
        # in the repository and has been mutated in-place above by the
        # insert/update/delete branches. When no cache is passed in (test
        # call sites, single-shot callers) fall back to the historical
        # repo-wide SELECT so behaviour stays identical.
        if cache is None:
            repository_nodes = list(
                (
                    await session.scalars(
                        select(CodeNode).where(CodeNode.repository_id == repository_id)
                    )
                ).all()
            )
            node_by_id = {node.id: node for node in repository_nodes}
            nodes_by_qn_full = {node.qualified_name: node for node in repository_nodes}
            module_nodes_by_file_path = {
                node.file_path: node
                for node in repository_nodes
                if node.node_type is CodeNodeType.MODULE
            }
        else:
            repository_nodes = cache.repository_nodes()
            node_by_id = cache.node_by_id
            nodes_by_qn_full = cache.nodes_by_qn
            module_nodes_by_file_path = cache.module_nodes_by_file_path
        # A method's parent is the node named by its qn minus the last part,
        # never a module. Methods of other files can move only when this call
        # adds, drops or renames a non-module node; else this file's methods
        # are enough. Such a file still re-links every method, O(repo nodes);
        # a cache index of methods by parent qn would remove that.
        parents_may_move = (
            renamed_non_module
            or any(
                node.node_type is not GraphNodeType.MODULE
                for node, _, _ in pending_inserts
            )
            or any(node.node_type is not CodeNodeType.MODULE for node in doomed_nodes)
        )
        _refresh_method_parents(
            repository_nodes=(
                repository_nodes
                if parents_may_move
                else list(nodes_by_qualified_name.values())
            ),
            nodes_by_qualified_name=nodes_by_qn_full,
        )

        new_edges: list[CodeEdge] = []
        seen_edges: set[tuple[UUID, str, str]] = set()
        resolved_calls_count = 0
        unresolved_calls_count = 0
        unresolved_by_source_qn: dict[str, list[str]] = defaultdict(list)

        for extracted_edge in extracted_graph.edges:
            source_node = nodes_by_qualified_name.get(extracted_edge.source)
            if source_node is None:
                continue

            canonical_target_name = _canonical_target_name(
                edge_type=extracted_edge.edge_type,
                raw_target=extracted_edge.target,
                source_node=source_node,
                module_nodes_by_file_path=module_nodes_by_file_path,
                node_by_id=node_by_id,
                nodes_by_qualified_name=nodes_by_qn_full,
            )
            edge_key = (
                source_node.id,
                extracted_edge.edge_type.value,
                canonical_target_name,
            )
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            resolved_target = _resolve_edge_target(
                edge_type=extracted_edge.edge_type,
                canonical_target_name=canonical_target_name,
                nodes_by_qualified_name=nodes_by_qn_full,
            )

            target_node_id = resolved_target.id if resolved_target is not None else None

            new_edges.append(
                CodeEdge(
                    repository_id=repository_id,
                    source_node_id=source_node.id,
                    target_node_id=target_node_id,
                    target_qualified_name=canonical_target_name,
                    edge_type=extracted_edge.edge_type.value,
                )
            )

            if extracted_edge.edge_type is GraphEdgeType.CALLS:
                if resolved_target is not None:
                    resolved_calls_count += 1
                else:
                    unresolved_calls_count += 1
                    unresolved_by_source_qn[extracted_edge.source].append(
                        extracted_edge.target
                    )

        session.add_all(new_edges)
        await session.flush()

        affected_qualified_names = (
            set(nodes_by_qualified_name.keys()) | replaced_or_removed_qns
        )
        source_nodes_to_refresh_unresolved: set[UUID] = set()
        peers_from_reresolved: set[UUID] = set()
        reresolved_target_ids: set[UUID] = set()
        if affected_qualified_names:
            candidate_edges = list(
                (
                    await session.scalars(
                        select(CodeEdge).where(
                            CodeEdge.repository_id == repository_id,
                            CodeEdge.target_node_id.is_(None),
                            CodeEdge.target_qualified_name.in_(
                                affected_qualified_names
                            ),
                        )
                    )
                ).all()
            )
            for edge in candidate_edges:
                source_node = node_by_id.get(edge.source_node_id)
                if source_node is None:
                    continue
                # Either outcome affects this peer's back-compat arrays.
                peers_from_reresolved.add(source_node.id)
                resolved_target = _resolve_edge_target(
                    edge_type=GraphEdgeType(edge.edge_type),
                    canonical_target_name=edge.target_qualified_name,
                    nodes_by_qualified_name=nodes_by_qn_full,
                )
                if resolved_target is not None:
                    edge.target_node_id = resolved_target.id
                    if edge.edge_type == GraphEdgeType.CALLS.value:
                        reresolved_target_ids.add(resolved_target.id)
                        source_nodes_to_refresh_unresolved.add(source_node.id)
                        # A previously-dangling CALLS edge just got bound to
                        # a real target. The accumulated per-file deltas
                        # need this so they match the post-rebuild repo
                        # totals callers expect.
                        resolved_calls_count += 1
                        unresolved_calls_count -= 1

        if source_nodes_to_refresh_unresolved:
            # Chunked to keep the IN-list under the asyncpg 32767-placeholder
            # cap on full re-resolves of large monorepos.
            remaining_unresolved: list[CodeEdge] = []
            for batch in chunked(source_nodes_to_refresh_unresolved):
                remaining_unresolved.extend(
                    (
                        await session.scalars(
                            select(CodeEdge).where(
                                CodeEdge.repository_id == repository_id,
                                CodeEdge.source_node_id.in_(batch),
                                CodeEdge.edge_type == GraphEdgeType.CALLS.value,
                                CodeEdge.target_node_id.is_(None),
                            )
                        )
                    ).all()
                )
            unresolved_by_source_id: dict[UUID, list[str]] = defaultdict(list)
            for edge in remaining_unresolved:
                unresolved_by_source_id[edge.source_node_id].append(
                    edge.target_qualified_name
                )
            for source_id in source_nodes_to_refresh_unresolved:
                source_node = node_by_id.get(source_id)
                if source_node is None:
                    continue
                unresolved_targets_for_node = unresolved_by_source_id.get(source_id)
                if unresolved_targets_for_node:
                    source_node.node_metadata = _set_metadata_list(
                        source_node.node_metadata,
                        "unresolved_calls",
                        unresolved_targets_for_node,
                    )
                else:
                    source_node.node_metadata = _without_metadata_key(
                        source_node.node_metadata, "unresolved_calls"
                    )

        # Scope the back-compat rebuild to touched nodes only: the previous
        # whole-repo SELECT + UPDATE loop was O(N) per file ingest and was
        # explicitly required to be removed by the refactor plan.
        touched_ids: set[UUID] = {node.id for node in nodes_by_qualified_name.values()}
        touched_ids.update(preserved_node_ids)
        touched_ids.update(peers_from_deleted_outbound)
        touched_ids.update(peers_from_reresolved)
        touched_ids.update(source_nodes_to_refresh_unresolved)
        touched_ids.update(
            edge.target_node_id for edge in new_edges if edge.target_node_id is not None
        )
        touched_ids.discard(None)  # type: ignore[arg-type]

        # Every edge this call changed starts or ends at a node of this file,
        # at a re-resolved target, or at a deleted node. Those get a full
        # rebuild; the other touched nodes (hub callees above all) only
        # swap their entries for them.
        rebuilt_ids = {node.id for node in nodes_by_qualified_name.values()}
        rebuilt_ids.update(reresolved_target_ids)
        await self._rebuild_back_compat_arrays(
            session=session,
            repository_id=repository_id,
            repository_nodes=repository_nodes,
            touched_ids=rebuilt_ids,
            peer_ids=touched_ids - rebuilt_ids,
            dropped_ids=doomed_ids,
        )

        for source_qn, unresolved_targets in unresolved_by_source_qn.items():
            node = nodes_by_qualified_name.get(source_qn)
            if node is None:
                continue
            node.node_metadata = _set_metadata_list(
                node.node_metadata, "unresolved_calls", unresolved_targets
            )

        await session.flush()

        return GraphBuildResult(
            inserted_nodes=len(nodes_by_qualified_name),
            replaced_files=changed_file_paths,
            resolved_calls=resolved_calls_count,
            unresolved_calls=unresolved_calls_count,
        )

    async def rebuild_relationships(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        cache: GraphIngestCache | None = None,
    ) -> None:
        if cache is not None:
            repository_nodes = cache.repository_nodes()
        else:
            repository_nodes = list(
                (
                    await session.scalars(
                        select(CodeNode).where(CodeNode.repository_id == repository_id)
                    )
                ).all()
            )
        await self._rebuild_back_compat_arrays(
            session=session,
            repository_id=repository_id,
            repository_nodes=repository_nodes,
        )
        await session.flush()

    async def rebuild_relationships_scoped(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        touched_ids: set[UUID],
    ) -> None:
        """Refresh back-compat arrays only for the given node ids.

        Used by the incremental ingest's delete-only path so peers whose
        inbound edges were CASCADE-nulled don't carry dangling UUIDs in their
        callers/callees arrays.
        """
        if not touched_ids:
            return
        repository_nodes: list[CodeNode] = []
        for batch in chunked(touched_ids):
            repository_nodes.extend(
                (
                    await session.scalars(
                        select(CodeNode).where(
                            CodeNode.repository_id == repository_id,
                            CodeNode.id.in_(batch),
                        )
                    )
                ).all()
            )
        await self._rebuild_back_compat_arrays(
            session=session,
            repository_id=repository_id,
            repository_nodes=repository_nodes,
            touched_ids=touched_ids,
        )
        await session.flush()

    async def _upsert_source_files(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        module_by_file: dict[str, ExtractedNode],
        commit_sha: str | None,
    ) -> dict[str, UUID]:
        existing_source_files = list(
            (
                await session.scalars(
                    select(SourceFile).where(
                        SourceFile.repository_id == repository_id,
                        SourceFile.file_path.in_(list(module_by_file.keys())),
                    )
                )
            ).all()
        )
        existing_by_path = {sf.file_path: sf for sf in existing_source_files}

        source_file_id_by_path: dict[str, UUID] = {}
        for file_path, module_node in module_by_file.items():
            raw_bytes = module_node.content.encode("utf-8")
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            existing = existing_by_path.get(file_path)
            if existing is not None:
                existing.raw_bytes = raw_bytes
                existing.content_hash = content_hash
                existing.blob_hash = content_hash
                existing.bytes = len(raw_bytes)
                existing.language = module_node.language.value
                existing.kind = SourceFileKind.CODE.value
                if commit_sha is not None:
                    existing.commit_sha = commit_sha
                source_file_id_by_path[file_path] = existing.id
                continue

            new_source_file = SourceFile(
                repository_id=repository_id,
                file_path=file_path,
                language=module_node.language.value,
                kind=SourceFileKind.CODE.value,
                raw_bytes=raw_bytes,
                content_hash=content_hash,
                blob_hash=content_hash,
                bytes=len(raw_bytes),
                commit_sha=commit_sha,
            )
            session.add(new_source_file)
            await session.flush()
            source_file_id_by_path[file_path] = new_source_file.id

        return source_file_id_by_path

    async def _rebuild_back_compat_arrays(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        repository_nodes: list[CodeNode] | None = None,
        touched_ids: set[UUID] | None = None,
        peer_ids: set[UUID] | frozenset[UUID] = frozenset(),
        dropped_ids: set[UUID] | frozenset[UUID] = frozenset(),
    ) -> None:
        """Rewrite `callers`/`callees` from the CALLS edges.

        `touched_ids` (all nodes when None) are rebuilt from every edge that
        touches them. `peer_ids` keep their arrays and only swap the entries
        for `touched_ids` and `dropped_ids`, so a hub callee costs its edges to
        the touched nodes, not all of its edges. That is exact only when
        every changed edge starts or ends in `touched_ids` or `dropped_ids`.
        """
        if repository_nodes is None:
            repository_nodes = list(
                (
                    await session.scalars(
                        select(CodeNode).where(CodeNode.repository_id == repository_id)
                    )
                ).all()
            )
        node_by_id = {node.id: node for node in repository_nodes}

        if touched_ids is None:
            # Full rebuild — used by the standalone rebuild_relationships path
            # and by the delete-only incremental branch.
            nodes_to_reset = repository_nodes
        else:
            if not touched_ids:
                return
            nodes_to_reset = [
                node_by_id[node_id] for node_id in touched_ids if node_id in node_by_id
            ]

        # Id pairs, not ORM rows: loading CodeEdge objects dominated the walk.
        base_filter = (
            CodeEdge.repository_id == repository_id,
            CodeEdge.edge_type == GraphEdgeType.CALLS.value,
            CodeEdge.target_node_id.is_not(None),
        )
        pairs: Iterable[tuple[UUID, UUID]]
        if touched_ids is None:
            pairs = (
                await session.execute(
                    select(CodeEdge.source_node_id, CodeEdge.target_node_id).where(
                        *base_filter
                    )
                )
            ).all()
        else:
            # Query only edges touching the affected id set. Peers outside that
            # set keep their arrays intact, so we avoid O(repo) UPDATEs. Chunked
            # to stay under the asyncpg 32767-placeholder cap: a fresh sync of
            # a large monorepo touches >32k node ids and would otherwise crash
            # the resolver pass with `the number of query arguments cannot
            # exceed 32767`. Run the source-side and target-side IN-lists as
            # separate chunked sweeps and dedupe by edge.id (Postgres expands
            # IN-OR into one statement, so a combined-OR rewrite wouldn't fit
            # either even after chunking one side).
            edges_by_id: dict[UUID, tuple[UUID, UUID]] = {}
            for batch in chunked(touched_ids):
                for column in (CodeEdge.source_node_id, CodeEdge.target_node_id):
                    rows = await session.execute(
                        select(
                            CodeEdge.id, CodeEdge.source_node_id, CodeEdge.target_node_id
                        ).where(*base_filter, column.in_(batch))
                    )
                    for edge_id, source_id, target_id in rows:
                        edges_by_id[edge_id] = (source_id, target_id)
            pairs = edges_by_id.values()

        # dicts as ordered sets: edge order, first occurrence wins.
        peers = [node_by_id[node_id] for node_id in peer_ids if node_id in node_by_id]
        callees: dict[UUID, dict[str, None]] = {
            n.id: {} for n in (*nodes_to_reset, *peers)
        }
        callers: dict[UUID, dict[str, None]] = {
            n.id: {} for n in (*nodes_to_reset, *peers)
        }
        for source_id, target_id in pairs:
            if source_id not in node_by_id or target_id not in node_by_id:
                continue
            # A node outside both sets keeps whatever it had — its UUID-list
            # invariant is preserved because none of its edges changed.
            if source_id in callees:
                callees[source_id][str(target_id)] = None
            if target_id in callers:
                callers[target_id][str(source_id)] = None

        for node in nodes_to_reset:
            _assign_if_changed(node, "callees", list(callees[node.id]))
            _assign_if_changed(node, "callers", list(callers[node.id]))
        stale = {str(node_id) for node_id in (*(touched_ids or ()), *dropped_ids)}
        for node in peers:
            _assign_if_changed(
                node, "callees", _swapped(node.callees, stale, callees[node.id])
            )
            _assign_if_changed(
                node, "callers", _swapped(node.callers, stale, callers[node.id])
            )


def _swapped(current: list[str], stale: set[str], fresh: dict[str, None]) -> list[str]:
    """Drop `stale` entries that are not in `fresh`, append new `fresh` ones.

    Surviving entries keep their position, so a peer whose edges came back
    unchanged gets an identical list and no UPDATE.
    """
    kept = [item for item in current if item not in stale or item in fresh]
    present = set(kept)
    return kept + [item for item in fresh if item not in present]


def _assign_if_changed(node: CodeNode, attribute: str, value: list[str]) -> None:
    # Skipping equal lists keeps an unchanged hub out of the flush.
    if getattr(node, attribute) != value:
        setattr(node, attribute, value)


def _dedupe_nodes_by_qualified_name(nodes):
    by_qn: dict[str, "object"] = {}
    shadowed: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        qn = node.qualified_name
        if qn in by_qn:
            prev = by_qn[qn]
            if prev.signature:
                shadowed[qn].append(prev.signature)
        by_qn[qn] = node

    result = []
    for node in by_qn.values():
        qn = node.qualified_name
        if qn not in shadowed:
            result.append(node)
            continue
        merged_metadata = dict(node.metadata)
        existing_overloads = merged_metadata.get("overloads", [])
        if not isinstance(existing_overloads, list):
            existing_overloads = []
        merged_metadata["overloads"] = [*existing_overloads, *shadowed[qn]]
        result.append(replace(node, metadata=merged_metadata))
    return result


def _temporal_lookup_key(
    *,
    file_path: str,
    symbol_key: str,
    qualified_name: str,
) -> tuple[str, str]:
    return (file_path, symbol_key or qualified_name)


def _canonical_target_name(
    *,
    edge_type: GraphEdgeType,
    raw_target: str,
    source_node: CodeNode,
    module_nodes_by_file_path: dict[str, CodeNode],
    node_by_id: dict[UUID, CodeNode],
    nodes_by_qualified_name: dict[str, CodeNode],
) -> str:
    if edge_type is not GraphEdgeType.CALLS:
        return raw_target

    module_node = module_nodes_by_file_path.get(source_node.file_path)

    if raw_target.startswith("self.") and source_node.parent_id is not None:
        parent_node = node_by_id.get(source_node.parent_id)
        if parent_node is not None:
            return f"{parent_node.qualified_name}.{raw_target.removeprefix('self.')}"

    # `this.` (TS/JS) means "member of my class" — but only inside class
    # members. A top-level function's `this` is caller-bound, so the rewrite
    # is gated on the parent actually being a class.
    if (
        raw_target.startswith("this.")
        and source_node.language in ("typescript", "javascript")
        and source_node.parent_id is not None
    ):
        parent_node = node_by_id.get(source_node.parent_id)
        if parent_node is not None and parent_node.node_type is CodeNodeType.CLASS:
            return f"{parent_node.qualified_name}.{raw_target.removeprefix('this.')}"

    if source_node.language == "go":
        receiver_name = _metadata_str(source_node.node_metadata, "receiver_name")
        if receiver_name and raw_target.startswith(f"{receiver_name}."):
            parent_qualified_name, _, _ = source_node.qualified_name.rpartition(".")
            if parent_qualified_name:
                return f"{parent_qualified_name}{raw_target[len(receiver_name) :]}"

    if module_node is not None:
        import_candidates: list[str] = []
        for import_target in _metadata_list(module_node.node_metadata, "imports"):
            normalized_import = _normalize_import_target(
                import_target=import_target,
                module_node=module_node,
            )
            candidate = _candidate_from_import(
                raw_target=raw_target,
                import_target=normalized_import,
            )
            if candidate is not None and candidate not in import_candidates:
                import_candidates.append(candidate)
        if len(import_candidates) == 1:
            return import_candidates[0]

    if source_node.language == "go" and module_node is not None:
        package_qualified_name = _metadata_str(
            module_node.node_metadata,
            "package_qualified_name",
        )
        if package_qualified_name:
            if "." not in raw_target and not _is_go_builtin(raw_target):
                return f"{package_qualified_name}.{raw_target}"
            if "." in raw_target:
                type_candidate = f"{package_qualified_name}.{raw_target}"
                first_segment = raw_target.split(".", 1)[0]
                if (
                    first_segment[:1].isupper()
                    or type_candidate in nodes_by_qualified_name
                ):
                    return type_candidate

    if "." not in raw_target and module_node is not None:
        return f"{module_node.qualified_name}.{raw_target}"

    return raw_target


def _resolve_edge_target(
    *,
    edge_type: GraphEdgeType,
    canonical_target_name: str,
    nodes_by_qualified_name: dict[str, CodeNode],
) -> CodeNode | None:
    direct = nodes_by_qualified_name.get(canonical_target_name)
    if direct is not None:
        return direct

    if edge_type is GraphEdgeType.INHERITS:
        matches = [
            candidate
            for qualified_name, candidate in nodes_by_qualified_name.items()
            if qualified_name.endswith(f".{canonical_target_name}")
            or candidate.name == canonical_target_name
        ]
        if len(matches) == 1:
            return matches[0]

    return None


def _normalize_import_target(*, import_target: str, module_node: CodeNode) -> str:
    # Preserve ` as <alias>` across normalization so aliased relative imports
    # (e.g. `from . import utils as u`) still carry the local name downstream.
    alias_suffix = ""
    working = import_target
    if " as " in working:
        canonical, _, alias = working.partition(" as ")
        working = canonical.strip()
        alias_suffix = f" as {alias.strip()}" if alias.strip() else ""

    if not working.startswith("."):
        return f"{working}{alias_suffix}"

    level = len(working) - len(working.lstrip("."))
    remainder = working[level:]
    package_parts = _package_parts(module_node)
    up_levels = max(level - 1, 0)
    if up_levels > len(package_parts):
        return f"{remainder}{alias_suffix}"

    base_parts = package_parts[: len(package_parts) - up_levels]
    if not remainder:
        return f"{'.'.join(base_parts)}{alias_suffix}"
    return f"{'.'.join([*base_parts, remainder])}{alias_suffix}"


def _package_parts(module_node: CodeNode) -> list[str]:
    qualified_name_parts = module_node.qualified_name.split(".")
    if _is_package_module(module_node.file_path):
        return qualified_name_parts
    return qualified_name_parts[:-1]


def _is_package_module(file_path: str) -> bool:
    return file_path == "__init__.py" or file_path.endswith("/__init__.py")


def _candidate_from_import(*, raw_target: str, import_target: str) -> str | None:
    if not import_target or import_target.endswith(".*"):
        return None
    # `canonical as alias` means call-sites use `alias` but the edge resolves
    # against `canonical`. Without this branch, aliased imports are silently
    # unresolvable.
    if " as " in import_target:
        canonical, _, alias = import_target.partition(" as ")
        canonical = canonical.strip()
        alias = alias.strip()
        if not canonical or not alias:
            return None
        if alias == "_":
            return None
        if alias == ".":
            if "." in raw_target:
                return None
            return f"{canonical}.{raw_target}"
        if raw_target == alias:
            return canonical
        if raw_target.startswith(f"{alias}."):
            return f"{canonical}{raw_target[len(alias) :]}"
        return None
    local_name = import_target.rsplit(".", 1)[-1]
    if raw_target == local_name:
        return import_target
    if raw_target.startswith(f"{local_name}."):
        return f"{import_target}{raw_target[len(local_name) :]}"
    return None


def _group_extracted_edges(
    edges: list[ExtractedEdge],
) -> dict[GraphEdgeType, dict[str, list[str]]]:
    grouped: dict[GraphEdgeType, dict[str, list[str]]] = {
        edge_type: defaultdict(list) for edge_type in GraphEdgeType
    }
    for edge in edges:
        grouped[edge.edge_type][edge.source].append(edge.target)
    return grouped


def _set_metadata_list(
    metadata: dict[str, object],
    key: str,
    values: list[str],
) -> dict[str, object]:
    updated = dict(metadata)
    target_list: list[str] = []
    for value in values:
        if value not in target_list:
            target_list.append(value)
    if target_list:
        updated[key] = target_list
    else:
        updated.pop(key, None)
    return updated


def _metadata_list(metadata: dict[str, object], key: str) -> list[str]:
    values = metadata.get(key)
    return list(values) if isinstance(values, list) else []


def _metadata_str(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _without_metadata_key(metadata: dict[str, object], key: str) -> dict[str, object]:
    updated = dict(metadata)
    updated.pop(key, None)
    return updated


async def detach_surviving_children(
    session: AsyncSession,
    *,
    repository_id: UUID,
    doomed_ids: list[UUID] | Select[tuple[UUID]],
) -> None:
    """Clear `parent_id` on every child of the nodes about to be deleted.

    `code_nodes.parent_id` is ON DELETE CASCADE, so deleting a parent
    silently deletes its children too — a kept node of the same file, or a
    Go method whose receiver type lives in another file. The ORM still
    holds those rows and the next flush fails on them. Callers re-parent
    what survives (the parent pass, `_refresh_method_parents`).
    """
    batches = [doomed_ids] if isinstance(doomed_ids, Select) else chunked(doomed_ids)
    for batch in batches:
        await session.execute(
            update(CodeNode)
            .where(
                CodeNode.repository_id == repository_id,
                CodeNode.parent_id.in_(batch),
            )
            .values(parent_id=None)
            .execution_options(synchronize_session="fetch")
        )


def _refresh_method_parents(
    *,
    repository_nodes: list[CodeNode],
    nodes_by_qualified_name: dict[str, CodeNode],
) -> None:
    for node in repository_nodes:
        if node.node_type is not CodeNodeType.METHOD:
            continue
        parent_qualified_name, _, _ = node.qualified_name.rpartition(".")
        parent_node = nodes_by_qualified_name.get(parent_qualified_name)
        parent_id = parent_node.id if parent_node is not None else None
        # A same-value set still dirties the row, and every later flush
        # walks every dirty method in the repo.
        if node.parent_id != parent_id:
            node.parent_id = parent_id


def _is_go_builtin(name: str) -> bool:
    return name in {
        "append",
        "cap",
        "clear",
        "close",
        "complex",
        "copy",
        "delete",
        "imag",
        "len",
        "make",
        "max",
        "min",
        "new",
        "panic",
        "print",
        "println",
        "real",
        "recover",
    }


def _to_model_node_type(node_type: GraphNodeType) -> CodeNodeType:
    return CodeNodeType(node_type.value)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
