"""`VerifiedEvidenceLedger` — per-page write-only log of every successful
agent tool call.

The Stage 4 dispatcher owns one ledger per page; every successful tool
call is converted into one or more `EvidenceRecord`s via
`extract_evidence(tool_name, payload, result)` and appended via
`ledger.record(...)`. The ledger never gates the agent — it just
collects, dedupes, and exposes verified-by-tool sets the downstream
citation gate (T3) and coverage gate (T4) treat as the canonical truth.

Public surface:

    ledger = VerifiedEvidenceLedger()
    ledger.record(record)
    ledger.verified_node_qns  -> set[str]
    ledger.verified_file_paths -> set[str]
    ledger.verified_doc_paths  -> set[str]
    ledger.record_by_id(record_id) -> EvidenceRecord | None
    ledger.compact_pack(max_records=40, max_tokens=3000) -> str
    extract_evidence(tool_name, payload, result) -> list[EvidenceRecord]

`compact_pack` is what the T3 repair prompt embeds — a plain-text dump
("VERIFIED EVIDENCE\n----\n[ev-001] node:cmd/tool/main.go::main ...")
that the writer can re-read when it ships an unverified citation. Token
budget is enforced via `_estimate_tokens` (chars ÷ 4 — accurate enough
for a budget knob; we don't need true tiktoken here).

Pure dataclass-y collector. No I/O. No DB. No LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.app.wiki.schemas import EvidenceRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_DEFAULT_MAX_RECORDS = 40
_DEFAULT_MAX_TOKENS = 3000
# Coarse char→token estimate. Real tokenization varies, but the citation
# repair prompt is the only consumer and an over-budget pack costs at
# most a few hundred extra tokens — well under the model context window.
_CHARS_PER_TOKEN = 4
_SNIPPET_CAP = 320


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class VerifiedEvidenceLedger:
    """Append-only log of evidence verified by tool calls.

    Records are deduplicated by `record_id` — calling `record()` twice
    with the same id is a no-op (the agent often re-reads the same node
    across turns; the second read is not new evidence). Records keep
    insertion order so `compact_pack` lists them in the order the agent
    actually fetched them, which matters for repair prompts (the LLM
    often references "the function I just looked up").
    """

    records: list[EvidenceRecord] = field(default_factory=list)
    _by_id: dict[str, int] = field(default_factory=dict)

    def record(self, evidence: EvidenceRecord) -> None:
        """Append an evidence record. Idempotent on `record_id`."""
        if evidence.record_id in self._by_id:
            return
        self._by_id[evidence.record_id] = len(self.records)
        self.records.append(evidence)

    def record_by_id(self, record_id: str) -> EvidenceRecord | None:
        idx = self._by_id.get(record_id)
        if idx is None:
            return None
        return self.records[idx]

    @property
    def verified_node_qns(self) -> set[str]:
        return {r.qn for r in self.records if r.source == "code_node" and r.qn}

    @property
    def verified_file_paths(self) -> set[str]:
        return {r.file_path for r in self.records if r.source == "file" and r.file_path}

    @property
    def verified_doc_paths(self) -> set[str]:
        return {r.file_path for r in self.records if r.source == "doc" and r.file_path}

    def __len__(self) -> int:
        return len(self.records)

    def compact_pack(
        self,
        *,
        max_records: int = _DEFAULT_MAX_RECORDS,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> str:
        """Render the ledger as a plain-text block for the T3 repair prompt.

        Format (one record per block, separated by blank lines):

            [ev-001] node:cmd/tool/main.go::main
              file: cmd/tool/main.go:10-42
              snippet: func main() { ... }

            [ev-002] doc:docs/USAGE.md
              snippet: ## Quick start
                Run `tool generate`.

        Truncation:
        - Hard-stop at `max_records` (most recent kept) so a chatty agent
          doesn't blow the prompt budget.
        - Snippets capped at `_SNIPPET_CAP` chars per record.
        - If the running token estimate exceeds `max_tokens`, stop adding
          new records and append `... [N more truncated]` so the LLM
          sees something is missing rather than silently losing context.
        """
        if not self.records:
            return "(no verified evidence — agent shipped page without any tool calls)"

        # Take the most recent `max_records` so the repair pass focuses on
        # the latest context (the agent often shipped a draft right after
        # its final reads — those are the most relevant).
        sliced = (
            self.records[-max_records:]
            if len(self.records) > max_records
            else list(self.records)
        )
        skipped = len(self.records) - len(sliced)

        out: list[str] = []
        running_chars = 0
        truncated_at: int | None = None
        for idx, rec in enumerate(sliced):
            block = _format_record(rec)
            running_chars += len(block) + 2  # +2 for separator newlines
            if running_chars // _CHARS_PER_TOKEN > max_tokens:
                truncated_at = idx
                break
            out.append(block)

        body = "\n\n".join(out)
        suffix_parts: list[str] = []
        if skipped > 0:
            suffix_parts.append(f"... [{skipped} earlier records elided]")
        if truncated_at is not None:
            remaining = len(sliced) - truncated_at
            suffix_parts.append(f"... [{remaining} more records truncated for budget]")
        if suffix_parts:
            body = body + "\n\n" + "\n".join(suffix_parts)
        return body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_record(rec: EvidenceRecord) -> str:
    """Render one record. Public-facing — the citation gate's repair
    prompt reads this verbatim, so the format is part of T3's contract."""
    header_parts = [f"[{rec.record_id}]"]
    if rec.source == "code_node":
        header_parts.append(f"node:{rec.qn or '<unknown>'}")
    elif rec.source == "doc":
        header_parts.append(f"doc:{rec.file_path or '<unknown>'}")
    else:
        header_parts.append(f"file:{rec.file_path or '<unknown>'}")

    lines = [" ".join(header_parts)]
    if rec.file_path and rec.source == "code_node":
        location = rec.file_path
        if rec.start_line is not None and rec.end_line is not None:
            location = f"{rec.file_path}:{rec.start_line}-{rec.end_line}"
        lines.append(f"  file: {location}")
    snippet = (rec.snippet or "").strip()
    if snippet:
        if len(snippet) > _SNIPPET_CAP:
            snippet = snippet[:_SNIPPET_CAP] + "…"
        snippet = "\n    ".join(snippet.splitlines())
        lines.append(f"  snippet: {snippet}")
    return "\n".join(lines)


