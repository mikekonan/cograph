"""Direct unit tests for `_run_coverage_gate_loop` (T4 wiring).

The full `write_pages` path requires a real session/tool context to
populate the dispatcher's evidence ledger; these tests instead drive
the loop directly with a manually-seeded `VerifiedEvidenceLedger` so
each branch (clean / repair-success / repair-fail-strip) is observable
without booting tool-dispatch infrastructure.
"""

from __future__ import annotations

import pytest

from backend.app.wiki.agent_dispatcher import AgentDispatcher
from backend.app.wiki.evidence_ledger import VerifiedEvidenceLedger
from backend.app.wiki.llm_client import (
    StructuredCompletionError,
    ToolUseResult,
)
from backend.app.wiki.pipeline import (
    WikiGenerationConfig,
    _run_coverage_gate_loop,
)
from backend.app.wiki.schemas import (
    AgentTelemetry,
    EvidenceRecord,
    PageSpec,
    QualityStatus,
    ReaderQuestion,
)

pytestmark = pytest.mark.asyncio


def _spec(*covers: ReaderQuestion) -> PageSpec:
    return PageSpec(
        slug="cli",
        title="CLI",
        purpose="about cli",
        covers_questions=list(covers),
    )


def _node_record(qn: str) -> EvidenceRecord:
    return EvidenceRecord(
        record_id=f"node:{qn}",
        source="code_node",
        qn=qn,
        snippet="...",
    )


def _dispatcher(*records: EvidenceRecord) -> AgentDispatcher:
    """Make a dispatcher with a pre-seeded ledger and a tool_context that
    is never actually invoked (the loop only reads `dispatcher.ledger`
    and writes `dispatcher.captured_markdown` here)."""
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


