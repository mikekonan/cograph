from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from backend.app.graph.extractor import ExtractedNode

logger = logging.getLogger(__name__)

_BLAME_HEADER_PATTERN = re.compile(r"^([0-9a-fA-F]{40}) (\d+) (\d+) (\d+)$")


@dataclass(slots=True, kw_only=True, frozen=True)
class NodeTemporalMetadata:
    first_seen_commit: str | None = None
    last_changed_commit: str | None = None
    last_changed_at: datetime | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class _BlameSpan:
    commit_sha: str
    committed_at: datetime | None


def collect_node_temporal_metadata(
    *,
    root_path: Path,
    file_path: Path,
    nodes: Iterable[ExtractedNode],
) -> dict[tuple[str, str], NodeTemporalMetadata]:
    node_list = list(nodes)
    if not node_list:
        return {}

    blame_by_line = _collect_blame_by_line(root_path=root_path, file_path=file_path)
    if not blame_by_line:
        return {}

    metadata: dict[tuple[str, str], NodeTemporalMetadata] = {}
    for node in node_list:
        spans = [
            blame_by_line[line_number]
            for line_number in range(node.start_line, node.end_line + 1)
            if line_number in blame_by_line
        ]
        if not spans:
            continue

        oldest = _pick_oldest_span(spans)
        newest = _pick_newest_span(spans)
        metadata[(node.file_path, node.symbol_key or node.qualified_name)] = NodeTemporalMetadata(
            first_seen_commit=oldest.commit_sha if oldest is not None else None,
            last_changed_commit=newest.commit_sha if newest is not None else None,
            last_changed_at=newest.committed_at if newest is not None else None,
        )
    return metadata


def _collect_blame_by_line(*, root_path: Path, file_path: Path) -> dict[int, _BlameSpan]:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root_path),
                "blame",
                "--incremental",
                "--follow",
                "--",
                file_path.as_posix(),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "git_blame_failed",
            extra={
                "repo": str(root_path),
                "file_path": file_path.as_posix(),
                "returncode": exc.returncode,
                "stderr": exc.stderr[:500] if exc.stderr else "",
            },
        )
        return {}
    except subprocess.TimeoutExpired:
        logger.warning(
            "git_blame_timeout",
            extra={"repo": str(root_path), "file_path": file_path.as_posix()},
        )
        return {}
    except FileNotFoundError:
        logger.warning(
            "git_blame_no_git_cli",
            extra={"repo": str(root_path), "file_path": file_path.as_posix()},
        )
        return {}

    blame_by_line: dict[int, _BlameSpan] = {}
    committed_at_by_sha: dict[str, datetime | None] = {}

    current_sha: str | None = None
    current_final_line = 0
    current_num_lines = 0
    current_time_raw: int | None = None
    current_tz_raw: str | None = None

    for raw_line in completed.stdout.splitlines():
        header = _BLAME_HEADER_PATTERN.match(raw_line)
        if header:
            current_sha = header.group(1)
            current_final_line = int(header.group(3))
            current_num_lines = int(header.group(4))
            current_time_raw = None
            current_tz_raw = None
            continue

        if current_sha is None:
            continue

        if raw_line.startswith("committer-time "):
            current_time_raw = int(raw_line.removeprefix("committer-time ").strip())
            continue
        if raw_line.startswith("committer-tz "):
            current_tz_raw = raw_line.removeprefix("committer-tz ").strip()
            continue
        if raw_line.startswith("author-time ") and current_time_raw is None:
            current_time_raw = int(raw_line.removeprefix("author-time ").strip())
            continue
        if raw_line.startswith("author-tz ") and current_tz_raw is None:
            current_tz_raw = raw_line.removeprefix("author-tz ").strip()
            continue
        if not raw_line.startswith("filename "):
            continue

        if current_time_raw is not None and current_tz_raw is not None:
            committed_at_by_sha[current_sha] = _timestamp_to_datetime(
                epoch_seconds=current_time_raw,
            )
        committed_at = committed_at_by_sha.get(current_sha)
        span = _BlameSpan(commit_sha=current_sha, committed_at=committed_at)
        for line_number in range(current_final_line, current_final_line + current_num_lines):
            blame_by_line[line_number] = span

    return blame_by_line


def _timestamp_to_datetime(*, epoch_seconds: int) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC)


def _pick_newest_span(spans: list[_BlameSpan]) -> _BlameSpan | None:
    dated = [span for span in spans if span.committed_at is not None]
    if dated:
        return max(dated, key=lambda span: (span.committed_at, span.commit_sha))
    return spans[-1] if spans else None


def _pick_oldest_span(spans: list[_BlameSpan]) -> _BlameSpan | None:
    dated = [span for span in spans if span.committed_at is not None]
    if dated:
        return min(dated, key=lambda span: (span.committed_at, span.commit_sha))
    return spans[0] if spans else None
