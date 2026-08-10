"""Stage 0 helper: parse `docs/` + `README` outlines into seed topics.

Doc files are the strongest user-facing signal we have for what the
maintainers think a reader should learn. A heading like
`## Authentication flow` in `docs/architecture.md` is much better
evidence of an important page than the underlying code's directory
shape — somebody decided that topic deserved prose explanation.

This module walks doc files, classifies them by filename allowlist,
and emits `DocSection` outline entries plus `TopicCandidate`s. The
output is fed into `repo_signals.build_repo_signals` which mixes them
with file-cluster and CLI-AST candidates.

Staleness mitigation: a heading that matches no symbol or file path in
the repository is demoted to `supporting` with a `docs_heading_no_code_evidence`
demotion reason. The signal isn't dropped — sometimes prose explains a
concept that doesn't map to a single symbol — but it can't out-rank
real code-backed evidence either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from backend.app.wiki.schemas import (
    CandidateKind,
    DocSection,
    SalienceTier,
    TopicCandidate,
)

# ---------------------------------------------------------------------------
# Filename allowlists
# ---------------------------------------------------------------------------


_PUBLIC_DOC_NAMES: Final = frozenset(
    {
        "usage",
        "guide",
        "tutorial",
        "quickstart",
        "quick-start",
        "getting-started",
        "getting_started",
        "cli",
        "api",
        "reference",
        "examples",
        "architecture",
        "overview",
    }
)

_SUPPORTING_DOC_NAMES: Final = frozenset(
    {
        "internals",
        "design",
        "concepts",
        "model",
    }
)

_NON_CONTENT_DOC_NAMES: Final = frozenset(
    {
        "contributing",
        "development",
        "developing",
        "release",
        "releases",
        "changelog",
        "license",
        "code_of_conduct",
        "code-of-conduct",
        "security",
        "testing",
        "ci",
    }
)


_DOC_PATH_RE: Final = re.compile(
    r"^(?:README(?:\.[a-zA-Z]+)?|"
    r"docs?/.+\.(?:md|rst|markdown)|"
    r"docs?/.+/.+\.(?:md|rst|markdown))$",
    re.IGNORECASE,
)


_HEADING_RE: Final = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocFile:
    """Inputs to `build_docs_outline`."""

    file_path: str
    content: str


@dataclass(frozen=True, slots=True)
class DocsOutlineResult:
    """Outline + topic-candidate seeds from doc files."""

    sections: list[DocSection]
    candidates: list[TopicCandidate]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_docs_outline(
    doc_files: list[DocFile],
    *,
    repo_paths: set[str] | None = None,
    repo_symbol_names: set[str] | None = None,
) -> DocsOutlineResult:
    """Parse `doc_files` into outline entries + topic candidates.

    `repo_paths` and `repo_symbol_names`, when provided, drive the
    staleness-mitigation step: a heading that matches no path or
    symbol identifier is demoted from PUBLIC to SUPPORTING.
    """
    sections: list[DocSection] = []
    candidates: list[TopicCandidate] = []
    seen_keys: set[str] = set()

    for doc in doc_files:
        if not _DOC_PATH_RE.match(doc.file_path):
            continue
        tier = _classify_doc_tier(doc.file_path)
        if tier is None:
            # Non-content allowlist — skip entirely.
            continue
        is_public = tier == SalienceTier.PUBLIC
        for level, heading in _iter_headings(doc.content):
            section = DocSection(
                file_path=doc.file_path,
                heading=heading,
                level=level,
                public=is_public,
            )
            sections.append(section)
            # Only top-level (H1/H2) headings seed candidates — H3+ is
            # too fine-grained to be a wiki page on its own.
            if level > 2:
                continue
            key = _candidate_key(doc.file_path, heading)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            score, candidate_tier, demotions = _heading_score(
                tier=tier,
                heading=heading,
                repo_paths=repo_paths,
                repo_symbol_names=repo_symbol_names,
            )
            candidates.append(
                TopicCandidate(
                    id=_make_id(key),
                    title=heading,
                    normalized_key=key,
                    salience_score=score,
                    salience_tier=candidate_tier,
                    candidate_kind=CandidateKind.DOCS_TOPIC,
                    reasons=["docs_heading"],
                    demotion_reasons=demotions,
                    evidence_paths=[doc.file_path],
                    docs=[doc.file_path],
                )
            )

    return DocsOutlineResult(sections=sections, candidates=candidates)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _classify_doc_tier(path: str) -> SalienceTier | None:
    """Return PUBLIC / SUPPORTING based on the doc filename, or None
    if the path is on the non-content allowlist."""
    base = path.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0].lower()
    if stem in _NON_CONTENT_DOC_NAMES:
        return None
    if stem in _PUBLIC_DOC_NAMES:
        return SalienceTier.PUBLIC
    if stem in _SUPPORTING_DOC_NAMES:
        return SalienceTier.SUPPORTING
    # README is a special case: always public.
    if stem.lower() == "readme":
        return SalienceTier.PUBLIC
    # Anything else under `docs/` defaults to supporting — present and
    # documented but not on the curated list.
    if path.lower().startswith("docs/") or path.lower().startswith("doc/"):
        return SalienceTier.SUPPORTING
    return SalienceTier.SUPPORTING


def _iter_headings(content: str):
    """Yield `(level, heading_text)` for each Markdown heading."""
    in_fence = False
    for raw in content.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group("hashes"))
        text = m.group("text").strip()
        if not text:
            continue
        yield (level, text)


def _candidate_key(file_path: str, heading: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    return f"docs:{file_path}#{slug}"


def _heading_score(
    *,
    tier: SalienceTier,
    heading: str,
    repo_paths: set[str] | None,
    repo_symbol_names: set[str] | None,
) -> tuple[float, SalienceTier, list[str]]:
    """Score a doc heading; demote PUBLIC headings that don't match
    any repo symbol or path."""
    base_public_score = 0.85
    base_supporting_score = 0.45

    if tier == SalienceTier.PUBLIC:
        if _heading_has_code_evidence(heading, repo_paths, repo_symbol_names):
            return (base_public_score, SalienceTier.PUBLIC, [])
        # No code evidence — still useful but demoted so concrete
        # evidence (CLI commands, manifest exports) wins on ties.
        return (
            base_supporting_score,
            SalienceTier.SUPPORTING,
            ["docs_heading_no_code_evidence"],
        )
    return (base_supporting_score, SalienceTier.SUPPORTING, [])


def _heading_has_code_evidence(
    heading: str,
    repo_paths: set[str] | None,
    repo_symbol_names: set[str] | None,
) -> bool:
    """Cheap fuzzy match: does any token from the heading appear in a
    repo path or symbol name?"""
    if not repo_paths and not repo_symbol_names:
        # Caller didn't provide evidence sets — treat as "evidence
        # unknown", default to True so we don't punish projects whose
        # extraction layers haven't run yet.
        return True
    tokens = _heading_tokens(heading)
    if not tokens:
        return False
    if repo_symbol_names:
        symbol_blob = " ".join(repo_symbol_names).lower()
        if any(t in symbol_blob for t in tokens):
            return True
    if repo_paths:
        path_blob = " ".join(repo_paths).lower()
        if any(t in path_blob for t in tokens):
            return True
    return False


def _heading_tokens(heading: str) -> list[str]:
    """Tokenize a heading for evidence search.

    We strip stopwords and short tokens so a heading like
    "Working with the Generator" matches "generator" but doesn't fire
    on the article "the".
    """
    raw = re.findall(r"[A-Za-z][A-Za-z0-9_-]+", heading.lower())
    return [t for t in raw if len(t) >= 4 and t not in _STOPWORDS]


_STOPWORDS: Final = frozenset(
    {
        "with",
        "from",
        "into",
        "your",
        "this",
        "that",
        "what",
        "when",
        "where",
        "which",
        "while",
        "about",
        "using",
        "running",
        "guide",
        "overview",
    }
)


_ID_SAFE_RE: Final = re.compile(r"[^a-zA-Z0-9_:.-]+")


def _make_id(key: str) -> str:
    return _ID_SAFE_RE.sub("-", key).strip("-").lower()


__all__ = (
    "DocFile",
    "DocsOutlineResult",
    "build_docs_outline",
)
