"""Stage 1b: parse `.cograph/wiki.json` (or `.yaml`) — user-supplied steering
that lets a repo override or augment the LLM-decided plan.

When present, the steering file gives the user three knobs:

  - `repo_notes`: free-form notes the planner sees as a `<repo_notes>` block
    in its user message. Used for "this is a port of X", "the auth module
    is being deprecated, point readers at the new one in `pkg/authz`", etc.
  - `pages`: an explicit list of pages. When set, this BYPASSES Stage 2.5
    (clustering) and Stage 3 (LLM planning) — the steering pages become the
    plan verbatim. Devin DeepWiki's behaviour.
  - `pages[].page_notes`: per-page hints that render as `<page_hints>` in
    the writer's user message ("focus on the producer side", "do NOT
    document the legacy adapter — it's being removed in Q2").

Failure mode is "log and continue": any schema or cap violation drops the
offending entries (or the whole file if the top-level shape is wrong) and
the run proceeds without steering. We never let a malformed steering file
fail a regen — the file is user-supplied and may be checked in by mistake.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Caps. These values are not user-configurable on purpose: they keep prompt
# bloat bounded and prevent the steering file from becoming an exfil vector
# for arbitrarily large blobs into the LLM context.
# ---------------------------------------------------------------------------
_MAX_REPO_NOTES = 100
_MAX_NOTE_CHARS = 10_000
_MAX_PAGES = 30
_MAX_PAGE_NOTES = 10
_MAX_PAGE_NOTE_CHARS = 2_000


class RepoNote(BaseModel):
    """One free-form note the planner sees in `<repo_notes>`.

    Truncated to `_MAX_NOTE_CHARS` at parse time. `author` is informational
    only — surfaced in the prompt block so the LLM can attribute the note
    if it needs to.
    """

    content: str
    author: str | None = None

    @field_validator("content")
    @classmethod
    def _truncate_content(cls, value: str) -> str:
        if len(value) > _MAX_NOTE_CHARS:
            return value[:_MAX_NOTE_CHARS]
        return value


class PageHint(BaseModel):
    """One page in the user-supplied page list.

    `parent`, when set, MUST match the `title` of another `PageHint` in the
    same file — and that other hint must NOT itself have a parent
    (single-level hierarchy only). Validation runs at the `WikiSteering`
    level so cross-page checks see the full list.
    """

    title: str
    purpose: str
    parent: str | None = None
    page_notes: list[str] = Field(default_factory=list)

    @field_validator("page_notes")
    @classmethod
    def _cap_page_notes(cls, value: list[str]) -> list[str]:
        capped = value[:_MAX_PAGE_NOTES]
        return [
            note[:_MAX_PAGE_NOTE_CHARS] if len(note) > _MAX_PAGE_NOTE_CHARS else note
            for note in capped
        ]


class WikiSteering(BaseModel):
    """Parsed `.cograph/wiki.json` / `.yaml` — passes through `RepoContext`."""

    repo_notes: list[RepoNote] = Field(default_factory=list)
    pages: list[PageHint] | None = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


_STEERING_DIR = ".cograph"
_STEERING_BASENAMES: tuple[str, ...] = (
    "wiki.json",
    "wiki.yaml",
    "wiki.yml",
)


def _load_raw(path: Path) -> object | None:
    """Read the file at `path` and parse it as JSON or YAML.

    Returns the decoded object or `None` if the file is missing, unreadable,
    or doesn't decode. JSON is tried first regardless of extension because
    `wiki.yaml` containing valid JSON should still parse — YAML is a JSON
    superset, so the JSON-first attempt is just a fast path.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        logger.debug("steering: cannot read %s (%s)", path, exc)
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning("steering: %s is neither valid JSON nor YAML (%s)", path, exc)
        return None


