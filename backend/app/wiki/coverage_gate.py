"""Deterministic coverage gate (T4): every `covers_questions` slug must
be answered by a `<!-- answers: question-slug -->` marker followed by a
verified citation in the same section.

Prior to T4, the writer was free to emit `## Open questions` for gaps,
which left half-grounded pages shipping unchallenged. T4 flips the
contract: there is NO `## Open questions` section. If a question can't
be grounded with verified evidence, the writer must omit the section
entirely and leave the question as `missing` in telemetry. The page
ships at `quality_status=partial` instead of carrying ungrounded prose.

A "section" for coverage purposes is the run of markdown from the
marker to the next H2 (`^## `) or end-of-document. Within that section
we look for at least one of:

  - a verified `[[node:X]]` citation (X in `ledger.verified_node_qns`)
  - a verified `[[doc:Y]]` citation (path-of-Y in `ledger.verified_doc_paths`)
  - a `Source: path:Lstart-Lend` attribution line whose `path` is in
    `ledger.verified_file_paths` OR `verified_doc_paths`

Public surface:

    result = validate_coverage(
        markdown=body,
        covers_questions=spec.covers_questions,
        ledger=dispatcher.ledger,
    )
    # result.answered_questions, result.missing_questions
    # result.has_open_questions_section  (forbidden — fails the contract)

Pure / deterministic. No I/O. No LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal

from backend.app.wiki.citations import PLACEHOLDER_RE
from backend.app.wiki.evidence_ledger import VerifiedEvidenceLedger
from backend.app.wiki.schemas import ReaderQuestion


_ANSWER_MARKER_RE = re.compile(
    r"<!--\s*answers\s*:\s*([a-z][a-z0-9-]*)\s*-->",
    re.IGNORECASE,
)
_H2_RE = re.compile(r"^##\s+", re.MULTILINE)
_SOURCE_LINE_RE = re.compile(
    r"^\s*Source\s*:\s*`?([^\s`:]+(?:/[^\s`:]+)*)(?::L\d+(?:-L?\d+)?)?`?",
    re.MULTILINE,
)
_OPEN_QUESTIONS_HEADING_RE = re.compile(
    r"^##\s+open\s+questions\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TEST_STRATEGY_HEADING_RE = re.compile(
    r"^##\s+test\s+strategy\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_COMPARISON_HEADING_RE = re.compile(
    r"^##\s+comparison\s+with\s+alternatives\s*$",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoverageResult:
    """Outcome of a coverage gate run.

    `answered_questions` lists the slugs whose marker was found AND was
    grounded by a verified citation/source line. `missing_questions`
    lists the slugs that either had no marker at all OR had a marker
    without grounding.

    `markers_without_grounding` lists slugs whose marker WAS present
    but the section had no verified evidence — these are the prime
    repair candidates (the writer remembered the marker but forgot the
    cite). `extra_markers` lists slugs that appear in the markdown but
    weren't on the page's `covers_questions` list — usually a copy-paste
    slip; we keep them as telemetry but don't fail on them.

    `has_open_questions_section`, `has_test_strategy_section`, and
    `has_comparison_section` flag the three forbidden H2s. The plan
    contract forbids all three: `## Open questions` (escape hatch for
    ungrounded prose), `## Test Strategy` (testing belongs in the codebase,
    not the wiki), and `## Comparison with alternatives` (we don't compare
    third-party libs in product docs).
    """

    answered_questions: list[str] = field(default_factory=list)
    missing_questions: list[str] = field(default_factory=list)
    markers_without_grounding: list[str] = field(default_factory=list)
    extra_markers: list[str] = field(default_factory=list)
    has_open_questions_section: bool = False
    has_test_strategy_section: bool = False
    has_comparison_section: bool = False

    @property
    def has_forbidden_section(self) -> bool:
        """True iff any of the three forbidden H2s are present."""
        return (
            self.has_open_questions_section
            or self.has_test_strategy_section
            or self.has_comparison_section
        )

    @property
    def is_clean(self) -> bool:
        """True iff every required question is answered AND no forbidden
        H2 section is present."""
        return not self.missing_questions and not self.has_forbidden_section


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_coverage(
    *,
    markdown: str,
    covers_questions: Iterable[ReaderQuestion | str],
    ledger: VerifiedEvidenceLedger,
) -> CoverageResult:
    """Validate that every required question has a grounded section.

    Algorithm:
      1. Parse `<!-- answers: slug -->` markers and their positions.
      2. For each marker, slice the section it owns (marker → next H2
         or EOF) and look for at least one verified citation or a
         verified `Source:` attribution line.
      3. Compare against the required slugs from `covers_questions`.
    """
    required = _normalize_question_set(covers_questions)
    body = markdown or ""

    has_open_q = bool(_OPEN_QUESTIONS_HEADING_RE.search(body))
    has_test_strategy = bool(_TEST_STRATEGY_HEADING_RE.search(body))
    has_comparison = bool(_COMPARISON_HEADING_RE.search(body))

    if not body:
        return CoverageResult(
            answered_questions=[],
            missing_questions=sorted(required),
            markers_without_grounding=[],
            extra_markers=[],
            has_open_questions_section=False,
            has_test_strategy_section=False,
            has_comparison_section=False,
        )

    markers = _find_markers(body)
    section_bounds = _section_bounds(body)

    grounded: set[str] = set()
    ungrounded: set[str] = set()
    seen_markers: set[str] = set()
    for marker_pos, slug in markers:
        seen_markers.add(slug)
        section_end = _section_end_for(marker_pos, section_bounds, len(body))
        section_text = body[marker_pos:section_end]
        if _section_has_verified_evidence(section_text, ledger):
            grounded.add(slug)
        else:
            ungrounded.add(slug)

    answered = sorted(grounded & required)
    missing = sorted(required - grounded)
    extra_markers = sorted(seen_markers - required)
    markers_without_grounding = sorted(ungrounded)

    return CoverageResult(
        answered_questions=answered,
        missing_questions=missing,
        markers_without_grounding=markers_without_grounding,
        extra_markers=extra_markers,
        has_open_questions_section=has_open_q,
        has_test_strategy_section=has_test_strategy,
        has_comparison_section=has_comparison,
    )


def ensure_inferred_answer_markers(
    *,
    markdown: str,
    covers_questions: Iterable[ReaderQuestion | str],
    ledger: VerifiedEvidenceLedger,
) -> str:
    """Insert missing answer markers for already-grounded H2 sections.

    The marker is an internal contract comment, not reader-facing prose. If
    the writer clearly produced an evidenced `## Configuration` / `## Usage`
    / `## API` section but forgot the comment, adding it preserves quality
    and prevents a false `partial` status.
    """
    body = markdown or ""
    required = _normalize_question_set(covers_questions)
    if not body or not required:
        return body

    existing = {slug for _pos, slug in _find_markers(body)}
    missing = required - existing
    if not missing:
        return body

    insertions: list[tuple[int, str]] = []
    for heading in _H2_RE.finditer(body):
        heading_line_end = body.find("\n", heading.end())
        if heading_line_end == -1:
            heading_line_end = len(body)
        section_end = _section_end_for(heading.start(), _section_bounds(body), len(body))
        section_text = body[heading.start():section_end]
        if not _section_has_verified_evidence(section_text, ledger):
            continue
        title = body[heading.end():heading_line_end].strip().lower()
        matched = [slug for slug in sorted(missing) if _heading_answers(title, slug)]
        if not matched:
            continue
        marker_text = "".join(f"<!-- answers: {slug} -->\n" for slug in matched)
        insertions.append((heading_line_end + 1, marker_text))
        missing -= set(matched)
        if not missing:
            break

    if not insertions:
        return body
    out = body
    for pos, text in sorted(insertions, reverse=True):
        out = out[:pos] + text + out[pos:]
    return out


# ---------------------------------------------------------------------------
# Strip helpers — last-resort cleanup so a partial page can ship cleanly
# ---------------------------------------------------------------------------


def _strip_section(markdown: str, heading_re: re.Pattern[str]) -> str:
    """Remove a single H2 section matched by `heading_re` and the body
    that runs from the heading to the next H2 or EOF."""
    if not markdown:
        return markdown
    match = heading_re.search(markdown)
    if match is None:
        return markdown
    start = match.start()
    next_h2 = _H2_RE.search(markdown, match.end())
    end = next_h2.start() if next_h2 else len(markdown)
    cleaned = markdown[:start] + markdown[end:]
    return cleaned.rstrip() + "\n"


def strip_open_questions_section(markdown: str) -> str:
    """Remove the forbidden `## Open questions` H2 and its content.

    Used as the final fallback after the coverage repair pass exhausts:
    the writer cannot stop emitting `Open questions`, so we strip it
    silently rather than ship a section the contract forbids.
    """
    return _strip_section(markdown, _OPEN_QUESTIONS_HEADING_RE)


def strip_test_strategy_section(markdown: str) -> str:
    """Remove the forbidden `## Test Strategy` H2 and its content."""
    return _strip_section(markdown, _TEST_STRATEGY_HEADING_RE)


def strip_comparison_section(markdown: str) -> str:
    """Remove the forbidden `## Comparison with alternatives` H2 and its
    content."""
    return _strip_section(markdown, _COMPARISON_HEADING_RE)


def strip_forbidden_sections(markdown: str) -> str:
    """Remove every forbidden H2 section in one pass.

    Convenience for callers that don't care which specific forbidden
    section is present — strip them all and ship clean.
    """
    out = strip_open_questions_section(markdown)
    out = strip_test_strategy_section(out)
    out = strip_comparison_section(out)
    return out


def strip_unanswered_markers(markdown: str, unanswered_slugs: Iterable[str]) -> str:
    """Remove `<!-- answers: slug -->` markers whose slug appears in
    `unanswered_slugs`. The surrounding section text is preserved — we
    only drop the (now misleading) marker comment so coverage telemetry
    accurately reflects what the writer grounded."""
    targets = {s for s in unanswered_slugs if s}
    if not targets:
        return markdown

    def _replace(match: re.Match[str]) -> str:
        slug = match.group(1).strip().lower()
        if slug in targets:
            return ""
        return match.group(0)

    return _ANSWER_MARKER_RE.sub(_replace, markdown)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalize_question_set(
    questions: Iterable[ReaderQuestion | str],
) -> set[str]:
    out: set[str] = set()
    for q in questions or ():
        if isinstance(q, ReaderQuestion):
            out.add(q.value)
        elif isinstance(q, str):
            slug = q.strip().lower()
            if slug:
                out.add(slug)
    return out


def _find_markers(body: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for match in _ANSWER_MARKER_RE.finditer(body):
        slug = match.group(1).strip().lower()
        out.append((match.start(), slug))
    return out


def _section_bounds(body: str) -> list[int]:
    """Return the start positions of every `## ` heading in `body`.

    Used as the universe of section boundaries — the section "owned" by
    a marker runs from the marker's position to the next H2 (or EOF).
    """
    return [m.start() for m in _H2_RE.finditer(body)]


def _section_end_for(marker_pos: int, section_starts: list[int], doc_end: int) -> int:
    for start in section_starts:
        if start > marker_pos:
            return start
    return doc_end


def _section_has_verified_evidence(
    section_text: str, ledger: VerifiedEvidenceLedger
) -> bool:
    """True iff the section contains at least one verified citation or
    a verified `Source:` attribution line."""
    verified_nodes = ledger.verified_node_qns
    verified_docs = ledger.verified_doc_paths
    verified_files = ledger.verified_file_paths

    for match in PLACEHOLDER_RE.finditer(section_text):
        kind = match.group(1)
        value = match.group(2).strip()
        if not value:
            continue
        if kind == "node" and value in verified_nodes:
            return True
        if kind == "doc":
            path = value.partition("#")[0].strip().lstrip("./")
            if path and path in verified_docs:
                return True

    for src_match in _SOURCE_LINE_RE.finditer(section_text):
        path = src_match.group(1).strip().lstrip("./")
        if not path:
            continue
        if path in verified_files or path in verified_docs:
            return True
    return False


def _heading_answers(title: str, slug: str) -> bool:
    keywords = {
        "configuration": (
            "configuration",
            "config",
            "settings",
            "environment",
            "runtime configuration",
        ),
        "how-to-run": (
            "getting started",
            "quick start",
            "usage",
            "run",
            "local development",
            "build",
        ),
        "dependencies": (
            "dependencies",
            "dependency",
            "runtime",
            "infrastructure",
            "integration",
            "wiring",
        ),
        "public-api": (
            "api",
            "entrypoint",
            "entrypoints",
            "route",
            "routes",
            "public surface",
        ),
        "use-cases": (
            "overview",
            "use case",
            "use cases",
            "usage",
            "domain",
            "business",
            "problem",
        ),
    }
    return any(keyword in title for keyword in keywords.get(slug, ()))


CoverageOutcome = Literal[
    "ok",
    "partial",
    "open_questions_forbidden",
    "test_strategy_forbidden",
    "comparison_forbidden",
]


def coverage_outcome(result: CoverageResult) -> CoverageOutcome:
    """Map a CoverageResult to a stable outcome label for telemetry.

    Forbidden-section outcomes take precedence over `partial`/`ok`
    because they signal a contract violation. Open-questions wins the
    tiebreaker among forbidden sections — it's the most common writer
    regression and most urgent to surface.
    """
    if result.has_open_questions_section:
        return "open_questions_forbidden"
    if result.has_test_strategy_section:
        return "test_strategy_forbidden"
    if result.has_comparison_section:
        return "comparison_forbidden"
    if result.missing_questions:
        return "partial"
    return "ok"


__all__ = (
    "CoverageOutcome",
    "CoverageResult",
    "coverage_outcome",
    "ensure_inferred_answer_markers",
    "strip_comparison_section",
    "strip_forbidden_sections",
    "strip_open_questions_section",
    "strip_test_strategy_section",
    "strip_unanswered_markers",
    "validate_coverage",
)
