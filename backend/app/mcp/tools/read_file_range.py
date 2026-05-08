"""Read a line range of a source file by path.

Wraps the SourceFile model directly so agents don't have to look up a
source_file_id first. Caps the range at 1000 lines to keep agents from
asking for whole 50K-line files this way.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
    require_ready_repository,
    resolve_readable_repository_by_slug,
)
from backend.app.models.source_file import SourceFile

_READ_FILE_RANGE_DESCRIPTION = (
    "Read a 1-indexed line range of a source file by repo slug + path.\n"
    "Use when: you have a `file_path` (typically from a `citation` returned by "
    "cograph.retrieve / cograph.search_code / cograph.read_node) and need a few "
    "dozen lines of surrounding context that a node-bounded read won't capture.\n"
    "Do NOT use to dump whole files (the range is capped at 1000 lines) or to "
    "search by content (use cograph.retrieve)."
)

MAX_LINE_RANGE = 1000


class ReadFileRangeArgs(BaseModel):
    repository: str
    path: str
    start_line: int = Field(..., ge=1, description="Inclusive 1-indexed start")
    end_line: int = Field(..., ge=1, description="Inclusive 1-indexed end")


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph.read_file_range",
        description=_READ_FILE_RANGE_DESCRIPTION,
    )
    async def read_file_range(
        repository: str,
        path: str,
        start_line: int,
        end_line: int,
        ctx: Context | None = None,
    ) -> object:
        args = ReadFileRangeArgs(
            repository=repository,
            path=path,
            start_line=start_line,
            end_line=end_line,
        )
        if args.end_line < args.start_line:
            raise ValueError(
                "INVALID_RANGE: end_line must be >= start_line"
            )
        if args.end_line - args.start_line + 1 > MAX_LINE_RANGE:
            raise ValueError(
                f"INVALID_RANGE: range exceeds {MAX_LINE_RANGE} lines"
            )

        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            repo = await resolve_readable_repository_by_slug(
                session=session,
                slug=args.repository,
                services=services,
                current_user=current_user,
            )
            await require_ready_repository(
                session=session,
                repository_id=repo.id,
            )
            source_file = await session.scalar(
                select(SourceFile).where(
                    SourceFile.repository_id == repo.id,
                    SourceFile.file_path == args.path,
                )
            )

        if source_file is None:
            raise ValueError("NOT_FOUND: Source file not found")

        try:
            full = bytes(source_file.raw_bytes).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"INVALID_RANGE: cannot decode {args.path} as UTF-8"
            ) from exc

        lines = full.splitlines()
        total_lines = len(lines)
        if args.start_line > total_lines:
            raise ValueError(
                f"INVALID_RANGE: start_line {args.start_line} > total_lines {total_lines}"
            )
        clamped_end = min(args.end_line, total_lines)
        sliced = "\n".join(lines[args.start_line - 1 : clamped_end])

        slug_path = f"{repo.host}/{repo.owner}/{repo.name}"
        return encode_payload(
            {
                "repository_slug": slug_path,
                "file_path": source_file.file_path,
                "language": source_file.language,
                "total_lines": total_lines,
                "start_line": args.start_line,
                "end_line": clamped_end,
                "content": sliced,
                "content_truncated": clamped_end < args.end_line,
            }
        )
