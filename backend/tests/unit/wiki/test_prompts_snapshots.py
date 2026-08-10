"""Golden-snapshot guard for the three V1 system prompts.

The system blocks ride at the front of the cached prefix on OpenAI Chat
Completions (implicit prefix caching). Any change to their text breaks
cache hits and inflates the per-run cost model, so we want intentional,
reviewed edits, not silent drift.

If a prompt change is intentional, regenerate the snapshot:

    python -c "from backend.app.wiki.prompts import REPO_ANALYZER_SYSTEM; \
        print(REPO_ANALYZER_SYSTEM, end='')" \
        > backend/tests/snapshots/prompts/repo_analyzer_system.txt

(Same idea for `PAGE_PLANNER_SYSTEM` and `PAGE_WRITER_SYSTEM`.) Then commit
the snapshot with the prompt change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.wiki.prompts import (
    DIAGRAM_SYNTHESIZER_SYSTEM,
    MINDMAP_GENERATOR_SYSTEM,
    PAGE_OUTLINE_SYSTEM,
    PAGE_PLANNER_SYSTEM,
    PAGE_PROSE_SYSTEM,
    PAGE_WRITER_SYSTEM,
    REPO_ANALYZER_SYSTEM,
)

SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "snapshots" / "prompts"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("mindmap_generator_system.txt", MINDMAP_GENERATOR_SYSTEM),
        ("repo_analyzer_system.txt", REPO_ANALYZER_SYSTEM),
        ("page_planner_system.txt", PAGE_PLANNER_SYSTEM),
        ("page_writer_system.txt", PAGE_WRITER_SYSTEM),
        ("page_outline_system.txt", PAGE_OUTLINE_SYSTEM),
        ("page_prose_system.txt", PAGE_PROSE_SYSTEM),
        ("diagram_synthesizer_system.txt", DIAGRAM_SYNTHESIZER_SYSTEM),
    ],
)
def test_system_prompt_matches_golden_snapshot(name: str, value: str) -> None:
    snapshot = (SNAPSHOT_DIR / name).read_text()
    assert value == snapshot, (
        f"System prompt {name} drifted from its golden snapshot. "
        "If the change is intentional, regenerate the snapshot under "
        f"backend/tests/snapshots/prompts/{name}."
    )
