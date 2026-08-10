"""Atomic citation gate (T3): validate every `[[node:X]]` / `[[doc:X]]`
in a draft against the per-page `VerifiedEvidenceLedger`.

The legacy `CitationResolver.prevalidate_page` checked whether a citation
was *resolvable* against the indexed graph — that is, whether the
identifier exists in `code_nodes` / `repo_documents`. T3 raises the bar:
a citation is only valid if the agent *verified* it via a tool call.
That closes the loop where the LLM emits a citation it never grounded
("I think there's a `pkg.Validate`") that happens to exist in the graph
but was never read by the agent for this page.

Public surface:

    invalid = validate_citations(markdown, ledger)
    if invalid:
        cleaned = strip_invalid_citations(markdown, invalid)

`validate_citations` returns one `InvalidCitation` per invalid placeholder
location so the repair prompt can list exact failures (citation gate
language: "Failed citations: [[node:foo.Bar]] — not in verified set").

`strip_invalid_citations` is the final-attempt fallback: convert each
invalid `[[node:X]]` to `` `X` `` (inline-code, no link) and each
`[[doc:Y]]` to `Y` so the page still ships at quality_status=degraded
without unresolved-marker noise.

Pure / deterministic. No I/O. No LLM. The ledger does the verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from backend.app.wiki.citations import PLACEHOLDER_RE
from backend.app.wiki.evidence_ledger import VerifiedEvidenceLedger


@dataclass(frozen=True, slots=True)
class InvalidCitation:
    """One placeholder that the gate rejected.

    `kind` and `value` mirror the placeholder syntax (`[[kind:value]]`).
    `position` is the byte offset of the `[[` in the source markdown so
    a downstream repairer can show the LLM exactly where it failed.
    `reason` is a stable machine-readable code (currently only
    `not_in_ledger`) for telemetry; the human-facing message is built by
    the prompt.
    """

    kind: Literal["node", "doc"]
    value: str
    position: int
    reason: Literal["not_in_ledger"] = "not_in_ledger"

    @property
    def placeholder(self) -> str:
        return f"[[{self.kind}:{self.value}]]"


def validate_citations(
    markdown: str,
    ledger: VerifiedEvidenceLedger,
    *,
    doc_path_match: Literal["exact", "prefix"] = "prefix",
) -> list[InvalidCitation]:
    """Return one entry per invalid placeholder in `markdown`.

    Rules:
      - `[[node:X]]` is valid iff `X in ledger.verified_node_qns`.
      - `[[doc:Y]]` is valid iff the file path part of `Y` (with `#anchor`
        stripped) matches a `ledger.verified_doc_paths` entry. Default
        match is *prefix* (the agent searched `docs/USAGE.md` once; a
        page-time citation `[[doc:docs/USAGE.md#quickstart]]` is fine).
        Pass `doc_path_match="exact"` to require exact equality.
      - Unknown kinds and empty values don't match `PLACEHOLDER_RE` and
        are not seen here; they pass through to the resolver / renderer
        which is the correct layer for those structural failures.

    The same invalid placeholder appearing twice on a page yields two
    entries (one per location) so the repair prompt can show the writer
    where it slipped up. Order matches the order the placeholders appear
    in the source so the LLM reads them top-to-bottom.
    """
    if not markdown:
        return []

    verified_nodes = ledger.verified_node_qns
    verified_docs = ledger.verified_doc_paths

    invalid: list[InvalidCitation] = []
    for match in PLACEHOLDER_RE.finditer(markdown):
        kind = match.group(1)
        value = match.group(2).strip()
        if not value:
            # Should never happen given the placeholder regex requires
            # at least one non-`]` char, but stay defensive.
            continue
        if kind == "node":
            if value not in verified_nodes:
                invalid.append(
                    InvalidCitation(
                        kind="node",
                        value=value,
                        position=match.start(),
                    )
                )
        elif kind == "doc":
            path = _strip_doc_anchor(value)
            if not _doc_path_verified(path, verified_docs, mode=doc_path_match):
                invalid.append(
                    InvalidCitation(
                        kind="doc",
                        value=value,
                        position=match.start(),
                    )
                )
    return invalid


def strip_invalid_citations(markdown: str, invalid: list[InvalidCitation]) -> str:
    """Replace invalid placeholders with safe plain-text fallbacks.

    Used as the final fallback (3rd repair pass exhausted): the page
    still ships at `quality_status=degraded` so the FE can chip-warn the
    reader, but it doesn't carry a `⚠️ unresolved:` marker for these
    citations because the agent claim was never grounded — silently
    falling back to inline-code is preferable to advertising a broken
    link the writer never could have honored.

    Replacement strategy:
      - `[[node:foo.Bar]]` → `` `foo.Bar` ``
      - `[[doc:docs/foo.md]]` → `docs/foo.md`
      - `[[doc:docs/foo.md#quickstart]]` → `docs/foo.md#quickstart`
        (anchor preserved as plain text — it's still useful context)
    """
    if not invalid:
        return markdown
    invalid_keys = {(c.kind, c.value) for c in invalid}

    def _replace(match: re.Match[str]) -> str:
        kind = match.group(1)
        value = match.group(2).strip()
        if (kind, value) not in invalid_keys:
            return match.group(0)
        if kind == "node":
            return f"`{value}`" if value else ""
        return value

    return PLACEHOLDER_RE.sub(_replace, markdown)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _strip_doc_anchor(value: str) -> str:
    """Mirror `citations._strip_doc_anchor` — return the path part only."""
    head, _, _ = value.partition("#")
    return head.strip().lstrip("./")


def _doc_path_verified(
    path: str,
    verified_docs: set[str],
    *,
    mode: Literal["exact", "prefix"],
) -> bool:
    if not path:
        return False
    if mode == "exact":
        return path in verified_docs
    # `prefix` mode: a verified `docs/USAGE.md` covers a citation to
    # `docs/USAGE.md` exactly OR via `#anchor` (anchor already stripped).
    # We don't allow real prefix-of-different-file matching — verifying
    # `docs/USAGE.md` should not validate `[[doc:docs/USAGE.md.bak]]`.
    return path in verified_docs


__all__ = (
    "InvalidCitation",
    "strip_invalid_citations",
    "validate_citations",
)
