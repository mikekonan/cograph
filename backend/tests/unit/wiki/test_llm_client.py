"""Tests for `OpenAICompatibleStructuredProvider` request shape and helpers.

Cograph routes all wiki LLM traffic through OpenAI Chat Completions
(per-role secrets + model assignments live in Postgres). These tests
mock the `AsyncOpenAI` client; no network calls.
"""

from __future__ import annotations

import pytest

from backend.app.wiki.llm_client import (
    CacheBlock,
    OpenAICompatibleStructuredProvider,
    _strip_json_fences,
    _supports_temperature,
    _uses_max_completion_tokens,
)
from backend.app.wiki.schemas import RepoOverview


def test_strip_json_fences_handles_plain() -> None:
    assert _strip_json_fences('{"a":1}') == '{"a":1}'


def test_strip_json_fences_handles_fenced() -> None:
    fenced = '```json\n{"a":1}\n```'
    assert _strip_json_fences(fenced) == '{"a":1}'


def test_strip_json_fences_handles_unlabeled_fence() -> None:
    fenced = '```\n{"a":1}\n```'
    assert _strip_json_fences(fenced) == '{"a":1}'


def test_uses_max_completion_tokens_matches_gpt5_and_o_series() -> None:
    assert _uses_max_completion_tokens("gpt-5.4-mini")
    assert _uses_max_completion_tokens("o3-mini")
    assert not _uses_max_completion_tokens("gpt-4o-mini")
    assert not _uses_max_completion_tokens("llama3:70b")


def test_supports_temperature_excludes_reasoning_families() -> None:
    # OpenAI reasoning families lock `temperature` to the default; the
    # provider must omit the field instead of sending an explicit value.
    assert not _supports_temperature("gpt-5.4-mini")
    assert not _supports_temperature("gpt-5.5")
    assert not _supports_temperature("o3-mini")
    assert _supports_temperature("gpt-4o-mini")
    assert _supports_temperature("llama3:70b")


@pytest.mark.asyncio
async def test_openai_compat_complete_text_concatenates_blocks_and_routes_to_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI-compatible provider concatenates blocks and uses chat.completions.create."""
    captured: dict[str, object] = {}

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = type("_M", (), {"content": content})()

    class _Resp:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _ChatCompletions:
        async def create(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return _Resp("hello")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _ChatCompletions()

    class _DummyAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str, timeout: float) -> None:
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["timeout"] = timeout
            self.chat = _Chat()

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _DummyAsyncOpenAI)

    provider = OpenAICompatibleStructuredProvider(
        api_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
    )
    text = await provider.complete_text(
        system="sys",
        blocks=[
            CacheBlock(text="prefix", cacheable=True),
            CacheBlock(text="user", cacheable=False),
        ],
    )

    assert text == "hello"
    assert captured["base_url"] == "https://api.openai.com/v1"
    assert captured["api_key"] == "test-key"
    assert captured["model"] == "gpt-4o-mini"
    assert captured["max_tokens"] == 4096
    assert "max_completion_tokens" not in captured
    messages = captured["messages"]
    assert messages[0] == {"role": "system", "content": "sys"}
    # Blocks are concatenated with a blank line separator — no per-block markers
    assert messages[1] == {"role": "user", "content": "prefix\n\nuser"}
    assert "response_format" not in captured


@pytest.mark.asyncio
async def test_openai_compat_complete_json_uses_json_object_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = type("_M", (), {"content": content})()

    class _Resp:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _ChatCompletions:
        async def create(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return _Resp('{"one_line":"hi","long_description":"there"}')

    class _DummyAsyncOpenAI:
        def __init__(self, **_: object) -> None:
            self.chat = type("_C", (), {"completions": _ChatCompletions()})()

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _DummyAsyncOpenAI)

    provider = OpenAICompatibleStructuredProvider(
        api_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-5.4-mini",
    )
    overview = await provider.complete_json(
        system="sys",
        blocks=[CacheBlock(text="x", cacheable=False)],
        schema=RepoOverview,
    )

    assert overview.one_line == "hi"
    assert captured["response_format"] == {"type": "json_object"}
    # gpt-5.x routes to max_completion_tokens, not max_tokens
    assert captured["max_completion_tokens"] == 4096
    assert "max_tokens" not in captured
