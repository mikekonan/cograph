"""Per-page dispatcher: routes `(name, input) -> dict` from the agent loop.

One `AgentDispatcher` is instantiated PER WIKI PAGE inside `_agent_write_one`
(see `pipeline.py`). It owns:

  - the immutable `AgentToolContext` (services, repository_id, checkout_fs)
  - per-page mutable state — `captured_markdown` (set by `write_page`),
    `tools_called` counts, `files_read` set, last error
  - the dispatch table mapping tool names to handlers

The agent loop (`StructuredCompletionProvider.complete_with_tools`) calls
`dispatcher.dispatch(name, input)` for every tool_use block. We open a
fresh `AsyncSession` per call — long-running pages (8–15 turns) must not
pin a single connection in the pool.

Errors raised by handlers are captured and returned as a JSON envelope to
the model (`{"error": "ClassName: msg"}`). The agent decides whether to
retry, swap tools, or give up. We never propagate exceptions from a single
tool failure into the loop — the cost of a wasted turn is far smaller
than the cost of a crashed page.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.wiki.agent_tools import (
    AgentToolContext,
    FindByNameInput,
    GetNeighborsInput,
    GrepInput,
    ListByFileInput,
    ListChildrenInput,
    ListFilesInput,
    ReadFileInput,
    ReadNodeByQnInput,
    SearchCodeInput,
    SearchDocsInput,
    WritePageInput,
    find_by_name,
    get_neighbors,
    grep_tool,
    list_by_file,
    list_children,
    list_files_tool,
    read_file_tool,
    read_node_by_qn,
    search_code,
    search_docs,
)
from backend.app.wiki.evidence_ledger import (
    VerifiedEvidenceLedger,
    extract_evidence,
)
from backend.app.wiki.llm_client import ToolDefinition

logger = logging.getLogger(__name__)


_TOOL_TIMEOUT_SECONDS = 10.0


# A handler is `(ctx, session, payload) -> dict`. The dispatcher wraps
# every call in a timeout + error envelope, so handlers can raise freely.
_Handler = Callable[
    [AgentToolContext, AsyncSession, dict[str, Any]],
    Awaitable[dict[str, Any]],
]


# Tool name → (handler, input schema, description). Description is what
# the provider stamps onto the tool surface in the system prompt; keep
# it crisp — the LLM reads this to pick the right tool. Names mirror
# the schema in `agent_tools.py` so the writer prompt and the dispatcher
# stay in lockstep.
_TOOL_REGISTRY: dict[str, tuple[_Handler, type, str]] = {
    "read_node_by_qn": (
        read_node_by_qn,
        ReadNodeByQnInput,
        "Look up one code node by its fully qualified name. Returns id, "
        "file_path, line range, signature, docstring, snippet, and parent_id. "
        "Use this when you already know the QN — it's exact and cheap.",
    ),
    "find_by_name": (
        find_by_name,
        FindByNameInput,
        "Fuzzy symbol search by short name (e.g. `Validate`, `Run`). "
        "Returns ranked candidates with their qualified_names and locations. "
        "Use this when you have a name but not the QN.",
    ),
    "list_children": (
        list_children,
        ListChildrenInput,
        "List the direct child nodes (fields and methods) of a struct, "
        "class, or interface. Use this when documenting a type — the "
        "result is the table you'll render in the page's API section.",
    ),
    "list_by_file": (
        list_by_file,
        ListByFileInput,
        "Enumerate every code node in a single file. Use after `grep` or "
        "`read_file` to discover which symbols live where.",
    ),
    "get_neighbors": (
        get_neighbors,
        GetNeighborsInput,
        "Walk the call graph from a seed symbol — returns its callers, "
        "callees, and parent containment. Use this when documenting how "
        "a function fits into the surrounding code.",
    ),
    "search_code": (
        search_code,
        SearchCodeInput,
        "Hybrid retrieval (lexical + symbol + embeddings) over the code "
        "corpus. Use natural-language queries — `how does the cache "
        "invalidator handle stale entries`. Returns ranked snippets.",
    ),
    "search_docs": (
        search_docs,
        SearchDocsInput,
        "Lexical search over the in-repo documentation (markdown files). "
        "Use this when the README or docs/*.md probably explains the "
        "concept you're writing about.",
    ),
    "read_file": (
        read_file_tool,
        ReadFileInput,
        "Read a slice of a checked-out file by path. Use after a tool "
        "result points at a file you want to see in full context.",
    ),
    "grep": (
        grep_tool,
        GrepInput,
        "Regex search across the checkout (ripgrep when available). "
        "Use to locate symbols, error messages, or config keys whose name "
        "you can spell exactly.",
    ),
    "list_files": (
        list_files_tool,
        ListFilesInput,
        "List files matching a relative glob (`pkg/auth/**/*.go`). "
        "Use to scope your understanding before diving into a directory.",
    ),
}


@dataclass(slots=True)
class AgentDispatcher:
    """Per-page dispatcher.

    Hand it the tool context and the session factory; pass `dispatch` as
    the `tool_dispatch` argument to `complete_with_tools`. After the loop
    returns, read `captured_markdown` for the page body the agent shipped
    (or `None` if it never called `write_page`).
    """

    ctx: AgentToolContext
    session_factory: Callable[[], AsyncIterator[AsyncSession]]
    captured_markdown: str | None = None
    tools_called: dict[str, int] = field(default_factory=dict)
    files_read: set[str] = field(default_factory=set)
    last_error: str | None = None
    # Per-page write-only log of every successful tool result. T3's
    # citation gate and T4's coverage gate read this — citations / answer
    # markers that don't reference one of these records are stripped.
    ledger: VerifiedEvidenceLedger = field(default_factory=VerifiedEvidenceLedger)

    @classmethod
    def tool_definitions(cls) -> list[ToolDefinition]:
        """Convert the dispatch table into the schema list the LLM sees."""
        defs: list[ToolDefinition] = []
        for name, (_handler, schema, description) in _TOOL_REGISTRY.items():
            defs.append(
                ToolDefinition(
                    name=name,
                    description=description,
                    input_schema=schema.model_json_schema(),
                )
            )
        defs.append(
            ToolDefinition(
                name="write_page",
                description=(
                    "Ship the completed wiki page. Calling this tool ENDS "
                    "the agent loop — the markdown you pass is the page "
                    "verbatim, no further edits possible. Call this only "
                    "AFTER you've gathered, thought, and written the page."
                ),
                input_schema=WritePageInput.model_json_schema(),
            )
        )
        return defs

    async def dispatch(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Route one tool call. Always returns a JSON-serialisable dict.

        On any handler error, returns `{"error": "ClassName: msg"}`. The
        agent's prompt knows to inspect this and either retry with
        different input or fall back to another tool.
        """
        self.tools_called[name] = self.tools_called.get(name, 0) + 1

        if name == "write_page":
            return self._handle_write_page(payload)

        registered = _TOOL_REGISTRY.get(name)
        if registered is None:
            self.last_error = f"unknown_tool:{name}"
            return {"error": f"unknown tool: {name!r}"}
        handler, schema, _description = registered
        try:
            schema.model_validate(payload)
        except Exception as exc:
            self.last_error = f"invalid_input:{name}:{exc}"
            return {"error": f"invalid input for {name}: {exc}"}

        try:
            async with self._session() as session:
                result = await asyncio.wait_for(
                    handler(self.ctx, session, payload),
                    timeout=_TOOL_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            self.last_error = f"timeout:{name}"
            return {
                "error": (
                    f"{name} timed out after {_TOOL_TIMEOUT_SECONDS}s — "
                    "try a narrower query."
                )
            }
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}:{exc}"
            logger.warning("agent tool %s failed: %s", name, exc)
            return {"error": f"{type(exc).__name__}: {exc}"}

        # Side-channel telemetry. Read paths come back as `path` from
        # checkout-fs tools; the agent prompt cites these so the chip
        # surface in the FE accurately reflects what the agent used.
        if isinstance(result, dict):
            path = result.get("path")
            if isinstance(path, str) and path:
                self.files_read.add(path)
            # T2: extract evidence from the tool result and append to
            # the per-page ledger so T3 has a verified-by-tool set when
            # the citation gate runs.
            for record in extract_evidence(name, payload, result):
                self.ledger.record(record)
        return result

    def _handle_write_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Capture markdown — terminal tool.

        Returns an ack dict so the model has *something* to read; the
        loop typically end-turns on the next assistant response.
        """
        try:
            args = WritePageInput.model_validate(payload)
        except Exception as exc:
            self.last_error = f"invalid_input:write_page:{exc}"
            return {"error": f"invalid input for write_page: {exc}"}
        markdown = args.markdown.strip()
        if not markdown:
            return {
                "error": (
                    "write_page received empty markdown — call again with "
                    "the page body."
                )
            }
        self.captured_markdown = markdown
        return {
            "ok": True,
            "received_chars": len(markdown),
            "message": (
                "Page captured. End your turn now — no further tool calls are needed."
            ),
        }

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """Open a fresh session for the duration of one tool call.

        `session_factory` is normally `session_manager.session()` (an
        async context manager). Tests sometimes pass a function that
        returns a contextmanager directly, so we accept both via duck
        typing — anything that's an async context manager flows through.
        """
        scoped = self.session_factory()
        if hasattr(scoped, "__aenter__"):
            async with scoped as session:
                yield session
            return
        # Fallback: caller passed an awaitable that yields a session
        # without context-manager semantics. Best effort — close on exit.
        session = await scoped  # type: ignore[misc]
        try:
            yield session
        finally:
            close = getattr(session, "close", None)
            if close is not None:
                await close()


__all__ = ["AgentDispatcher"]
