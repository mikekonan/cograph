"""Direct unit tests for the T5 two-pass writer driver.

Pass-1 (outline) drives the agent loop with full tools; pass-2 (prose)
runs `complete_text` over the outline + ledger pack. These tests stub
both LLM calls so we can drive each branch deterministically:

  - happy path → returns body + ok
  - outline LLM error → falls back to None + failed
  - outline JSON unparseable → retries within budget; returns failed
  - outline schema invalid → retries within budget; returns failed
  - prose LLM error → returns None + failed
  - prose returns empty → returns None + failed
"""

from __future__ import annotations

from backend.app.wiki.agent_dispatcher import AgentDispatcher
from backend.app.wiki.evidence_ledger import VerifiedEvidenceLedger
from backend.app.wiki.llm_client import (
    StructuredCompletionError,
    ToolUseResult,
)
from backend.app.wiki.pipeline import (
    WikiGenerationConfig,
    _extract_outline_json,
    _run_two_pass_write,
)
from backend.app.wiki.retrieval import PageBundle
from backend.app.wiki.schemas import (
    EvidenceRecord,
    PageKind,
    PageSpec,
    ReaderQuestion,
    RepoOverview,
)


def _spec() -> PageSpec:
    return PageSpec(
        slug="domain-model",
        title="Domain Model",
        purpose="The Account / Transfer / Ledger entity graph.",
        covers_questions=[ReaderQuestion.PUBLIC_API],
        page_kind=PageKind.DOMAIN_MODEL,
    )


def _dispatcher(*records: EvidenceRecord) -> AgentDispatcher:
    led = VerifiedEvidenceLedger()
    for r in records:
        led.record(r)
    disp = AgentDispatcher.__new__(AgentDispatcher)
    disp.ctx = None  # type: ignore[assignment]
    disp.session_factory = None  # type: ignore[assignment]
    disp.tools_called = {}
    disp.files_read = set()
    disp.captured_markdown = None
    disp.last_error = None
    disp.ledger = led
    return disp