def _validate_pages(pages: list[PageHint]) -> list[PageHint]:
    """Apply cross-page constraints: cap, dedupe titles, validate parents."""
    if not pages:
        return []
    if len(pages) > _MAX_PAGES:
        logger.warning(
            "steering: dropping %d pages over the %d cap",
            len(pages) - _MAX_PAGES,
            _MAX_PAGES,
        )
        pages = pages[:_MAX_PAGES]

    seen_titles: set[str] = set()
    deduped: list[PageHint] = []
    for page in pages:
        title = page.title.strip()
        if not title:
            logger.warning("steering: dropping page with empty title")
            continue
        if title in seen_titles:
            logger.warning("steering: dropping duplicate title %r", title)
            continue
        seen_titles.add(title)
        deduped.append(page.model_copy(update={"title": title}))

    titles = {page.title for page in deduped}
    parented_titles = {
        page.title for page in deduped if page.parent is not None
    }
    cleaned: list[PageHint] = []
    for page in deduped:
        parent = page.parent.strip() if page.parent else None
        if parent == "":
            parent = None
        if parent is not None:
            if parent == page.title:
                logger.warning(
                    "steering: page %r is its own parent — clearing parent",
                    page.title,
                )
                parent = None
            elif parent not in titles:
                logger.warning(
                    "steering: page %r references unknown parent %r — clearing parent",
                    page.title,
                    parent,
                )
                parent = None
            elif parent in parented_titles:
                # Plan invariant: at most one level of hierarchy. If the
                # parent itself has a parent, the user is trying to nest
                # three deep.
                logger.warning(
                    "steering: page %r has a 2-level parent chain via %r — "
                    "clearing parent (single-level hierarchy only)",
                    page.title,
                    parent,
                )
                parent = None
        cleaned.append(page.model_copy(update={"parent": parent}))
    return cleaned


def _validate_repo_notes(notes: list[RepoNote]) -> list[RepoNote]:
    if len(notes) > _MAX_REPO_NOTES:
        logger.warning(
            "steering: dropping %d repo_notes over the %d cap",
            len(notes) - _MAX_REPO_NOTES,
            _MAX_REPO_NOTES,
        )
        notes = notes[:_MAX_REPO_NOTES]
    return notes


def load_wiki_steering(checkout_path: Path | str | None) -> WikiSteering | None:
    """Locate and parse `<checkout>/.cograph/wiki.{json,yaml,yml}`.

    Returns `None` when the checkout is absent, the file is missing, or the
    file fails top-level schema validation. Returns a `WikiSteering` with
    pruned content (caps applied, invalid entries dropped) on partial
    success.
    """
    if checkout_path is None:
        return None
    root = Path(checkout_path)
    if not root.is_dir():
        return None
    steering_dir = root / _STEERING_DIR
    if not steering_dir.is_dir():
        return None

    chosen: Path | None = None
    for basename in _STEERING_BASENAMES:
        candidate = steering_dir / basename
        if candidate.is_file():
            chosen = candidate
            break
    if chosen is None:
        return None

    raw = _load_raw(chosen)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.warning(
            "steering: %s top-level must be an object, got %s — ignoring",
            chosen,
            type(raw).__name__,
        )
        return None

    try:
        parsed = WikiSteering.model_validate(raw)
    except ValidationError as exc:
        logger.warning(
            "steering: %s failed schema validation (%s) — ignoring", chosen, exc
        )
        return None

    repo_notes = _validate_repo_notes(parsed.repo_notes)
    pages = _validate_pages(parsed.pages) if parsed.pages else None
    if pages is not None and not pages:
        # User declared `pages` but every entry was dropped by validation —
        # treat as no override rather than emitting an empty plan.
        logger.warning(
            "steering: %s has `pages` but none survived validation; "
            "falling back to LLM planning",
            chosen,
        )
        pages = None
    return WikiSteering(repo_notes=repo_notes, pages=pages)


__all__ = [
    "PageHint",
    "RepoNote",
    "WikiSteering",
    "load_wiki_steering",
]
