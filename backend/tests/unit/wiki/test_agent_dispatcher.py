"""Tests for the agent tool dispatcher and the 11 tool handlers.

Covers the dispatcher's plumbing (unknown tool, invalid input, exception →
error envelope, timeout, write_page capture, tools_called counter) and a
DB-backed end-to-end path for each handler that depends on the code graph.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.enums import CodeNodeType
from backend.app.models.repository import Repository
from backend.app.rag.retriever import RetrievedChunk
from backend.app.wiki.agent_dispatcher import AgentDispatcher
from backend.app.wiki.agent_tools import AgentToolContext
from backend.app.wiki.checkout_fs import CheckoutFs

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test fakes for the retrieval / traversal services
# ---------------------------------------------------------------------------


class _FakeSymbolLookup:
    """Stub `SymbolLookup` — returns a queue of canned `RetrievedChunk` lists."""

    def __init__(self) -> None:
        self.queue: list[list[RetrievedChunk]] = []
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        session: AsyncSession,
        *,
        query_text: str,
        repository_id: UUID,
        top_k: int = 10,
        **_: object,
    ) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "query_text": query_text,
                "repository_id": repository_id,
                "top_k": top_k,
            }
        )
        if self.queue:
            return self.queue.pop(0)
        return []


class _FakeLexical:
    """Stub `LexicalRetriever` — replays canned chunks when queried for repo_docs."""

    def __init__(self) -> None:
        self.queue: list[list[RetrievedChunk]] = []
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        session: AsyncSession,
        *,
        store: str,
        query_text: str,
        repository_id: UUID | None = None,
        top_k: int = 10,
        **_: object,
    ) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "store": store,
                "query_text": query_text,
                "repository_id": repository_id,
                "top_k": top_k,
            }
        )
        if self.queue:
            return self.queue.pop(0)
        return []


class _FakeHybrid:
    """Stub `HybridRetriever`."""

    def __init__(self) -> None:
        self.queue: list[list[RetrievedChunk]] = []
        self.calls: list[dict[str, object]] = []

    async def retrieve(
        self,
        session: AsyncSession,
        *,
        query_text: str,
        query_embedding: list[float],
        repository_id: UUID,
        top_k: int = 10,
        stores: set[str] | None = None,
        **_: object,
    ) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "query_text": query_text,
                "stores": set(stores or set()),
                "top_k": top_k,
                "has_embedding": bool(query_embedding),
            }
        )
        if self.queue:
            return self.queue.pop(0)
        return []


class _FakeTraversal:
    """Stub `GraphTraversalService`."""

    def __init__(self) -> None:
        self.response = None
        self.calls: list[dict[str, object]] = []

    async def traverse(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        node_id: UUID,
        depth: int = 1,
        direction=None,
    ):
        self.calls.append(
            {
                "node_id": node_id,
                "depth": depth,
                "direction": direction,
            }
        )
        return self.response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(session: AsyncSession, name: str = "agent-tools") -> Repository:
    repo = Repository(
        host="example.com",
        git_url=f"https://github.com/test/{name}",
        name=name,
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc",
    )
    session.add(repo)
    await session.flush()
    return repo


async def _add_node(
    session: AsyncSession,
    *,
    repo_id: UUID,
    qn: str,
    name: str | None = None,
    parent_id: UUID | None = None,
    file_path: str = "pkg/x.go",
    start_line: int = 1,
    end_line: int = 10,
    node_type: CodeNodeType = CodeNodeType.STRUCT,
    content: str = "<source>",
    signature: str | None = None,
    doc_comment: str | None = None,
) -> CodeNode:
    node = CodeNode(
        repository_id=repo_id,
        file_path=file_path,
        qualified_name=qn,
        node_type=node_type,
        name=name or qn.rsplit(".", 1)[-1],
        language="go",
        start_line=start_line,
        end_line=end_line,
        content=content,
        content_hash="x" * 64,
        parent_id=parent_id,
        signature=signature,
        doc_comment=doc_comment,
    )
    session.add(node)
    await session.flush()
    return node


def _make_context(
    *,
    repo_id: UUID,
    hybrid=None,
    lexical=None,
    symbol=None,
    traversal=None,
    checkout_fs: CheckoutFs | None = None,
) -> AgentToolContext:
    return AgentToolContext(
        session_factory=lambda: None,  # filled per-dispatcher
        repository_id=repo_id,
        checkout_fs=checkout_fs,
        hybrid=hybrid or _FakeHybrid(),
        lexical=lexical or _FakeLexical(),
        symbol=symbol or _FakeSymbolLookup(),
        traversal=traversal or _FakeTraversal(),
        embedder=None,
    )


def _make_dispatcher(
    ctx: AgentToolContext, db_session: AsyncSession
) -> AgentDispatcher:
    @asynccontextmanager
    async def _factory():
        yield db_session

    return AgentDispatcher(ctx=ctx, session_factory=_factory)


# ---------------------------------------------------------------------------
# Dispatcher plumbing
# ---------------------------------------------------------------------------


async def test_tool_definitions_includes_all_eleven_tools() -> None:
    defs = AgentDispatcher.tool_definitions()
    names = {d.name for d in defs}
    assert names == {
        "read_node_by_qn",
        "find_by_name",
        "list_children",
        "list_by_file",
        "get_neighbors",
        "search_code",
        "search_docs",
        "read_file",
        "grep",
        "list_files",
        "write_page",
    }
    # Each schema must be JSON-serialisable (would be inlined into the
    # provider request payload).
    for d in defs:
        assert isinstance(d.input_schema, dict)
        assert d.input_schema.get("type") == "object"


async def test_dispatch_unknown_tool_returns_error_envelope(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    ctx = _make_context(repo_id=repo.id)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch("not_a_tool", {})
    assert "error" in result
    assert "not_a_tool" in result["error"]
    assert disp.tools_called["not_a_tool"] == 1
    assert disp.last_error is not None
    assert disp.last_error.startswith("unknown_tool:")


async def test_dispatch_invalid_input_returns_error_envelope(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    ctx = _make_context(repo_id=repo.id)
    disp = _make_dispatcher(ctx, db_session)
    # ReadNodeByQnInput requires `qualified_name`; an empty payload fails.
    result = await disp.dispatch("read_node_by_qn", {})
    assert "error" in result
    assert "read_node_by_qn" in result["error"]


async def test_dispatch_handler_exception_caught(
    db_session: AsyncSession,
) -> None:
    """Failing handler returns the error envelope, never raises."""
    repo = await _make_repo(db_session)
    ctx = _make_context(repo_id=repo.id, symbol=_RaisingSymbol())
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch("find_by_name", {"name": "x"})
    assert "error" in result
    assert "kaboom" in result["error"]
    assert disp.last_error is not None


class _RaisingSymbol:
    async def search(self, *_a, **_kw):
        raise RuntimeError("kaboom")


async def test_dispatch_timeout_returns_error_envelope(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A handler that hangs past the timeout produces a timeout error envelope."""
    repo = await _make_repo(db_session)

    class _SlowSymbol:
        async def search(self, *_a, **_kw):
            await asyncio.sleep(60)
            return []

    ctx = _make_context(repo_id=repo.id, symbol=_SlowSymbol())
    disp = _make_dispatcher(ctx, db_session)
    monkeypatch.setattr(
        "backend.app.wiki.agent_dispatcher._TOOL_TIMEOUT_SECONDS", 0.05
    )
    result = await disp.dispatch("find_by_name", {"name": "x"})
    assert "error" in result
    assert "timed out" in result["error"]