class _LLM:
    """Programmable LLM stub for the outline + prose passes.

    `outline_outputs` is a list of strings or `StructuredCompletionError`
    instances delivered through `complete_with_tools.final_text` (one
    per outline attempt). `prose_output` is a string, an
    `StructuredCompletionError`, or None (None means complete_text
    raises NotImplementedError, modeling tests that don't reach pass-2).
    """

    model = "two-pass-stub-v1"

    def __init__(
        self,
        *,
        outline_outputs: list[str | StructuredCompletionError],
        prose_output: str | StructuredCompletionError | None = None,
    ):
        self._outline_outputs = list(outline_outputs)
        self._prose_output = prose_output

    async def complete_text(self, **_kwargs):
        if self._prose_output is None:
            raise NotImplementedError("prose pass should not be reached")
        if isinstance(self._prose_output, StructuredCompletionError):
            raise self._prose_output
        return self._prose_output

    async def complete_json(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    async def complete_with_tools(self, **_kwargs):
        if not self._outline_outputs:
            raise RuntimeError("outline outputs exhausted")
        item = self._outline_outputs.pop(0)
        if isinstance(item, StructuredCompletionError):
            raise item
        return ToolUseResult(
            stop_reason="end_turn",
            turns_used=2,
            final_text=item,
        )


# ---------------------------------------------------------------------------
# _extract_outline_json
# ---------------------------------------------------------------------------


def test_extract_outline_json_finds_balanced_object_in_preamble() -> None:
    text = (
        "Here is the outline you asked for:\n```json\n"
        '{"sections": [{"heading": "Overview", "reader_questions": [], "facts": []}]}'
        "\n```\nLet me know if you want changes."
    )
    extracted = _extract_outline_json(text)
    assert extracted is not None
    assert extracted.startswith("{")
    assert extracted.endswith("}")
    assert '"sections"' in extracted


def test_extract_outline_json_handles_nested_braces_inside_strings() -> None:
    text = '{"sections": [{"heading": "h{w}r"}]}'
    extracted = _extract_outline_json(text)
    assert extracted == text


def test_extract_outline_json_returns_none_on_unbalanced() -> None:
    assert _extract_outline_json("{") is None
    assert _extract_outline_json("nothing here") is None


# ---------------------------------------------------------------------------
# _run_two_pass_write — happy path
# ---------------------------------------------------------------------------


_GOOD_OUTLINE_JSON = (
    '{"sections":[{"heading":"Public API",'
    '"reader_questions":["public-api"],'
    '"facts":[{"claim":"Account is the root entity.",'
    '"evidence_refs":["node:Account"],'
    '"required_citations":["Account"],"confidence":"high"}]}]}'
)


async def test_two_pass_happy_path_returns_body_and_ok() -> None:
    disp = _dispatcher(
        EvidenceRecord(
            record_id="node:Account",
            source="code_node",
            qn="Account",
            snippet="...",
        )
    )
    llm = _LLM(
        outline_outputs=[_GOOD_OUTLINE_JSON],
        prose_output=(
            "## Public API\n"
            "<!-- answers: public-api -->\n"
            "Account is the root entity. See [[node:Account]].\n"
        ),
    )
    body, telemetry, status = await _run_two_pass_write(
        slug="domain-model",
        spec=_spec(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        bundle=PageBundle(),
        sibling_pages=[_spec()],
        exported_types=[],
        page_notes=None,
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,  # type: ignore[arg-type]
        config=WikiGenerationConfig(),
    )
    assert status == "ok"
    assert body is not None
    assert "## Public API" in body
    assert telemetry.turns_used == 2  # one outline attempt
    assert telemetry.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Outline failures
# ---------------------------------------------------------------------------


async def test_two_pass_outline_llm_error_retries_then_fails() -> None:
    disp = _dispatcher()
    llm = _LLM(
        outline_outputs=[
            StructuredCompletionError("upstream 503"),
            StructuredCompletionError("still 503"),
        ]
    )
    body, telemetry, status = await _run_two_pass_write(
        slug="domain-model",
        spec=_spec(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        bundle=PageBundle(),
        sibling_pages=[_spec()],
        exported_types=[],
        page_notes=None,
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,  # type: ignore[arg-type]
        config=WikiGenerationConfig(),
    )
    assert status == "failed"
    assert body is None


async def test_two_pass_outline_invalid_json_retries_within_budget() -> None:
    disp = _dispatcher(
        EvidenceRecord(
            record_id="node:Account",
            source="code_node",
            qn="Account",
            snippet="...",
        )
    )
    # First attempt emits no JSON; second attempt emits valid JSON.
    llm = _LLM(
        outline_outputs=[
            "Sorry, I forgot the format. Here's some prose instead.",
            f"Here's the outline:\n{_GOOD_OUTLINE_JSON}",
        ],
        prose_output=(
            "## Public API\n<!-- answers: public-api -->\nAccount [[node:Account]].\n"
        ),
    )
    body, _, status = await _run_two_pass_write(
        slug="domain-model",
        spec=_spec(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        bundle=PageBundle(),
        sibling_pages=[_spec()],
        exported_types=[],
        page_notes=None,
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,  # type: ignore[arg-type]
        config=WikiGenerationConfig(),
    )
    assert status == "ok"
    assert body is not None


async def test_two_pass_outline_invalid_schema_falls_back() -> None:
    disp = _dispatcher()
    # Both attempts emit JSON that doesn't match the PageOutline shape
    # (sections is an int, not a list).
    bad = '{"sections": 42}'
    llm = _LLM(outline_outputs=[bad, bad])
    body, _, status = await _run_two_pass_write(
        slug="domain-model",
        spec=_spec(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        bundle=PageBundle(),
        sibling_pages=[_spec()],
        exported_types=[],
        page_notes=None,
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,  # type: ignore[arg-type]
        config=WikiGenerationConfig(),
    )
    assert status == "failed"
    assert body is None


# ---------------------------------------------------------------------------
# Prose failures
# ---------------------------------------------------------------------------


async def test_two_pass_prose_llm_error_returns_failed() -> None:
    disp = _dispatcher(
        EvidenceRecord(
            record_id="node:Account",
            source="code_node",
            qn="Account",
            snippet="...",
        )
    )
    llm = _LLM(
        outline_outputs=[_GOOD_OUTLINE_JSON],
        prose_output=StructuredCompletionError("upstream timeout"),
    )
    body, _, status = await _run_two_pass_write(
        slug="domain-model",
        spec=_spec(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        bundle=PageBundle(),
        sibling_pages=[_spec()],
        exported_types=[],
        page_notes=None,
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,  # type: ignore[arg-type]
        config=WikiGenerationConfig(),
    )
    assert status == "failed"
    assert body is None


async def test_two_pass_prose_empty_returns_failed() -> None:
    disp = _dispatcher(
        EvidenceRecord(
            record_id="node:Account",
            source="code_node",
            qn="Account",
            snippet="...",
        )
    )
    llm = _LLM(outline_outputs=[_GOOD_OUTLINE_JSON], prose_output="   ")
    body, _, status = await _run_two_pass_write(
        slug="domain-model",
        spec=_spec(),
        overview=RepoOverview(one_line="Demo", long_description="..."),
        bundle=PageBundle(),
        sibling_pages=[_spec()],
        exported_types=[],
        page_notes=None,
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,  # type: ignore[arg-type]
        config=WikiGenerationConfig(),
    )
    assert status == "failed"
    assert body is None
