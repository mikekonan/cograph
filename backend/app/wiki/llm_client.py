"""Block-aware LLM client supporting prompt caching and structured outputs.

Why a new client when `backend.app.llm.completion.CompletionProvider` exists:
the existing protocol is `complete(prompt: str) -> str` — a flat string, no way
to mark cache breakpoints, no schema. This module's `StructuredCompletionProvider`
adds:
    - block-based input (system + cached repo-context + fresh user)
    - optional Pydantic schema for JSON-mode parsing
    - multi-turn tool-use loop for the wiki agent writer
    - retries with the same exponential-jitter policy as `OpenAICompletionProvider`

The existing `CompletionProvider` stays unchanged for `summary_generator` and
other callers; this client is dedicated to wiki generation.

Cograph is OpenAI-compatible only — provider config (`api_url`, `api_key`,
`chat_model`) is stored in the `llm_providers` Postgres table and resolved at
worker boot via `resolve_runtime_provider_assignments`. The provider class
below talks to any OpenAI Chat Completions endpoint (api.openai.com,
self-hosted vLLM, Azure OpenAI, etc.) and uses function-calling for the
agent loop.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from backend.app.llm.usage import LlmUsageTally

T = TypeVar("T", bound=BaseModel)

_TOOL_RESULT_COMPACT_CHAR_CAP = 4_000


class StructuredCompletionError(RuntimeError):
    """Raised when the LLM call fails after retries or returns unparseable JSON."""


@dataclass(slots=True, frozen=True)
class CacheBlock:
    """One block of prompt input.

    `cacheable=True` is a hint that this block should sit at the front of
    the user message so OpenAI's implicit prefix caching can hit it.
    Block ordering is preserved; cacheable blocks should come first.
    """

    text: str
    cacheable: bool = False


@dataclass(slots=True, frozen=True)
class ToolDefinition:
    """One tool the agent can invoke during a `complete_with_tools` loop.

    `input_schema` is a JSON Schema describing the tool's parameters —
    typically derived from a Pydantic model via `Model.model_json_schema()`.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(slots=True)
class ToolUseResult:
    """Telemetry-rich return value of a tool-use loop.

    `final_text` is the concatenation of every text block the model
    produced across turns (newline-joined). For agents that capture their
    output via a terminal tool (e.g. `write_page`), the dispatcher's own
    state — not this field — is the canonical artefact.

    `stop_reason` is one of:
      - `end_turn`           — the model decided to stop normally
      - `max_tokens`         — the per-turn token cap was hit (model was cut off)
      - `tool_use`           — only seen when the loop ran exactly one turn and
                               the dispatcher returned but the next request
                               was never issued (should not happen in practice)
      - `budget_exhausted`   — the loop hit `max_turns` without seeing
                               `end_turn`; non-fatal, the caller decides what
                               to do with whatever final_text we have
    """

    final_text: str = ""
    turns_used: int = 0
    tools_called: dict[str, int] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    stop_reason: str = "end_turn"