class _ScriptedLLM:
    """Minimal LLM that returns scripted bodies via `tool_dispatch`.

    Each `complete_with_tools` call shifts one entry off the queue. An
    entry is either a markdown string (delivered through the
    `write_page` dispatch) or a `StructuredCompletionError` instance
    (raised verbatim).
    """

    model = "scripted-v1"

    def __init__(self, scripted: list[str | StructuredCompletionError]):
        self._scripted = list(scripted)

    async def complete_text(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    async def complete_json(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    async def complete_with_tools(self, **kwargs):
        if not self._scripted:
            raise RuntimeError("scripted LLM exhausted")
        item = self._scripted.pop(0)
        if isinstance(item, StructuredCompletionError):
            raise item
        await kwargs["tool_dispatch"]("write_page", {"markdown": item})
        return ToolUseResult(stop_reason="end_turn", turns_used=1)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_clean_first_draft_keeps_status_and_records_answered() -> None:
    body = "## How to run\n<!-- answers: how-to-run -->\nUse [[node:cmd.Run]].\n"
    disp = _dispatcher(_node_record("cmd.Run"))
    out_body, telemetry = await _run_coverage_gate_loop(
        slug="cli",
        spec=_spec(ReaderQuestion.HOW_TO_RUN),
        body=body,
        telemetry=AgentTelemetry(),
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=_ScriptedLLM([]),  # never invoked
        config=WikiGenerationConfig(),
    )
    assert out_body == body
    assert telemetry.answered_questions == ["how-to-run"]
    assert telemetry.missing_questions == []
    assert telemetry.coverage_repair_attempts == 0
    # No status update — caller's existing T3 status (None here) stays.
    assert telemetry.quality_status is None


async def test_no_covers_questions_short_circuits() -> None:
    """Index page with empty covers_questions skips the gate entirely."""
    disp = _dispatcher()
    body = "# Index\n## Open questions\n- foo\n"
    out_body, telemetry = await _run_coverage_gate_loop(
        slug="index",
        spec=PageSpec(slug="index", title="Index", purpose="root"),
        body=body,
        telemetry=AgentTelemetry(),
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=_ScriptedLLM([]),
        config=WikiGenerationConfig(),
    )
    # Body untouched — even forbidden Open questions sails through when
    # there's no contract on the page. The coverage-status doesn't apply.
    assert out_body == body
    assert telemetry.answered_questions == []
    assert telemetry.missing_questions == []


# ---------------------------------------------------------------------------
# Repair paths
# ---------------------------------------------------------------------------


async def test_repair_success_promotes_to_partial() -> None:
    """First draft missing the marker; one repair attempt adds it →
    page ships at PARTIAL (clean shape, but had to repair)."""
    initial_body = "## How to run\nRun [[node:cmd.Run]].\n"
    repaired = "## How to run\n<!-- answers: how-to-run -->\nRun [[node:cmd.Run]].\n"
    disp = _dispatcher(_node_record("cmd.Run"))
    llm = _ScriptedLLM([repaired])
    out_body, telemetry = await _run_coverage_gate_loop(
        slug="cli",
        spec=_spec(ReaderQuestion.HOW_TO_RUN),
        body=initial_body,
        telemetry=AgentTelemetry(quality_status=QualityStatus.OK),
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,
        config=WikiGenerationConfig(),
    )
    # Loop strips the trailing newline before returning.
    assert out_body == repaired.rstrip()
    assert telemetry.answered_questions == ["how-to-run"]
    assert telemetry.missing_questions == []
    assert telemetry.coverage_repair_attempts == 1
    assert telemetry.quality_status == QualityStatus.PARTIAL


async def test_repair_failure_strips_open_questions_and_marks_degraded() -> None:
    """Writer keeps `## Open questions` even after repair → strip
    fallback triggers, status downgrades to DEGRADED, bullets captured
    as telemetry."""
    initial_body = (
        "## Overview\nGeneric.\n\n## Open questions\n- We don't know the public API.\n"
    )
    persisted_body = (
        "## Overview\nStill generic.\n\n"
        "## Open questions\n- We still don't know the public API.\n"
    )
    disp = _dispatcher()
    llm = _ScriptedLLM([persisted_body])
    out_body, telemetry = await _run_coverage_gate_loop(
        slug="api",
        spec=_spec(ReaderQuestion.PUBLIC_API),
        body=initial_body,
        telemetry=AgentTelemetry(quality_status=QualityStatus.OK),
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,
        config=WikiGenerationConfig(),
    )
    assert "## Open questions" not in out_body
    assert telemetry.missing_questions == ["public-api"]
    assert telemetry.answered_questions == []
    assert telemetry.coverage_repair_attempts == 1
    assert telemetry.quality_status == QualityStatus.DEGRADED
    assert telemetry.open_questions_declared == ["We still don't know the public API."]


async def test_repair_failure_without_open_questions_marks_partial() -> None:
    """Writer omits the section entirely — repair fails to ground it
    either. No `## Open questions` was ever emitted, so the strip path
    sets PARTIAL (clean shape, just incomplete coverage)."""
    initial_body = "## Overview\nGeneric prose only.\n"
    persisted_body = "## Overview\nStill nothing to ground here.\n"
    disp = _dispatcher()
    llm = _ScriptedLLM([persisted_body])
    out_body, telemetry = await _run_coverage_gate_loop(
        slug="api",
        spec=_spec(ReaderQuestion.PUBLIC_API),
        body=initial_body,
        telemetry=AgentTelemetry(quality_status=QualityStatus.OK),
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,
        config=WikiGenerationConfig(),
    )
    assert out_body == persisted_body.rstrip()
    assert telemetry.missing_questions == ["public-api"]
    assert telemetry.coverage_repair_attempts == 1
    assert telemetry.quality_status == QualityStatus.PARTIAL
    assert telemetry.open_questions_declared == []


async def test_repair_llm_error_falls_back_to_strip() -> None:
    """If the repair call raises, the loop stops and the strip-fallback
    runs against the original body."""
    initial_body = "## Overview\nGeneric.\n\n## Open questions\n- gap\n"
    disp = _dispatcher()
    llm = _ScriptedLLM([StructuredCompletionError("repair LLM down")])
    out_body, telemetry = await _run_coverage_gate_loop(
        slug="api",
        spec=_spec(ReaderQuestion.PUBLIC_API),
        body=initial_body,
        telemetry=AgentTelemetry(quality_status=QualityStatus.OK),
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,
        config=WikiGenerationConfig(),
    )
    assert "## Open questions" not in out_body
    # Repair counted (we attempted) but the LLM never returned.
    assert telemetry.coverage_repair_attempts == 1
    assert telemetry.missing_questions == ["public-api"]
    assert telemetry.quality_status == QualityStatus.DEGRADED


# ---------------------------------------------------------------------------
# Status downgrade interactions
# ---------------------------------------------------------------------------


async def test_status_does_not_upgrade_existing_degraded() -> None:
    """If T3 already set DEGRADED (citations stripped), a clean-after-
    repair coverage outcome must NOT promote the page back to PARTIAL."""
    initial_body = "## How to run\nRun [[node:cmd.Run]].\n"
    repaired = "## How to run\n<!-- answers: how-to-run -->\nRun [[node:cmd.Run]].\n"
    disp = _dispatcher(_node_record("cmd.Run"))
    llm = _ScriptedLLM([repaired])
    out_body, telemetry = await _run_coverage_gate_loop(
        slug="cli",
        spec=_spec(ReaderQuestion.HOW_TO_RUN),
        body=initial_body,
        telemetry=AgentTelemetry(quality_status=QualityStatus.DEGRADED),
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,
        config=WikiGenerationConfig(),
    )
    assert out_body == repaired.rstrip()
    # Even though coverage is now clean, T3's DEGRADED stays.
    assert telemetry.quality_status == QualityStatus.DEGRADED


async def test_strips_unanswered_marker_when_section_present_but_ungrounded() -> None:
    """Writer emitted the marker but the section had no verified cite,
    AND the repair couldn't ground it. Strip-fallback removes the
    marker comment so coverage telemetry on disk reflects reality."""
    initial_body = (
        "## Configuration\n"
        "<!-- answers: configuration -->\n"
        "There are several config options. See the file.\n"
    )
    persisted_body = initial_body  # repair didn't fix anything
    disp = _dispatcher()
    llm = _ScriptedLLM([persisted_body])
    out_body, telemetry = await _run_coverage_gate_loop(
        slug="cfg",
        spec=_spec(ReaderQuestion.CONFIGURATION),
        body=initial_body,
        telemetry=AgentTelemetry(quality_status=QualityStatus.OK),
        dispatcher=disp,
        tool_definitions=[],
        cached_repo_block="",
        llm=llm,
        config=WikiGenerationConfig(),
    )
    assert "<!-- answers: configuration -->" not in out_body
    assert "There are several config options." in out_body
    assert telemetry.missing_questions == ["configuration"]
    assert telemetry.quality_status == QualityStatus.PARTIAL