def _truncate_snippet(text: str | None, *, cap: int = _SNIPPET_CAP) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= cap:
        return text
    return text[:cap] + "…"


def _opt_int(value: Any) -> int | None:
    """Coerce to a strictly positive int. Returns None for missing,
    non-numeric, or <= 0 values — wire payloads use 0 as "unknown" for
    line numbers."""
    if value is None:
        return None
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return None
    if ivalue <= 0:
        return None
    return ivalue


def _coerce_int(value: Any) -> int | None:
    """Coerce to an int, allowing 0 — used for fields like `chunk_index`
    where 0 is a valid value and only "missing" should map to None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Tool-result → evidence extraction
# ---------------------------------------------------------------------------


def extract_evidence(
    tool_name: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> list[EvidenceRecord]:
    """Map a successful tool call into 0..N evidence records.

    The dispatcher calls this once per successful call. Errors (`{"error": ...}`)
    are filtered out before we get here. Unknown tools / unexpected
    shapes return an empty list rather than raising — extraction must
    never break the agent loop.
    """
    if not isinstance(result, dict) or "error" in result:
        return []
    extractor = _EXTRACTORS.get(tool_name)
    if extractor is None:
        return []
    try:
        return extractor(payload or {}, result)
    except Exception:  # pragma: no cover — defensive; never crash the loop
        return []


def _extract_read_node_by_qn(
    payload: dict[str, Any], result: dict[str, Any]
) -> list[EvidenceRecord]:
    if not result.get("found"):
        return []
    qn = _opt_str(result.get("qualified_name") or payload.get("qualified_name"))
    if qn is None:
        return []
    file_path = _opt_str(result.get("file_path"))
    start = _opt_int(result.get("start_line"))
    end = _opt_int(result.get("end_line"))
    snippet = _truncate_snippet(
        result.get("snippet") or result.get("signature") or result.get("summary")
    )
    return [
        EvidenceRecord(
            record_id=_node_record_id(qn),
            source="code_node",
            qn=qn,
            file_path=file_path,
            start_line=start,
            end_line=end,
            snippet=snippet,
        )
    ]


def _extract_find_by_name(
    payload: dict[str, Any], result: dict[str, Any]
) -> list[EvidenceRecord]:
    return _records_from_candidate_list(result.get("candidates"))


def _extract_search_code(
    payload: dict[str, Any], result: dict[str, Any]
) -> list[EvidenceRecord]:
    return _records_from_candidate_list(result.get("results"))


def _records_from_candidate_list(items: Any) -> list[EvidenceRecord]:
    if not isinstance(items, list):
        return []
    out: list[EvidenceRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qn = _opt_str(item.get("qualified_name"))
        if qn is None:
            continue
        out.append(
            EvidenceRecord(
                record_id=_node_record_id(qn),
                source="code_node",
                qn=qn,
                file_path=_opt_str(item.get("file_path")),
                start_line=_opt_int(item.get("start_line")),
                end_line=_opt_int(item.get("end_line")),
                snippet=_truncate_snippet(item.get("snippet")),
            )
        )
    return out


def _extract_list_children(
    payload: dict[str, Any], result: dict[str, Any]
) -> list[EvidenceRecord]:
    if not result.get("found"):
        return []
    parent_qn = _opt_str(result.get("qualified_name"))
    out: list[EvidenceRecord] = []
    if parent_qn is not None:
        # Parent itself is verified: list_children only returns children
        # when the parent exists in the graph.
        out.append(
            EvidenceRecord(
                record_id=_node_record_id(parent_qn),
                source="code_node",
                qn=parent_qn,
                file_path=None,
                start_line=None,
                end_line=None,
                snippet="",
            )
        )
    children = result.get("children") or []
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, dict):
                continue
            child_qn = _opt_str(child.get("qualified_name"))
            if child_qn is None:
                continue
            out.append(
                EvidenceRecord(
                    record_id=_node_record_id(child_qn),
                    source="code_node",
                    qn=child_qn,
                    file_path=_opt_str(child.get("file_path")),
                    start_line=_opt_int(child.get("start_line")),
                    end_line=_opt_int(child.get("end_line")),
                    snippet=_truncate_snippet(
                        child.get("signature") or child.get("snippet")
                    ),
                )
            )
    return out


def _extract_list_by_file(
    payload: dict[str, Any], result: dict[str, Any]
) -> list[EvidenceRecord]:
    nodes = result.get("nodes") or []
    if not isinstance(nodes, list):
        return []
    out: list[EvidenceRecord] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        qn = _opt_str(node.get("qualified_name"))
        if qn is None:
            continue
        out.append(
            EvidenceRecord(
                record_id=_node_record_id(qn),
                source="code_node",
                qn=qn,
                file_path=_opt_str(node.get("file_path")),
                start_line=_opt_int(node.get("start_line")),
                end_line=_opt_int(node.get("end_line")),
                snippet=_truncate_snippet(node.get("signature") or node.get("snippet")),
            )
        )
    return out


def _extract_get_neighbors(
    payload: dict[str, Any], result: dict[str, Any]
) -> list[EvidenceRecord]:
    if not result.get("found"):
        return []
    out: list[EvidenceRecord] = []
    seed_qn = _opt_str(result.get("qualified_name"))
    if seed_qn is not None:
        out.append(
            EvidenceRecord(
                record_id=_node_record_id(seed_qn),
                source="code_node",
                qn=seed_qn,
                file_path=None,
                start_line=None,
                end_line=None,
                snippet="",
            )
        )
    for bucket in ("callers", "callees", "contains"):
        items = result.get(bucket) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            qn = _opt_str(item.get("qualified_name"))
            if qn is None:
                continue
            out.append(
                EvidenceRecord(
                    record_id=_node_record_id(qn),
                    source="code_node",
                    qn=qn,
                    file_path=_opt_str(item.get("file_path")),
                    start_line=_opt_int(item.get("start_line")),
                    end_line=_opt_int(item.get("end_line")),
                    snippet=_truncate_snippet(item.get("signature")),
                )
            )
    return out


def _extract_search_docs(
    payload: dict[str, Any], result: dict[str, Any]
) -> list[EvidenceRecord]:
    items = result.get("results") or []
    if not isinstance(items, list):
        return []
    out: list[EvidenceRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = _opt_str(item.get("file_path"))
        if path is None:
            continue
        chunk_idx = _coerce_int(item.get("chunk_index"))
        out.append(
            EvidenceRecord(
                record_id=_doc_record_id(path, chunk_idx),
                source="doc",
                qn=None,
                file_path=path,
                start_line=None,
                end_line=None,
                snippet=_truncate_snippet(item.get("snippet")),
            )
        )
    return out


def _extract_read_file(
    payload: dict[str, Any], result: dict[str, Any]
) -> list[EvidenceRecord]:
    path = _opt_str(result.get("path") or payload.get("path"))
    if path is None:
        return []
    start = _opt_int(result.get("start_line") or payload.get("offset"))
    end = _opt_int(result.get("end_line"))
    snippet = _truncate_snippet(result.get("content") or result.get("text"))
    return [
        EvidenceRecord(
            record_id=_file_record_id(path, start, end),
            source="file",
            qn=None,
            file_path=path,
            start_line=start,
            end_line=end,
            snippet=snippet,
        )
    ]


def _extract_grep(
    payload: dict[str, Any], result: dict[str, Any]
) -> list[EvidenceRecord]:
    matches = result.get("matches") or result.get("results") or []
    if not isinstance(matches, list):
        return []
    out: list[EvidenceRecord] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        path = _opt_str(match.get("path") or match.get("file_path"))
        if path is None:
            continue
        line_no = _opt_int(match.get("line") or match.get("line_number"))
        snippet = _truncate_snippet(match.get("text") or match.get("snippet"))
        out.append(
            EvidenceRecord(
                record_id=_file_record_id(path, line_no, line_no),
                source="file",
                qn=None,
                file_path=path,
                start_line=line_no,
                end_line=line_no,
                snippet=snippet,
            )
        )
    return out


_EXTRACTORS: dict[str, Any] = {
    "read_node_by_qn": _extract_read_node_by_qn,
    "find_by_name": _extract_find_by_name,
    "search_code": _extract_search_code,
    "list_children": _extract_list_children,
    "list_by_file": _extract_list_by_file,
    "get_neighbors": _extract_get_neighbors,
    "search_docs": _extract_search_docs,
    "read_file": _extract_read_file,
    "grep": _extract_grep,
}


def _node_record_id(qn: str) -> str:
    return f"node:{qn}"


def _doc_record_id(path: str, chunk_index: int | None) -> str:
    if chunk_index is None:
        return f"doc:{path}"
    return f"doc:{path}#{chunk_index}"


def _file_record_id(path: str, start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return f"file:{path}"
    if start == end or end is None:
        return f"file:{path}:{start}"
    return f"file:{path}:{start}-{end}"


__all__ = [
    "VerifiedEvidenceLedger",
    "extract_evidence",
]
