"""Round-trip tests for T5 outline schemas (`PageOutline`, `SectionOutline`,
`Fact`, `FactConfidence`).

These pin the JSON shape pass-1 emits, so a future schema change is an
intentional, reviewed edit rather than a silent prompt drift.
"""

from __future__ import annotations

import json

from backend.app.wiki.schemas import (
    Fact,
    FactConfidence,
    PageOutline,
    SectionOutline,
)


def test_fact_defaults_are_empty_lists_and_medium_confidence() -> None:
    fact = Fact(claim="The CLI exposes one entry point.")
    assert fact.evidence_refs == []
    assert fact.required_citations == []
    assert fact.confidence is FactConfidence.MEDIUM


def test_fact_round_trips_through_json_with_all_fields() -> None:
    src = Fact(
        claim="Run kicks off the pipeline.",
        evidence_refs=["node:cmd.Run", "file:cmd/main.go:1-20"],
        required_citations=["cmd.Run"],
        confidence=FactConfidence.HIGH,
    )
    dumped = src.model_dump_json()
    reloaded = Fact.model_validate_json(dumped)
    assert reloaded == src
    parsed = json.loads(dumped)
    # `confidence` serialises as the enum value, not the name — that's
    # what the writer's outline JSON emits.
    assert parsed["confidence"] == "high"


def test_section_outline_round_trips() -> None:
    src = SectionOutline(
        heading="How to run",
        reader_questions=["how-to-run"],
        facts=[
            Fact(
                claim="Build with `go build .`.",
                evidence_refs=["doc:README.md"],
                confidence=FactConfidence.LOW,
            )
        ],
    )
    reloaded = SectionOutline.model_validate_json(src.model_dump_json())
    assert reloaded == src
    assert reloaded.facts[0].confidence is FactConfidence.LOW


def test_page_outline_default_is_empty_sections_list() -> None:
    out = PageOutline()
    assert out.sections == []
    # Round-trip through JSON keeps the empty shape stable.
    assert PageOutline.model_validate_json(out.model_dump_json()) == out


def test_page_outline_round_trips_multi_section() -> None:
    src = PageOutline(
        sections=[
            SectionOutline(
                heading="Overview",
                reader_questions=[],
                facts=[
                    Fact(claim="High-level intent.", confidence=FactConfidence.MEDIUM)
                ],
            ),
            SectionOutline(
                heading="API Reference",
                reader_questions=["public-api"],
                facts=[
                    Fact(
                        claim="`Run` is the only exported entry point.",
                        evidence_refs=["node:cmd.Run"],
                        required_citations=["cmd.Run"],
                        confidence=FactConfidence.HIGH,
                    ),
                    Fact(
                        claim="`Config` carries runtime options.",
                        evidence_refs=["node:cmd.Config"],
                        required_citations=["cmd.Config"],
                        confidence=FactConfidence.MEDIUM,
                    ),
                ],
            ),
        ]
    )
    reloaded = PageOutline.model_validate_json(src.model_dump_json())
    assert reloaded == src
    assert [s.heading for s in reloaded.sections] == ["Overview", "API Reference"]
    assert len(reloaded.sections[1].facts) == 2
