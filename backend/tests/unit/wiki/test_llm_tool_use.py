"""Tests for `complete_with_tools` — the multi-turn tool-use loop.

Cograph is OpenAI-compatible only — the loop talks to Chat Completions
function-calling. These tests stub the `AsyncOpenAI` client with a tiny
SimpleNamespace-based fake that replays scripted `ChatCompletion`-shaped
responses; we never hit the network. The `FakeStructuredProvider` block
at the bottom exercises the in-process replay path that pipeline tests
rely on.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.wiki.llm_client import (
    CacheBlock,
    FakeAssistantTurn,
    FakeStructuredProvider,
    OpenAICompatibleStructuredProvider,
    StructuredCompletionError,
    ToolDefinition,
    ToolUseResult,
)


# ---------------------------------------------------------------------------
# OpenAI Chat Completions scripted client
# ---------------------------------------------------------------------------


def _tool_call(*, id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _message(
    *,
    content: str | None = None,
    tool_calls: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _choice(message: SimpleNamespace, finish_reason: str) -> SimpleNamespace:
    return SimpleNamespace(message=message, finish_reason=finish_reason)


def _usage(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )


def _response(
    *,
    choices: list[SimpleNamespace],
    usage: SimpleNamespace | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(choices=choices, usage=usage)


class _ScriptedOpenAIClient:
    """Async stand-in for `AsyncOpenAI` — `chat.completions.create` pops responses."""

    def __init__(
        self,
        responses: list[SimpleNamespace],
        capture: list[dict[str, Any]],
    ) -> None:
        self._responses = responses
        self.capture = capture
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        if not self._responses:
            raise AssertionError("scripted OpenAI client queue exhausted")
        resp = self._responses.pop(0)
        self.capture.append(kwargs)
        return resp


def _build_provider(
    responses: Iterable[SimpleNamespace],
    *,
    model: str = "gpt-5.4-mini",
) -> tuple[OpenAICompatibleStructuredProvider, list[dict[str, Any]]]:
    """Construct a provider with a scripted client. Captured calls share state."""
    capture: list[dict[str, Any]] = []
    client = _ScriptedOpenAIClient(list(responses), capture)
    provider = OpenAICompatibleStructuredProvider(
        api_url="https://example.invalid/v1",
        api_key="test-key",
        model=model,
        max_attempts=1,
    )
    provider._client = client  # type: ignore[attr-defined]
    return provider, capture


# ---------------------------------------------------------------------------
# OpenAICompatibleStructuredProvider.complete_with_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_turn_convergence() -> None:
    """Turn 1 issues a tool_call; turn 2 emits text and stops."""
    responses = [
        _response(
            choices=[
                _choice(
                    _message(
                        tool_calls=[
                            _tool_call(
                                id="call_1",
                                name="read_node_by_qn",
                                arguments=json.dumps(
                                    {"qualified_name": "pkg.Foo"}
                                ),
                            )
                        ]
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=_usage(prompt_tokens=120, completion_tokens=20),
        ),
        _response(
            choices=[
                _choice(
                    _message(content="# Page\n\nbody"),
                    finish_reason="stop",
                )
            ],
            usage=_usage(prompt_tokens=180, completion_tokens=8, cached_tokens=120),
        ),
    ]
    provider, capture = _build_provider(responses)

    dispatched: list[tuple[str, dict[str, Any]]] = []

    async def _dispatch(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        dispatched.append((name, payload))
        return {"signature": "func Foo()"}

    result = await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx", cacheable=True)],
        tools=[
            ToolDefinition(
                name="read_node_by_qn",
                description="lookup",
                input_schema={"type": "object"},
            )
        ],
        tool_dispatch=_dispatch,
        max_turns=5,
    )

    assert isinstance(result, ToolUseResult)
    assert result.turns_used == 2
    assert result.tools_called == {"read_node_by_qn": 1}
    assert result.stop_reason == "end_turn"
    assert "# Page" in result.final_text
    assert dispatched == [("read_node_by_qn", {"qualified_name": "pkg.Foo"})]
    assert result.tokens_in == 300  # 120 + 180
    assert result.tokens_out == 28
    assert result.cache_read_tokens == 120
    assert result.cache_creation_tokens == 0
    # Two API roundtrips were made.
    assert len(capture) == 2


@pytest.mark.asyncio
async def test_tools_payload_shape_in_first_request() -> None:
    """Tools land as `{type:'function', function:{name, description, parameters}}`."""
    provider, capture = _build_provider(
        [
            _response(
                choices=[
                    _choice(
                        _message(content="done"),
                        finish_reason="stop",
                    )
                ],
                usage=_usage(),
            )
        ]
    )

    async def _noop(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx", cacheable=True)],
        tools=[
            ToolDefinition(
                name="search_code",
                description="hybrid search",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            ),
            ToolDefinition(
                name="read_file",
                description="read",
                input_schema={"type": "object"},
            ),
        ],
        tool_dispatch=_noop,
    )

    body = capture[0]
    assert body["model"] == "gpt-5.4-mini"
    assert body["tool_choice"] == "auto"
    assert body["temperature"] == 0.0
    # gpt-5.x → max_completion_tokens, not max_tokens.
    assert "max_completion_tokens" in body
    assert "max_tokens" not in body
    tools = body["tools"]
    assert tools[0] == {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "hybrid search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    }
    assert {t["function"]["name"] for t in tools} == {"search_code", "read_file"}
    # First user message should be the joined blocks (single block here).
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1] == {"role": "user", "content": "ctx"}


@pytest.mark.asyncio
async def test_hard_cap_returns_budget_exhausted() -> None:
    """Loop stops at max_turns even if the model keeps requesting tools."""
    # Model NEVER says stop — every response is another tool_use.
    responses = [
        _response(
            choices=[
                _choice(
                    _message(
                        tool_calls=[
                            _tool_call(
                                id=f"call_{i}",
                                name="grep",
                                arguments=json.dumps({"pattern": "x"}),
                            )
                        ]
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=_usage(prompt_tokens=10, completion_tokens=5),
        )
        for i in range(10)
    ]
    provider, _ = _build_provider(responses)

    async def _dispatch(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"matches": []}

    result = await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[
            ToolDefinition(
                name="grep", description="d", input_schema={"type": "object"}
            )
        ],
        tool_dispatch=_dispatch,
        max_turns=3,
        soft_turn_budget=99,  # disable nudge
    )
    assert result.stop_reason == "budget_exhausted"
    assert result.turns_used == 3
    assert result.tools_called == {"grep": 3}


@pytest.mark.asyncio
async def test_dispatcher_exception_returns_error_envelope() -> None:
    """Dispatcher raising — error JSON sent back as tool reply, loop continues."""
    responses = [
        _response(
            choices=[
                _choice(
                    _message(
                        tool_calls=[
                            _tool_call(
                                id="call_x",
                                name="boom",
                                arguments="{}",
                            )
                        ]
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=_usage(),
        ),
        _response(
            choices=[
                _choice(
                    _message(content="recovered"),
                    finish_reason="stop",
                )
            ],
            usage=_usage(),
        ),
    ]
    provider, capture = _build_provider(responses)

    async def _broken(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("nope")

    result = await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[
            ToolDefinition(
                name="boom", description="d", input_schema={"type": "object"}
            )
        ],
        tool_dispatch=_broken,
    )
    assert result.stop_reason == "end_turn"
    # Second API call's messages should contain a tool-reply with the error envelope.
    second_body = capture[1]
    tool_replies = [
        msg for msg in second_body["messages"] if msg.get("role") == "tool"
    ]
    assert tool_replies, "expected at least one role:tool reply on the 2nd turn"
    payload = json.loads(tool_replies[0]["content"])
    assert "error" in payload
    assert "ValueError" in payload["error"]


@pytest.mark.asyncio
async def test_soft_budget_appends_nudge_to_next_user_turn() -> None:
    """Past the soft budget, a 'wrap up' user message is appended before next turn."""
    responses = [
        _response(
            choices=[
                _choice(
                    _message(
                        tool_calls=[
                            _tool_call(
                                id=f"c{i}",
                                name="noop",
                                arguments="{}",
                            )
                        ]
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=_usage(),
        )
        for i in range(5)
    ] + [
        _response(
            choices=[_choice(_message(content="done"), finish_reason="stop")],
            usage=_usage(),
        )
    ]
    provider, capture = _build_provider(responses)

    async def _noop(_n: str, _p: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[
            ToolDefinition(
                name="noop", description="d", input_schema={"type": "object"}
            )
        ],
        tool_dispatch=_noop,
        max_turns=10,
        soft_turn_budget=2,
    )

    # The 4th request (turn 4) must include a nudge user message inserted after
    # the soft budget threshold (>= 2). Look for the "Wrap up" string somewhere
    # in the messages of the request that fired AFTER the soft budget hit.
    nudges_seen = 0
    for body in capture[2:]:
        for msg in body["messages"]:
            if msg.get("role") == "user" and "Wrap up" in str(msg.get("content", "")):
                nudges_seen += 1
                break
    assert nudges_seen >= 1


@pytest.mark.asyncio
async def test_cache_anchor_stable_across_turns() -> None:
    """System + initial user message stay byte-identical for prefix caching."""
    responses = [
        _response(
            choices=[
                _choice(
                    _message(
                        tool_calls=[
                            _tool_call(
                                id="call_1",
                                name="t",
                                arguments="{}",
                            )
                        ]
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=_usage(),
        ),
        _response(
            choices=[_choice(_message(content="done"), finish_reason="stop")],
            usage=_usage(),
        ),
    ]
    provider, capture = _build_provider(responses)

    async def _dispatch(_n: str, _p: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    await provider.complete_with_tools(
        system="SYS",
        blocks=[CacheBlock(text="PREFIX", cacheable=True)],
        tools=[
            ToolDefinition(
                name="t", description="d", input_schema={"type": "object"}
            )
        ],
        tool_dispatch=_dispatch,
    )
    assert capture[0]["messages"][0] == {"role": "system", "content": "SYS"}
    assert capture[0]["messages"][1] == {"role": "user", "content": "PREFIX"}
    # Second turn must keep the same prefix (system + first user).
    assert capture[1]["messages"][0] == {"role": "system", "content": "SYS"}
    assert capture[1]["messages"][1] == {"role": "user", "content": "PREFIX"}


@pytest.mark.asyncio
async def test_anchor_drift_raises() -> None:
    """The byte-equality assertion fires when the blocks list mutates mid-loop."""
    responses = [
        _response(
            choices=[
                _choice(
                    _message(
                        tool_calls=[
                            _tool_call(
                                id="call_1",
                                name="noop",
                                arguments="{}",
                            )
                        ]
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=_usage(),
        ),
        _response(
            choices=[_choice(_message(content="done"), finish_reason="stop")],
            usage=_usage(),
        ),
    ]
    provider, _ = _build_provider(responses)

    blocks = [CacheBlock(text="prefix", cacheable=True)]

    async def _dispatch_mutates(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        # Buggy caller mutates the cached block from inside dispatch — the
        # next loop iteration's anchor check must catch this.
        blocks[0] = CacheBlock(text="MUTATED", cacheable=True)
        return {"ok": True}

    with pytest.raises(StructuredCompletionError, match="cache anchor mutated"):
        await provider.complete_with_tools(
            system="sys",
            blocks=blocks,
            tools=[
                ToolDefinition(
                    name="noop",
                    description="d",
                    input_schema={"type": "object"},
                )
            ],
            tool_dispatch=_dispatch_mutates,
            max_turns=5,
        )


@pytest.mark.asyncio
async def test_legacy_model_uses_max_tokens_field() -> None:
    """gpt-4 / older models use `max_tokens`, not `max_completion_tokens`."""
    provider, capture = _build_provider(
        [
            _response(
                choices=[_choice(_message(content="ok"), finish_reason="stop")],
                usage=_usage(),
            )
        ],
        model="gpt-4o-mini",
    )

    async def _noop(_n: str, _p: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[],
        tool_dispatch=_noop,
    )
    body = capture[0]
    assert "max_tokens" in body
    assert "max_completion_tokens" not in body


@pytest.mark.asyncio
async def test_finish_reason_length_maps_to_max_tokens() -> None:
    """`finish_reason='length'` → `stop_reason='max_tokens'` per the contract."""
    provider, _ = _build_provider(
        [
            _response(
                choices=[
                    _choice(_message(content="truncated"), finish_reason="length")
                ],
                usage=_usage(),
            )
        ]
    )

    async def _noop(_n: str, _p: dict[str, Any]) -> dict[str, Any]:
        return {}

    result = await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[],
        tool_dispatch=_noop,
    )
    assert result.stop_reason == "max_tokens"


# ---------------------------------------------------------------------------
# FakeStructuredProvider tool-use helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_provider_replays_scripted_turns() -> None:
    fake = FakeStructuredProvider()
    fake.queue_tool_turn(
        text="thinking",
        tool_uses=[("read_node_by_qn", {"qualified_name": "pkg.Foo"})],
    )
    fake.queue_tool_turn(text="# Page", tool_uses=[])

    captured: list[dict[str, Any]] = []

    async def _dispatch(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured.append({"name": name, "input": payload})
        return {"signature": "func Foo()"}

    result = await fake.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx", cacheable=True)],
        tools=[
            ToolDefinition(
                name="read_node_by_qn",
                description="d",
                input_schema={"type": "object"},
            )
        ],
        tool_dispatch=_dispatch,
    )
    assert isinstance(result, ToolUseResult)
    assert result.turns_used == 2
    assert result.tools_called == {"read_node_by_qn": 1}
    assert result.stop_reason == "end_turn"
    assert "# Page" in result.final_text
    assert captured == [
        {"name": "read_node_by_qn", "input": {"qualified_name": "pkg.Foo"}}
    ]


@pytest.mark.asyncio
async def test_fake_provider_records_tool_calls_for_assertion() -> None:
    fake = FakeStructuredProvider()
    fake.queue_tool_turn(
        tool_uses=[
            ("search_code", {"query": "Foo"}),
            ("list_children", {"qualified_name": "pkg.Foo"}),
        ]
    )
    fake.queue_tool_turn(text="done")

    async def _dispatch(name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"name": name}

    await fake.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[
            ToolDefinition(
                name="search_code",
                description="d",
                input_schema={"type": "object"},
            ),
            ToolDefinition(
                name="list_children",
                description="d",
                input_schema={"type": "object"},
            ),
        ],
        tool_dispatch=_dispatch,
    )
    names = [c["name"] for c in fake.tool_calls]
    assert names == ["search_code", "list_children"]


@pytest.mark.asyncio
async def test_fake_provider_dispatcher_failure_caught() -> None:
    fake = FakeStructuredProvider()
    fake.queue_tool_turn(tool_uses=[("broken", {})])
    fake.queue_tool_turn(text="ok")

    async def _broken(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("nope")

    result = await fake.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[
            ToolDefinition(
                name="broken", description="d", input_schema={"type": "object"}
            )
        ],
        tool_dispatch=_broken,
    )
    assert result.stop_reason == "end_turn"
    # Error envelope landed in the recorded call instead of crashing the loop.
    assert "error" in fake.tool_calls[0]["result"]


@pytest.mark.asyncio
async def test_fake_assistant_turn_dataclass_round_trip() -> None:
    turn = FakeAssistantTurn(text="x", tool_uses=(("a", {"k": "v"}),))
    assert turn.text == "x"
    assert turn.tool_uses == (("a", {"k": "v"}),)


@pytest.mark.asyncio
async def test_fake_provider_cache_anchor_drift_clean_run() -> None:
    """The fake provider's anchor check passes when the caller behaves."""
    fake = FakeStructuredProvider()
    fake.queue_tool_turn(text="ok")

    blocks = [CacheBlock(text="prefix", cacheable=True)]

    result = await fake.complete_with_tools(
        system="sys",
        blocks=blocks,
        tools=[],
        tool_dispatch=lambda _n, _p: (_ for _ in ()).throw(  # type: ignore[arg-type]
            AssertionError("dispatch must NOT be called when no tool_uses")
        ),
    )
    assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_complete_with_tools_protocol_method_present() -> None:
    """Protocol declares `complete_with_tools` so the agent code can rely on it."""
    from backend.app.wiki.llm_client import StructuredCompletionProvider

    assert hasattr(StructuredCompletionProvider, "complete_with_tools")