async def test_write_page_captures_markdown(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    ctx = _make_context(repo_id=repo.id)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch(
        "write_page", {"markdown": "# Page\nbody here"}
    )
    assert result["ok"] is True
    assert result["received_chars"] == len("# Page\nbody here")
    assert disp.captured_markdown == "# Page\nbody here"


async def test_write_page_rejects_empty_markdown(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    ctx = _make_context(repo_id=repo.id)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch("write_page", {"markdown": "   "})
    assert "error" in result
    assert disp.captured_markdown is None


async def test_files_read_telemetry_collected(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    repo = await _make_repo(db_session)
    (tmp_path / "a.go").write_text("package x\n\nfunc Foo() {}\n")
    fs = CheckoutFs(root=tmp_path)
    ctx = _make_context(repo_id=repo.id, checkout_fs=fs)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch("read_file", {"path": "a.go"})
    assert "body" in result
    assert "a.go" in disp.files_read


# ---------------------------------------------------------------------------
# read_node_by_qn
# ---------------------------------------------------------------------------


async def test_read_node_by_qn_exact_match(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    node = await _add_node(
        db_session,
        repo_id=repo.id,
        qn="pkg.Generator",
        signature="func Generator()",
        doc_comment="Drives codegen.",
    )
    db_session.add(
        CodeNodeSummary(
            code_node_id=node.id,
            repository_id=repo.id,
            summary="Top-level generator.",
            importance=0.9,
            content_hash="x" * 64,
            neighbor_hash="y" * 64,
            model="test-model",
        )
    )
    await db_session.flush()
    ctx = _make_context(repo_id=repo.id)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch(
        "read_node_by_qn", {"qualified_name": "pkg.Generator"}
    )
    assert result["found"] is True
    assert result["qualified_name"] == "pkg.Generator"
    assert result["signature"] == "func Generator()"
    assert result["docstring"] == "Drives codegen."
    assert result["summary"] == "Top-level generator."


async def test_read_node_by_qn_misses_with_fuzzy_candidates(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    symbol = _FakeSymbolLookup()
    symbol.queue.append(
        [
            RetrievedChunk(
                store="code",
                chunk_id=UUID("00000000-0000-0000-0000-000000000001"),
                content="hint",
                score=0.42,
                metadata={
                    "qualified_name": "pkg.GeneratorImpl",
                    "file_path": "pkg/x.go",
                    "start_line": 5,
                },
            )
        ]
    )
    ctx = _make_context(repo_id=repo.id, symbol=symbol)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch(
        "read_node_by_qn", {"qualified_name": "pkg.Generator"}
    )
    assert result["found"] is False
    assert "candidates" in result
    assert result["candidates"][0]["qualified_name"] == "pkg.GeneratorImpl"


# ---------------------------------------------------------------------------
# find_by_name / list_children / list_by_file
# ---------------------------------------------------------------------------


async def test_find_by_name_routes_through_symbol_lookup(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    symbol = _FakeSymbolLookup()
    symbol.queue.append(
        [
            RetrievedChunk(
                store="code",
                chunk_id=UUID("00000000-0000-0000-0000-000000000002"),
                content="x",
                score=0.7,
                metadata={
                    "qualified_name": "auth.Validate",
                    "file_path": "auth/v.go",
                    "start_line": 10,
                    "end_line": 30,
                    "language": "go",
                },
            )
        ]
    )
    ctx = _make_context(repo_id=repo.id, symbol=symbol)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch(
        "find_by_name", {"name": "Validate", "top_k": 5}
    )
    assert result["candidates"][0]["qualified_name"] == "auth.Validate"
    assert symbol.calls[0]["top_k"] == 5


async def test_list_children_returns_struct_fields(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    parent = await _add_node(
        db_session,
        repo_id=repo.id,
        qn="pkg.User",
        node_type=CodeNodeType.STRUCT,
    )
    await _add_node(
        db_session,
        repo_id=repo.id,
        qn="pkg.User.Name",
        parent_id=parent.id,
        node_type=CodeNodeType.ATTRIBUTE,
        start_line=2,
    )
    await _add_node(
        db_session,
        repo_id=repo.id,
        qn="pkg.User.Save",
        parent_id=parent.id,
        node_type=CodeNodeType.METHOD,
        start_line=4,
    )
    ctx = _make_context(repo_id=repo.id)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch(
        "list_children", {"qualified_name": "pkg.User"}
    )
    assert result["found"] is True
    qns = [c["qualified_name"] for c in result["children"]]
    assert qns == ["pkg.User.Name", "pkg.User.Save"]


async def test_list_children_unknown_parent_returns_empty(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    ctx = _make_context(repo_id=repo.id)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch(
        "list_children", {"qualified_name": "pkg.Missing"}
    )
    assert result["found"] is False
    assert result["children"] == []


async def test_list_by_file_returns_nodes_in_path(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_node(
        db_session, repo_id=repo.id, qn="pkg.A", file_path="pkg/a.go"
    )
    await _add_node(
        db_session,
        repo_id=repo.id,
        qn="pkg.B",
        file_path="pkg/a.go",
        start_line=20,
    )
    await _add_node(
        db_session,
        repo_id=repo.id,
        qn="other.C",
        file_path="other/c.go",
    )
    ctx = _make_context(repo_id=repo.id)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch("list_by_file", {"file_path": "pkg/a.go"})
    qns = [n["qualified_name"] for n in result["nodes"]]
    assert qns == ["pkg.A", "pkg.B"]


# ---------------------------------------------------------------------------
# get_neighbors
# ---------------------------------------------------------------------------


async def test_get_neighbors_unknown_seed(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    ctx = _make_context(repo_id=repo.id)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch(
        "get_neighbors", {"qualified_name": "missing.Foo"}
    )
    assert result["found"] is False


async def test_get_neighbors_returns_callers_callees(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    seed = await _add_node(db_session, repo_id=repo.id, qn="pkg.Run")
    caller = await _add_node(
        db_session,
        repo_id=repo.id,
        qn="pkg.Main",
        start_line=5,
        node_type=CodeNodeType.FUNCTION,
    )
    callee = await _add_node(
        db_session,
        repo_id=repo.id,
        qn="pkg.Helper",
        start_line=15,
        node_type=CodeNodeType.FUNCTION,
    )

    class _StubNode:
        def __init__(self, node_id, name, file_path, start, end, ntype):
            self.id = node_id
            self.name = name
            self.node_type = ntype
            self.file_path = file_path
            self.start_line = start
            self.end_line = end
            self.signature = ""

    class _StubEdge:
        def __init__(self, src, tgt, etype):
            self.source = src
            self.target = tgt
            self.type = etype
            self.distance = 1

    class _StubResp:
        def __init__(self, nodes, edges):
            self.nodes = nodes
            self.edges = edges

    nodes = [
        _StubNode(seed.id, "pkg.Run", "x.go", 1, 10, "function"),
        _StubNode(caller.id, "pkg.Main", "x.go", 5, 20, "function"),
        _StubNode(callee.id, "pkg.Helper", "x.go", 15, 25, "function"),
    ]
    edges = [
        _StubEdge(caller.id, seed.id, "calls"),  # caller → seed
        _StubEdge(seed.id, callee.id, "calls"),  # seed → callee
    ]
    traversal = _FakeTraversal()
    traversal.response = _StubResp(nodes, edges)
    ctx = _make_context(repo_id=repo.id, traversal=traversal)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch(
        "get_neighbors",
        {"qualified_name": "pkg.Run", "depth": 1, "direction": "both"},
    )
    assert result["found"] is True
    caller_names = {c["qualified_name"] for c in result["callers"]}
    callee_names = {c["qualified_name"] for c in result["callees"]}
    assert "pkg.Main" in caller_names
    assert "pkg.Helper" in callee_names


# ---------------------------------------------------------------------------
# search_code / search_docs
# ---------------------------------------------------------------------------


async def test_search_code_routes_through_hybrid(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    hybrid = _FakeHybrid()
    hybrid.queue.append(
        [
            RetrievedChunk(
                store="code",
                chunk_id=UUID("00000000-0000-0000-0000-000000000003"),
                content="snippet body",
                score=0.55,
                metadata={
                    "qualified_name": "pkg.A",
                    "file_path": "pkg/a.go",
                    "start_line": 1,
                    "end_line": 10,
                    "language": "go",
                },
            )
        ]
    )
    ctx = _make_context(repo_id=repo.id, hybrid=hybrid)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch(
        "search_code", {"query": "auth flow", "top_k": 5}
    )
    assert result["results"][0]["qualified_name"] == "pkg.A"
    assert hybrid.calls[0]["stores"] == {"code"}


async def test_search_docs_routes_through_lexical_repo_docs(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    lexical = _FakeLexical()
    lexical.queue.append(
        [
            RetrievedChunk(
                store="repo_docs",
                chunk_id=UUID("00000000-0000-0000-0000-000000000004"),
                content="doc body",
                score=0.7,
                metadata={
                    "file_path": "docs/intro.md",
                    "title": "Intro",
                    "heading_path": ["Intro", "Overview"],
                    "chunk_index": 0,
                },
            )
        ]
    )
    ctx = _make_context(repo_id=repo.id, lexical=lexical)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch("search_docs", {"query": "intro"})
    assert result["results"][0]["file_path"] == "docs/intro.md"
    assert lexical.calls[0]["store"] == "repo_docs"


# ---------------------------------------------------------------------------
# Filesystem tools
# ---------------------------------------------------------------------------


async def test_read_file_unavailable_when_no_checkout(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    ctx = _make_context(repo_id=repo.id, checkout_fs=None)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch("read_file", {"path": "any.txt"})
    assert "error" in result
    assert "no checkout" in result["error"]


async def test_grep_routes_through_checkout_fs(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _make_repo(db_session)
    (tmp_path / "x.go").write_text("package main\n\nfunc Run() {}\n")
    # Force the Python fallback so grep is deterministic in CI.
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _: None)
    fs = CheckoutFs(root=tmp_path)
    ctx = _make_context(repo_id=repo.id, checkout_fs=fs)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch("grep", {"pattern": "Run"})
    assert any(m["path"] == "x.go" for m in result["matches"])


async def test_list_files_routes_through_checkout_fs(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    repo = await _make_repo(db_session)
    (tmp_path / "a.go").write_text("x")
    (tmp_path / "b.py").write_text("y")
    fs = CheckoutFs(root=tmp_path)
    ctx = _make_context(repo_id=repo.id, checkout_fs=fs)
    disp = _make_dispatcher(ctx, db_session)
    result = await disp.dispatch("list_files", {"glob": "**/*.go"})
    assert result["matches"] == ["a.go"]
