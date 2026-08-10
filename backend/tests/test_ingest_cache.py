"""Unit tests for GraphIngestCache invalidation.

The cache mirrors every CodeNode in the repo for one ingest run and is
mutated in place by each `persist_graph` call. If `remove()` or
`rename()` leak stale references into any of the three internal maps,
a later call can pick up a deleted-or-renamed node and queue an UPDATE
against a row that no longer exists in its prior shape — surfacing as
`StaleDataError: expected to update 1 row(s); 0 were matched`.

These tests pin the invariants of the cache's public mutators so the
class can't silently lose them in a future refactor.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from backend.app.graph.ingest_cache import GraphIngestCache
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType


def _node(
    *,
    qualified_name: str,
    file_path: str = "x.py",
    node_type: CodeNodeType = CodeNodeType.FUNCTION,
    node_id: UUID | None = None,
) -> CodeNode:
    return CodeNode(
        id=node_id or uuid4(),
        repository_id=uuid4(),
        file_path=file_path,
        qualified_name=qualified_name,
        symbol_key=qualified_name,
        node_type=node_type,
        name=qualified_name.split(".")[-1],
        language="python",
        start_line=1,
        end_line=2,
        start_byte=0,
        end_byte=10,
        content="pass",
    )


def test_remove_clears_node_from_every_internal_map() -> None:
    cache = GraphIngestCache()
    module = _node(
        qualified_name="pkg.mod", node_type=CodeNodeType.MODULE, file_path="pkg/mod.py"
    )
    cache.add(module)

    assert cache.node_by_id[module.id] is module
    assert cache.nodes_by_qn["pkg.mod"] is module
    assert cache.module_nodes_by_file_path["pkg/mod.py"] is module

    cache.remove(module)

    assert module.id not in cache.node_by_id
    assert "pkg.mod" not in cache.nodes_by_qn
    assert "pkg/mod.py" not in cache.module_nodes_by_file_path
    assert module not in cache.repository_nodes()


def test_remove_of_non_module_does_not_touch_module_map() -> None:
    cache = GraphIngestCache()
    module = _node(
        qualified_name="pkg.mod", node_type=CodeNodeType.MODULE, file_path="pkg/mod.py"
    )
    func = _node(
        qualified_name="pkg.mod.foo",
        node_type=CodeNodeType.FUNCTION,
        file_path="pkg/mod.py",
    )
    cache.add(module)
    cache.add(func)

    cache.remove(func)

    assert cache.module_nodes_by_file_path["pkg/mod.py"] is module
    assert "pkg.mod" in cache.nodes_by_qn
    assert "pkg.mod.foo" not in cache.nodes_by_qn


def test_remove_is_identity_keyed_not_qn_keyed() -> None:
    """If a different node now occupies the same QN slot, remove() must
    not delete *that* node. The cache must compare by `is` so a
    rename-then-remove of an old detached object is a no-op."""
    cache = GraphIngestCache()
    original = _node(qualified_name="pkg.foo")
    replacement = _node(qualified_name="pkg.foo")
    cache.add(original)
    # Simulate: replacement overwrites the slot (e.g. via add() of a new node).
    cache.add(replacement)
    assert cache.nodes_by_qn["pkg.foo"] is replacement

    cache.remove(original)

    # `replacement` survives — its slot is untouched.
    assert cache.nodes_by_qn["pkg.foo"] is replacement
    assert replacement.id in cache.node_by_id


def test_rename_moves_node_and_clears_old_slot() -> None:
    cache = GraphIngestCache()
    node = _node(qualified_name="pkg.old_name")
    cache.add(node)

    node.qualified_name = "pkg.new_name"
    cache.rename(node, "pkg.old_name")

    assert "pkg.old_name" not in cache.nodes_by_qn
    assert cache.nodes_by_qn["pkg.new_name"] is node


def test_rename_no_op_when_old_and_new_match() -> None:
    cache = GraphIngestCache()
    node = _node(qualified_name="pkg.foo")
    cache.add(node)

    cache.rename(node, "pkg.foo")

    assert cache.nodes_by_qn["pkg.foo"] is node


def test_rename_preserves_unrelated_slot_under_old_qn() -> None:
    """If the old QN was already reassigned to another node by a prior
    add, rename() must NOT clear that slot. Identity comparison again."""
    cache = GraphIngestCache()
    renamed = _node(qualified_name="pkg.target")
    other = _node(qualified_name="pkg.old_name")
    cache.add(renamed)
    cache.add(other)

    renamed.qualified_name = "pkg.new"
    cache.rename(renamed, "pkg.old_name")

    # `other` still owns pkg.old_name — its node identity isn't `renamed`.
    assert cache.nodes_by_qn["pkg.old_name"] is other
    assert cache.nodes_by_qn["pkg.new"] is renamed


def test_repository_nodes_omits_removed() -> None:
    cache = GraphIngestCache()
    n1 = _node(qualified_name="a")
    n2 = _node(qualified_name="b")
    cache.add(n1)
    cache.add(n2)

    cache.remove(n1)

    nodes = cache.repository_nodes()
    assert n2 in nodes
    assert n1 not in nodes
    assert len(nodes) == 1


def test_remove_then_re_add_round_trip() -> None:
    cache = GraphIngestCache()
    module = _node(
        qualified_name="pkg.mod", node_type=CodeNodeType.MODULE, file_path="pkg/mod.py"
    )
    cache.add(module)
    cache.remove(module)
    cache.add(module)

    assert cache.node_by_id[module.id] is module
    assert cache.nodes_by_qn["pkg.mod"] is module
    assert cache.module_nodes_by_file_path["pkg/mod.py"] is module