@pytest.mark.asyncio
async def test_input_budget_guard_drops_tools_and_forces_final_write() -> None:
    """When accumulated messages exceed `max_input_chars`, the next request
    omits `tools`/`tool_choice` and includes a wrap-up directive — the
    model can't issue more tool calls, it has to produce its final text."""
    big_payload = "x" * 10_000  # each tool reply ~10k chars
    # Turn 1: one tool call that the dispatcher answers with a huge blob.
    # Turn 2: model decides what to do (the request should now have NO tools).
    responses = [
        _response(
            choices=[
                _choice(
                    _message(
                        tool_calls=[
                            _tool_call(
                                id="call_1",
                                name="grep",
                                arguments=json.dumps({"pattern": "x"}),
                            )
                        ]
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=_usage(prompt_tokens=10),
        ),
        _response(
            choices=[
                _choice(
                    _message(content="final markdown body"),
                    finish_reason="stop",
                )
            ],
            usage=_usage(prompt_tokens=20, completion_tokens=5),
        ),
    ]
    provider, capture = _build_provider(responses)

    async def _dispatch(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"matches": [big_payload]}

    result = await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[
            ToolDefinition(
                name="grep", description="d", input_schema={"type": "object"}
            )
        ],
        tool_dispatch=_dispatch,
        max_turns=10,
        soft_turn_budget=99,
        # Tiny budget so the very-first tool reply tips us over and the
        # second request must shed its tools.
        max_input_chars=1_000,
    )

    assert result.stop_reason == "end_turn"
    assert result.final_text == "final markdown body"
    # Second request should NOT have `tools` or `tool_choice` (the guard
    # forces the model to write rather than call more tools).
    second = capture[1]
    assert "tools" not in second
    assert "tool_choice" not in second
    # The wrap-up directive landed in messages so the model knows why.
    last_user = [m for m in second["messages"] if m["role"] == "user"][-1]
    assert "Context budget reached" in last_user["content"]
    assert "DO NOT call any more" in last_user["content"]


@pytest.mark.asyncio
async def test_large_tool_result_is_compacted_without_dropping_tools() -> None:
    """Oversized tool replies are compacted before the loop gives up tools.

    This protects writer quality: after reading a large file/search result,
    the agent still gets another normal tool-capable turn instead of being
    forced to write immediately.
    """
    big_payload = "x" * 50_000
    responses = [
        _response(
            choices=[
                _choice(
                    _message(
                        tool_calls=[
                            _tool_call(
                                id="call_1",
                                name="read_file",
                                arguments=json.dumps({"path": "large.py"}),
                            )
                        ]
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=_usage(prompt_tokens=10),
        ),
        _response(
            choices=[
                _choice(
                    _message(content="final markdown body"),
                    finish_reason="stop",
                )
            ],
            usage=_usage(prompt_tokens=20, completion_tokens=5),
        ),
    ]
    provider, capture = _build_provider(responses)

    async def _dispatch(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"body": big_payload}

    result = await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[
            ToolDefinition(
                name="read_file", description="d", input_schema={"type": "object"}
            )
        ],
        tool_dispatch=_dispatch,
        max_turns=10,
        soft_turn_budget=99,
        max_input_chars=20_000,
    )

    assert result.final_text == "final markdown body"
    second = capture[1]
    assert "tools" in second
    tool_message = [m for m in second["messages"] if m["role"] == "tool"][0]
    assert '"truncated": true' in tool_message["content"]
    assert len(tool_message["content"]) < 5_000


@pytest.mark.asyncio
async def test_context_length_exceeded_breaks_loop_with_partial_output() -> None:
    """A 400 with `code: context_length_exceeded` from the API exits the
    loop with `budget_exhausted` instead of bubbling up — preserving any
    text the model produced earlier rather than failing the whole run."""
    import openai

    # Model produces partial text on turn 1, then makes a tool call that
    # fills the context, then turn 2's request raises 400 from the API.
    captured: list[dict[str, Any]] = []

    class _BoomClient:
        """Sequence: turn 1 ok with tool_call; turn 2 raises 400."""

        def __init__(self) -> None:
            self.calls = 0
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        async def _create(self, **kwargs: Any) -> SimpleNamespace:
            captured.append(kwargs)
            self.calls += 1
            if self.calls == 1:
                return _response(
                    choices=[
                        _choice(
                            _message(
                                content="partial text emitted before tool call",
                                tool_calls=[
                                    _tool_call(
                                        id="c1",
                                        name="grep",
                                        arguments="{}",
                                    )
                                ],
                            ),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=_usage(),
                )
            raise openai.BadRequestError(
                message="Input tokens exceed the configured limit",
                response=SimpleNamespace(
                    status_code=400, headers={}, request=SimpleNamespace()
                ),
                body={
                    "error": {
                        "message": "Input tokens exceed the configured limit",
                        "type": "invalid_request_error",
                        "code": "context_length_exceeded",
                    }
                },
            )

    provider = OpenAICompatibleStructuredProvider(
        api_url="https://example.invalid/v1",
        api_key="test-key",
        model="gpt-5.4-mini",
        max_attempts=1,
    )
    provider._client = _BoomClient()  # type: ignore[attr-defined]

    async def _dispatch(_n: str, _p: dict[str, Any]) -> dict[str, Any]:
        return {"matches": []}

    result = await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[
            ToolDefinition(
                name="grep", description="d", input_schema={"type": "object"}
            )
        ],
        tool_dispatch=_dispatch,
        max_turns=5,
        soft_turn_budget=99,
        max_input_chars=None,  # let the API trip the limit, not the guard
    )

    assert result.stop_reason == "budget_exhausted"
    assert result.final_text == "partial text emitted before tool call"


@pytest.mark.asyncio
async def test_context_length_exceeded_from_tenacity_iterator_is_recoverable() -> None:
    """Provider adapters can surface context-window 400s from the retry
    iterator rather than the inner OpenAI await. The loop must still rescue
    the page instead of crashing the whole repo sync."""

    class _ContextLengthError(Exception):
        body = {
            "error": {
                "message": "Input tokens exceed the configured limit",
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
            }
        }

    class _BoomClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        async def _create(self, **_kwargs: Any) -> SimpleNamespace:
            raise _ContextLengthError()

    provider = OpenAICompatibleStructuredProvider(
        api_url="https://example.invalid/v1",
        api_key="test-key",
        model="gpt-5.4-mini",
        max_attempts=1,
    )
    provider._client = _BoomClient()  # type: ignore[attr-defined]

    async def _dispatch(_n: str, _p: dict[str, Any]) -> dict[str, Any]:
        return {}

    result = await provider.complete_with_tools(
        system="sys",
        blocks=[CacheBlock(text="ctx")],
        tools=[
            ToolDefinition(
                name="grep", description="d", input_schema={"type": "object"}
            )
        ],
        tool_dispatch=_dispatch,
        max_turns=2,
        max_input_chars=None,
    )

    assert result.stop_reason == "budget_exhausted"


@pytest.mark.asyncio
async def test_context_length_exceeded_in_text_call_maps_to_structured_error() -> None:
    """Non-tool structured calls cannot ship partial output, but they should
    raise the pipeline's error type instead of leaking SDK-specific 400s."""

    class _ContextLengthError(Exception):
        body = {
            "error": {
                "message": "Input tokens exceed the configured limit",
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
            }
        }

    class _BoomClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        async def _create(self, **_kwargs: Any) -> SimpleNamespace:
            raise _ContextLengthError()

    provider = OpenAICompatibleStructuredProvider(
        api_url="https://example.invalid/v1",
        api_key="test-key",
        model="gpt-5.4-mini",
        max_attempts=1,
    )
    provider._client = _BoomClient()  # type: ignore[attr-defined]

    with pytest.raises(StructuredCompletionError, match="context window"):
        await provider.complete_text(
            system="sys",
            blocks=[CacheBlock(text="ctx")],
        )


@pytest.mark.asyncio
async def test_other_400_errors_still_raise() -> None:
    """Non-context-length 400s (schema violations, malformed args) keep
    raising — only `context_length_exceeded` is recoverable."""
    import openai

    class _BoomClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )

        async def _create(self, **_kwargs: Any) -> SimpleNamespace:
            raise openai.BadRequestError(
                message="bad request",
                response=SimpleNamespace(
                    status_code=400, headers={}, request=SimpleNamespace()
                ),
                body={
                    "error": {
                        "message": "Tool call schema violation",
                        "type": "invalid_request_error",
                        "code": "invalid_function_arguments",
                    }
                },
            )

    provider = OpenAICompatibleStructuredProvider(
        api_url="https://example.invalid/v1",
        api_key="test-key",
        model="gpt-5.4-mini",
        max_attempts=1,
    )
    provider._client = _BoomClient()  # type: ignore[attr-defined]

    async def _dispatch(_n: str, _p: dict[str, Any]) -> dict[str, Any]:
        return {}

    with pytest.raises(openai.BadRequestError):
        await provider.complete_with_tools(
            system="sys",
            blocks=[CacheBlock(text="ctx")],
            tools=[
                ToolDefinition(
                    name="grep", description="d", input_schema={"type": "object"}
                )
            ],
            tool_dispatch=_dispatch,
            max_turns=2,
            max_input_chars=None,
        )