# Tool dispatcher signature. Returns a JSON-serialisable dict.
ToolDispatcher = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@runtime_checkable
class StructuredCompletionProvider(Protocol):
    """Block-aware structured-output completion."""

    @property
    def model(self) -> str: ...

    async def complete_text(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str: ...

    async def complete_json(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        schema: type[T],
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> T: ...

    async def complete_with_tools(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        tools: list[ToolDefinition],
        tool_dispatch: ToolDispatcher,
        max_turns: int = 20,
        soft_turn_budget: int = 12,
        max_tokens_per_turn: int = 4096,
        temperature: float = 0.0,
        max_input_chars: int | None = 450_000,
    ) -> ToolUseResult: ...


def _approx_tokens(text: str) -> int:
    """Deterministic synthetic token count for the fake provider (~4 chars
    per token, floored at 1 so any non-trivial turn registers usage)."""
    return max(1, len(text) // 4)


@dataclass(slots=True, frozen=True)
class FakeAssistantTurn:
    """One turn the `FakeStructuredProvider` will play during a tool-use loop.

    `text` is the optional assistant text content for the turn. `tool_uses`
    is a list of (name, input) — each one models one tool call the assistant
    asks the dispatcher to run. When `tool_uses` is empty, the loop
    terminates with `stop_reason='end_turn'`; when it's non-empty, the
    loop dispatches each tool, records the result, and consumes the next
    queued turn.

    `tokens_in`/`tokens_out` are the synthetic usage this turn reports;
    `None` derives them from the prompt/output text lengths so cost
    accounting tests see non-zero, deterministic numbers without every
    queue site having to invent counts.
    """

    text: str = ""
    tool_uses: tuple[tuple[str, dict[str, Any]], ...] = ()
    tokens_in: int | None = None
    tokens_out: int | None = None


class FakeStructuredProvider:
    """Deterministic test fake.

    Stores a queue of canned responses; each call pops the next one. JSON calls
    parse the popped string against the provided schema, exposing schema bugs
    in tests. `complete_with_tools` consumes a separate queue of
    `FakeAssistantTurn` entries — each entry is one round-trip in the
    multi-turn loop.
    """

    def __init__(
        self,
        *,
        model: str = "fake-structured-v1",
        usage_tally: LlmUsageTally | None = None,
    ) -> None:
        self._model = model
        self._tally = usage_tally
        self._responses: list[str] = []
        self._tool_turns: list[FakeAssistantTurn] = []
        self.calls: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return self._model

    def queue(self, response: str) -> None:
        self._responses.append(response)

    def queue_tool_turn(
        self,
        *,
        text: str = "",
        tool_uses: list[tuple[str, dict[str, Any]]] | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> None:
        self._tool_turns.append(
            FakeAssistantTurn(
                text=text,
                tool_uses=tuple(tool_uses or ()),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        )

    async def complete_text(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        if not self._responses:
            raise RuntimeError("FakeStructuredProvider queue is empty")
        self.calls.append(
            {
                "system": system,
                "blocks": [(b.text, b.cacheable) for b in blocks],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        response = self._responses.pop(0)
        if self._tally is not None:
            self._tally.record(
                model=self._model,
                tokens_in=_approx_tokens(system + "".join(b.text for b in blocks)),
                tokens_out=_approx_tokens(response),
            )
        return response

    async def complete_json(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        schema: type[T],
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> T:
        text = await self.complete_text(
            system=system,
            blocks=blocks,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return schema.model_validate_json(text)

    async def complete_with_tools(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        tools: list[ToolDefinition],
        tool_dispatch: ToolDispatcher,
        max_turns: int = 20,
        soft_turn_budget: int = 12,
        max_tokens_per_turn: int = 4096,
        temperature: float = 0.0,
        max_input_chars: int | None = 450_000,
    ) -> ToolUseResult:
        """Replay the queued `FakeAssistantTurn`s against the dispatcher.

        Each queued turn corresponds to one assistant response. The loop
        dispatches every tool_use in the turn (recording per-tool counts
        and dispatch outputs in `self.tool_calls`), then advances to the
        next turn. Termination on:
          - first turn with empty `tool_uses` → `stop_reason='end_turn'`
          - queue exhausted before that      → `stop_reason='budget_exhausted'`
          - `max_turns` reached              → `stop_reason='budget_exhausted'`

        Tools available to the agent are recorded for assertion via
        `self.tool_calls`.
        """
        tools_called: dict[str, int] = {}
        final_parts: list[str] = []
        turns = 0
        tokens_in = 0
        tokens_out = 0
        stop_reason = "end_turn"
        # The full block prefix is re-sent on every turn, mirroring how
        # the real provider's prompt grows; per-turn synthetic input
        # defaults to that prefix size.
        prompt_chars = system + "".join(b.text for b in blocks)
        # Snapshot the cache anchors so tests can assert byte-equality
        # (the real provider asserts this internally).
        anchor_system = system
        anchor_first_block = blocks[0].text if blocks else ""

        while turns < max_turns:
            if not self._tool_turns:
                stop_reason = "budget_exhausted"
                break
            turns += 1
            turn = self._tool_turns.pop(0)
            if anchor_system != system or (
                blocks and anchor_first_block != blocks[0].text
            ):
                raise StructuredCompletionError(
                    "FakeStructuredProvider: cache anchor mutated mid-loop"
                )
            tokens_in += (
                turn.tokens_in
                if turn.tokens_in is not None
                else _approx_tokens(prompt_chars)
            )
            tokens_out += (
                turn.tokens_out
                if turn.tokens_out is not None
                else _approx_tokens(turn.text)
                + sum(_approx_tokens(json.dumps(p)) for _, p in turn.tool_uses)
            )
            if turn.text:
                final_parts.append(turn.text)
            if not turn.tool_uses:
                stop_reason = "end_turn"
                break
            for name, payload in turn.tool_uses:
                tools_called[name] = tools_called.get(name, 0) + 1
                try:
                    result = await tool_dispatch(name, dict(payload))
                except Exception as exc:
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                self.tool_calls.append(
                    {"name": name, "input": dict(payload), "result": result}
                )
        else:
            stop_reason = "budget_exhausted"

        if self._tally is not None and turns:
            self._tally.record(
                model=self._model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                calls=turns,
            )
        return ToolUseResult(
            final_text="\n".join(p for p in final_parts if p),
            turns_used=turns,
            tools_called=tools_called,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            stop_reason=stop_reason,
        )


def _strip_json_fences(text: str) -> str:
    """Tolerate models that wrap JSON in ```json fences despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # remove first ``` line (with optional language tag)
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


# GPT-5.x and o-series reasoning models reject `max_tokens`; everything older
# still expects the legacy field. Same families also lock `temperature` to
# the default (1) and reject any explicit value. Mirrors the gates in
# `llm/completion.py`.
_MAX_COMPLETION_TOKENS_MODEL_RE = re.compile(r"^(gpt-[5-9]|o[1-9])", re.IGNORECASE)
_LOCKED_TEMPERATURE_MODEL_RE = re.compile(r"^(gpt-[5-9]|o[1-9])", re.IGNORECASE)


def _uses_max_completion_tokens(model: str) -> bool:
    return bool(_MAX_COMPLETION_TOKENS_MODEL_RE.match(model or ""))


def _supports_temperature(model: str) -> bool:
    return not _LOCKED_TEMPERATURE_MODEL_RE.match(model or "")


class OpenAICompatibleStructuredProvider:
    """OpenAI-compatible Chat Completions wrapper for structured outputs.

    Concatenates `CacheBlock`s into a single user message — block boundaries
    are dropped because OpenAI-compatible APIs don't expose per-block cache
    breakpoints. Implicit prompt caching kicks in on supporting providers
    (gpt-4.1, gpt-5.x, certain self-hosted servers) when the prefix is
    repeated; we don't have to do anything special for that.

    `complete_json` uses `response_format={"type": "json_object"}`, the JSON
    mode supported by the widest range of OpenAI-compatible backends.
    """

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        max_attempts: int = 5,
        wait_initial: float = 1.0,
        wait_max: float = 30.0,
        request_timeout_seconds: float = 120.0,
        connect_timeout_seconds: float = 10.0,
        usage_tally: LlmUsageTally | None = None,
    ) -> None:
        import httpx
        from openai import AsyncOpenAI

        # Split connect/read so a slow upstream that completes the TCP
        # handshake but stalls mid-stream still times out — the prior
        # scalar `timeout=` collapsed both phases to the same budget,
        # which masked connection-pool exhaustion failure modes.
        self._client = AsyncOpenAI(
            base_url=api_url,
            api_key=api_key,
            timeout=httpx.Timeout(
                connect=connect_timeout_seconds,
                read=request_timeout_seconds,
                write=request_timeout_seconds,
                pool=10.0,
            ),
        )
        self._model = model
        self._max_attempts = max_attempts
        self._wait_initial = wait_initial
        self._wait_max = wait_max
        self._tally = usage_tally

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def _join_blocks(blocks: list[CacheBlock]) -> str:
        return "\n\n".join(block.text for block in blocks if block.text)

    def _record_usage(self, resp: Any) -> None:
        if self._tally is None:
            return
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        self._tally.record(
            model=self._model,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
        )

    async def _create(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> str:
        import openai
        from tenacity import (
            AsyncRetrying,
            RetryError,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential_jitter,
        )

        retryable = (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        )

        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self._join_blocks(blocks)},
            ],
        }
        if _supports_temperature(self._model):
            request_kwargs["temperature"] = temperature
        if _uses_max_completion_tokens(self._model):
            request_kwargs["max_completion_tokens"] = max_tokens
        else:
            request_kwargs["max_tokens"] = max_tokens
        if json_mode:
            request_kwargs["response_format"] = {"type": "json_object"}

        resp = None
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(retryable),
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential_jitter(
                    initial=self._wait_initial, max=self._wait_max
                ),
                reraise=False,
            ):
                with attempt:
                    resp = await self._client.chat.completions.create(**request_kwargs)
        except RetryError as exc:
            raise StructuredCompletionError(
                f"OpenAICompatibleStructuredProvider exhausted {self._max_attempts} attempts"
            ) from exc
        except Exception as exc:
            if _is_context_length_exceeded(exc):
                raise StructuredCompletionError(
                    "LLM input exceeded the model context window"
                ) from exc
            raise

        if resp is None:
            raise StructuredCompletionError(
                "retry budget exhausted with no successful attempt"
            )
        self._record_usage(resp)
        return resp.choices[0].message.content or ""

    async def complete_text(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        return await self._create(
            system=system,
            blocks=blocks,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=False,
        )

    async def complete_json(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        schema: type[T],
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> T:
        text = await self._create(
            system=system,
            blocks=blocks,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=True,
        )
        candidate = _strip_json_fences(text)
        try:
            return schema.model_validate_json(candidate)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise StructuredCompletionError(
                f"failed to parse {schema.__name__} from LLM output: {exc}\n"
                f"---\n{candidate[:1000]}\n---"
            ) from exc

    async def complete_with_tools(
        self,
        *,
        system: str,
        blocks: list[CacheBlock],
        tools: list[ToolDefinition],
        tool_dispatch: ToolDispatcher,
        max_turns: int = 20,
        soft_turn_budget: int = 12,
        max_tokens_per_turn: int = 4096,
        temperature: float = 0.0,
        max_input_chars: int | None = 450_000,
    ) -> ToolUseResult:
        """OpenAI Chat Completions function-calling loop.

        Concatenates `blocks` into one user message that becomes the prefix
        for every turn. Implicit prefix caching on gpt-4.1 / gpt-5.x kicks
        in as long as the system + initial user message stay byte-identical
        across turns (we assert that internally). Each turn sends
        `tools=[...]` and `tool_choice="auto"`; on
        `finish_reason == "tool_calls"` every entry in `message.tool_calls`
        is dispatched and replied to with one `role:"tool"` message before
        re-requesting. Loop exits on `finish_reason == "stop"` (model is
        done) or on `max_turns` (`stop_reason='budget_exhausted'`).

        `cache_creation_tokens` is always 0 — OpenAI's `usage` payload
        only exposes `prompt_tokens_details.cached_tokens` (read), so we
        record reads only.
        """
        import openai
        from tenacity import (
            AsyncRetrying,
            RetryError,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential_jitter,
        )

        retryable = (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        )

        anchor_system = system
        anchor_first_user = self._join_blocks(blocks)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": anchor_first_user},
        ]
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]

        tools_called: dict[str, int] = {}
        last_text_parts: list[str] = []
        tokens_in = 0
        tokens_out = 0
        cache_read = 0
        stop_reason = "end_turn"
        turns = 0
        # Once the accumulated messages exceed the input budget we strip
        # `tools` from the next request and tell the model to produce its
        # final output — issuing more tool calls would only push us over
        # the model's context window. This matches what `soft_turn_budget`
        # does for turn count, but driven by accumulated bytes.
        budget_exceeded = False

        while turns < max_turns:
            turns += 1
            if anchor_system != system or anchor_first_user != self._join_blocks(
                blocks
            ):
                raise StructuredCompletionError(
                    "complete_with_tools: cache anchor mutated mid-loop "
                    "(system or initial user block changed)"
                )

            if (
                not budget_exceeded
                and max_input_chars is not None
                and _measure_messages_chars(messages) > max_input_chars
            ):
                _compact_tool_messages(
                    messages,
                    cap=_TOOL_RESULT_COMPACT_CHAR_CAP,
                )
                if _measure_messages_chars(messages) > max_input_chars:
                    budget_exceeded = True
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Context budget reached — DO NOT call any more "
                                "tools. Produce the final markdown body now using "
                                "the evidence you already have. Cite only the "
                                "qualified names and file paths you have already "
                                "verified via prior tool calls."
                            ),
                        }
                    )

            request_kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
            }
            if _supports_temperature(self._model):
                request_kwargs["temperature"] = temperature
            if budget_exceeded:
                # Drop tools entirely so the next response is plain text.
                pass
            else:
                request_kwargs["tools"] = openai_tools
                request_kwargs["tool_choice"] = "auto"
            if _uses_max_completion_tokens(self._model):
                request_kwargs["max_completion_tokens"] = max_tokens_per_turn
            else:
                request_kwargs["max_tokens"] = max_tokens_per_turn

            resp = None
            try:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type(retryable),
                    stop=stop_after_attempt(self._max_attempts),
                    wait=wait_exponential_jitter(
                        initial=self._wait_initial, max=self._wait_max
                    ),
                    reraise=False,
                ):
                    with attempt:
                        resp = await self._client.chat.completions.create(
                            **request_kwargs
                        )
            except RetryError as exc:
                raise StructuredCompletionError(
                    f"OpenAICompatibleStructuredProvider exhausted {self._max_attempts} attempts"
                ) from exc
            except openai.BadRequestError as exc:
                # 400 with `code: context_length_exceeded` means our
                # accumulated messages array overflowed the model's
                # context window despite the input-budget guard. Bail out
                # of the loop instead of failing the whole repo sync —
                # the caller decides whether the partial output is
                # usable. Other 400s (schema validation, malformed tool
                # args) keep raising so they get surfaced.
                if _is_context_length_exceeded(exc):
                    stop_reason = "budget_exhausted"
                    break
                raise
            except Exception as exc:
                # Some openai/tenacity combinations surface non-retryable
                # 400s from AsyncRetrying.__anext__ instead of the inner
                # create() await. Keep context-window overflow recoverable
                # regardless of that call-stack shape.
                if _is_context_length_exceeded(exc):
                    stop_reason = "budget_exhausted"
                    break
                raise

            if resp is None:
                raise StructuredCompletionError(
                    "retry budget exhausted with no successful attempt"
                )

            usage = getattr(resp, "usage", None)
            if usage is not None:
                tokens_in += int(getattr(usage, "prompt_tokens", 0) or 0)
                tokens_out += int(getattr(usage, "completion_tokens", 0) or 0)
                details = getattr(usage, "prompt_tokens_details", None)
                if details is not None:
                    cache_read += int(getattr(details, "cached_tokens", 0) or 0)
            self._record_usage(resp)

            choice = resp.choices[0]
            message = choice.message
            finish_reason = choice.finish_reason or "stop"
            stop_reason = _normalise_finish_reason(finish_reason)

            content = message.content
            if isinstance(content, str) and content:
                last_text_parts = [content]

            tool_calls = list(getattr(message, "tool_calls", None) or [])
            if not tool_calls:
                break

            messages.append(_assistant_tool_call_message(message))
            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    payload_obj = json.loads(raw_args)
                    if not isinstance(payload_obj, dict):
                        payload_obj = {}
                except json.JSONDecodeError:
                    payload_obj = {}
                tools_called[name] = tools_called.get(name, 0) + 1
                try:
                    raw = await tool_dispatch(name, payload_obj)
                    if not isinstance(raw, dict):
                        raw = {"result": raw}
                    body_str = json.dumps(raw, default=str)
                except Exception as exc:
                    body_str = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": body_str,
                    }
                )

            if turns >= soft_turn_budget and turns < max_turns:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"You've used {turns}/{max_turns} turns. "
                            "Wrap up your investigation now and produce the "
                            "final output."
                        ),
                    }
                )
        else:
            stop_reason = "budget_exhausted"

        return ToolUseResult(
            final_text="\n".join(p for p in last_text_parts if p),
            turns_used=turns,
            tools_called=tools_called,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            cache_creation_tokens=0,
            stop_reason=stop_reason,
        )


def _assistant_tool_call_message(message: Any) -> dict[str, Any]:
    """Turn an OpenAI `ChatCompletionMessage` with tool_calls into a dict.

    The `messages` array we re-send must echo the assistant's tool-call
    request verbatim. We can't pass the typed object directly (the SDK
    rejects mixed dict/typed messages), so flatten the relevant fields.
    """
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in (message.tool_calls or [])
        ],
    }


def _measure_messages_chars(messages: list[dict[str, Any]]) -> int:
    """Approximate the input size of a Chat Completions `messages` array.

    We sum the `content` length of every message plus a small per-tool-call
    overhead so the estimate captures function-call argument blobs too. The
    OpenAI tokenization is denser on code-shaped identifiers than prose, so
    the default 450_000-char budget leaves room under the 272k-token context
    window of GPT-5.4-mini once system prompts, tool definitions, and
    reasoning headroom are included.
    """
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text_value = part.get("text")
                    if isinstance(text_value, str):
                        total += len(text_value)
        for tc in msg.get("tool_calls") or []:
            fn = (tc or {}).get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                total += len(args)
    return total


def _compact_tool_messages(messages: list[dict[str, Any]], *, cap: int) -> None:
    """Shrink already-returned tool payloads before giving up on tools.

    The writer can re-read a file or run a narrower search if it needs
    omitted detail. Keeping tool availability for one more turn usually
    preserves page quality better than forcing an immediate final answer.
    """
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, str) and len(content) > cap:
            msg["content"] = _compact_tool_result_content(content, cap=cap)


def _compact_tool_result_content(content: str, *, cap: int) -> str:
    if len(content) <= cap:
        return content
    return json.dumps(
        {
            "truncated": True,
            "original_chars": len(content),
            "prefix": content[:cap],
            "message": (
                "Tool output was compacted to keep the LLM context within "
                "budget. Re-run the same tool with narrower arguments or "
                "line offsets if omitted detail is needed."
            ),
        }
    )


def _normalise_finish_reason(reason: str) -> str:
    """Map OpenAI `finish_reason` to the `ToolUseResult.stop_reason` vocabulary.

    The pipeline-level callers (e.g. `_agent_write_one`) treat
    `end_turn` as the success signal, `max_tokens` as a soft truncation,
    and `budget_exhausted` as turn-cap reached. `tool_use` is only seen
    if the loop exits with the model still requesting tools — which the
    loop body already handles, but we map the raw OpenAI string for
    completeness.
    """
    if reason == "stop":
        return "end_turn"
    if reason == "length":
        return "max_tokens"
    if reason == "tool_calls":
        return "tool_use"
    return reason


def _is_context_length_exceeded(exc: BaseException) -> bool:
    """Detect OpenAI-compatible context-window errors across SDK variants."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            if str(err.get("code") or "") == "context_length_exceeded":
                return True
            message = str(err.get("message") or "")
            if "Input tokens exceed" in message or "context length" in message:
                return True
    text = str(exc)
    return "context_length_exceeded" in text or "Input tokens exceed" in text
