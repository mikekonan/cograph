"""Sandboxed filesystem access for the wiki agent.

The wiki writer agent needs to read source files, grep across the checkout,
and list files when it spots a relevant path that wasn't in the retrieval
bundle. This module provides those three primitives with hard guarantees
that match the agent's threat model:

  - **Path containment** — every resolved path MUST stay inside the
    checkout root. Symlinks that point outside the root are rejected.
    `..` traversal is rejected by `Path.resolve` + the `is_relative_to`
    check.
  - **Content size cap** — files larger than `_MAX_BYTES_READ` are
    truncated. This bounds the per-tool token cost.
  - **Binary detection** — files with NUL bytes in the first 8 KiB are
    flagged binary and refused. The agent's downstream is markdown text;
    pulling bytes out of a `.bin` would just waste a turn.
  - **Argv-only `grep`** — patterns flow to ripgrep via argv. No `shell=True`
    anywhere; the pattern is a literal regex argument, not a command
    fragment.

The functions return JSON-serialisable dicts; the dispatcher (`agent_dispatcher.py`)
wraps them in error envelopes if anything raises.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Caps. These are not exposed as tool parameters so the agent can't accidentally
# blow them up by passing huge values — the prompt asks for offsets/limits but
# the dispatcher clamps to these ceilings.
_MAX_BYTES_READ = 20_000
_MAX_LINES_READ = 400
_MAX_FILES_LISTED = 200
_MAX_GREP_MATCHES = 100
_GREP_TIMEOUT_S = 10.0
_BINARY_SNIFF_BYTES = 8_192


class CheckoutFsError(RuntimeError):
    """Raised when a checkout-fs operation fails for an actionable reason
    (path traversal, binary file, missing path, oversize input). The
    dispatcher converts these into `{"error": ...}` envelopes for the LLM.
    """


@dataclass(slots=True, frozen=True)
class CheckoutFs:
    """Path-scoped filesystem facade.

    Every method resolves the input path against `root` and refuses to
    operate outside it. Hold onto one of these per agent loop so the
    `root` directory is fixed for the page's lifetime.
    """

    root: Path

    def _resolve(self, raw: str) -> Path:
        if not raw or not isinstance(raw, str):
            raise CheckoutFsError("path must be a non-empty string")
        # `Path.resolve()` collapses `..` and normalises symlinks. We
        # reject anything that escapes `root` post-resolve. `strict=False`
        # so the path can be missing — `read_file`/`grep`/`list_files`
        # surface the "not found" via a different error.
        candidate = (self.root / raw).resolve(strict=False)
        try:
            candidate.relative_to(self.root.resolve(strict=False))
        except ValueError as exc:
            raise CheckoutFsError(
                f"path {raw!r} escapes the checkout root"
            ) from exc
        return candidate

    def read_file(
        self, path: str, *, offset: int = 0, limit: int = _MAX_LINES_READ
    ) -> dict[str, object]:
        """Read a UTF-8 text file with line-number-aware slicing.

        `offset` is the first line to include (1-indexed; clamps to 1 on
        non-positive input). `limit` is the line cap; the dispatcher
        clamps to `_MAX_LINES_READ`. Total bytes are capped at
        `_MAX_BYTES_READ` regardless of line count — the agent gets a
        truncation flag so it knows to ask for more.
        """
        target = self._resolve(path)
        if not target.is_file():
            raise CheckoutFsError(f"not a file: {path}")
        if target.is_symlink():
            # `Path.resolve` follows the symlink, so a non-escaping symlink
            # already passed the relative_to check — but a sibling link
            # could still resolve outside root if `strict=False` masked an
            # error. Belt-and-braces.
            real = target.resolve(strict=True)
            try:
                real.relative_to(self.root.resolve(strict=False))
            except ValueError as exc:
                raise CheckoutFsError(
                    f"symlink {path!r} resolves outside checkout root"
                ) from exc

        # Sniff for binary content first — saves us a full read for
        # large compiled artefacts. Read up to N bytes and look for NUL.
        with target.open("rb") as fh:
            head = fh.read(_BINARY_SNIFF_BYTES)
        if b"\x00" in head:
            raise CheckoutFsError(f"refusing to read binary file: {path}")

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise CheckoutFsError(f"cannot read {path}: {exc}") from exc

        clamped_offset = max(1, int(offset or 1))
        clamped_limit = max(1, min(int(limit or _MAX_LINES_READ), _MAX_LINES_READ))
        all_lines = text.splitlines()
        total_lines = len(all_lines)
        start_idx = clamped_offset - 1
        end_idx = min(start_idx + clamped_limit, total_lines)
        sliced = all_lines[start_idx:end_idx]
        body = "\n".join(sliced)
        truncated = False
        if len(body.encode("utf-8")) > _MAX_BYTES_READ:
            # Truncate the slice to the byte cap. Encoding round-trip is
            # cheap relative to the LLM call that's about to consume this.
            body_bytes = body.encode("utf-8")[:_MAX_BYTES_READ]
            body = body_bytes.decode("utf-8", errors="ignore")
            truncated = True

        return {
            "path": str(target.relative_to(self.root.resolve(strict=False))),
            "start_line": clamped_offset,
            "end_line": min(end_idx, total_lines),
            "total_lines": total_lines,
            "body": body,
            "truncated": truncated,
        }

    def list_files(
        self, glob: str = "**/*", *, cap: int = _MAX_FILES_LISTED
    ) -> dict[str, object]:
        """Enumerate paths under the checkout matching `glob`.

        Forbids absolute and rooted globs (a leading `/` would let the
        agent point at host paths via the `Path.glob` semantics). Hidden
        directories at the top level (`.git`, `.cograph`) are skipped
        because they're never page content.
        """
        if not isinstance(glob, str) or not glob.strip():
            glob = "**/*"
        if glob.startswith("/"):
            raise CheckoutFsError(f"glob must be relative: {glob!r}")

        matches: list[str] = []
        try:
            for entry in self.root.glob(glob):
                if entry.is_dir():
                    continue
                rel = entry.relative_to(self.root)
                if rel.parts and rel.parts[0] in {".git", ".cograph"}:
                    continue
                matches.append(str(rel))
                if len(matches) >= cap:
                    break
        except (OSError, ValueError) as exc:
            raise CheckoutFsError(
                f"glob {glob!r} failed: {exc}"
            ) from exc
        matches.sort()
        return {
            "glob": glob,
            "matches": matches,
            "truncated": len(matches) >= cap,
        }

    async def grep(
        self,
        pattern: str,
        *,
        glob: str | None = None,
        cap: int = _MAX_GREP_MATCHES,
    ) -> dict[str, object]:
        """Search the checkout for `pattern` using ripgrep when available,
        otherwise a Python fallback.

        ripgrep is invoked via argv (never shell=True): `rg --json -n
        --max-count=N -- pattern [glob]`. The fallback walks the tree and
        uses `re.search` per line — slower but identical contract. Either
        way, output is `{"pattern", "matches": [{path, line, text}], "truncated"}`.
        """
        if not isinstance(pattern, str) or not pattern:
            raise CheckoutFsError("pattern must be a non-empty string")

        rg = shutil.which("rg")
        if rg is not None:
            return await self._grep_with_rg(rg, pattern, glob=glob, cap=cap)
        return await asyncio.to_thread(
            self._grep_python, pattern, glob, cap
        )

    async def _grep_with_rg(
        self,
        rg_path: str,
        pattern: str,
        *,
        glob: str | None,
        cap: int,
    ) -> dict[str, object]:
        argv: list[str] = [
            rg_path,
            "--json",
            "-n",
            "--no-messages",
            f"--max-count={cap}",
            "--threads=1",
        ]
        if glob:
            argv.extend(["-g", glob])
        argv.extend(["-e", pattern, "."])
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self.root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise CheckoutFsError(f"failed to invoke ripgrep: {exc}") from exc
        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_GREP_TIMEOUT_S
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise CheckoutFsError(
                f"ripgrep timed out after {_GREP_TIMEOUT_S}s"
            ) from exc

        matches: list[dict[str, object]] = []
        for raw_line in stdout.splitlines():
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data") or {}
            path = (data.get("path") or {}).get("text", "")
            line_no = int(data.get("line_number") or 0)
            text_obj = data.get("lines") or {}
            text = text_obj.get("text", "").rstrip("\n")
            matches.append({"path": path, "line": line_no, "text": text})
            if len(matches) >= cap:
                break
        return {
            "pattern": pattern,
            "matches": matches,
            "truncated": len(matches) >= cap,
        }

    def _grep_python(
        self, pattern: str, glob: str | None, cap: int
    ) -> dict[str, object]:
        import re as _re

        try:
            regex = _re.compile(pattern)
        except _re.error as exc:
            raise CheckoutFsError(f"invalid regex {pattern!r}: {exc}") from exc

        targets: list[Path] = []
        glob_pat = glob or "**/*"
        if glob_pat.startswith("/"):
            raise CheckoutFsError(f"glob must be relative: {glob_pat!r}")
        for entry in self.root.glob(glob_pat):
            if not entry.is_file():
                continue
            rel = entry.relative_to(self.root)
            if rel.parts and rel.parts[0] in {".git", ".cograph"}:
                continue
            targets.append(entry)

        matches: list[dict[str, object]] = []
        for entry in targets:
            try:
                # NUL sniff so we don't dump a binary file's regex hits.
                with entry.open("rb") as fh:
                    head = fh.read(_BINARY_SNIFF_BYTES)
                if b"\x00" in head:
                    continue
                with entry.open("r", encoding="utf-8", errors="replace") as fh:
                    for line_no, line in enumerate(fh, start=1):
                        if regex.search(line):
                            matches.append(
                                {
                                    "path": str(entry.relative_to(self.root)),
                                    "line": line_no,
                                    "text": line.rstrip("\n"),
                                }
                            )
                            if len(matches) >= cap:
                                return {
                                    "pattern": pattern,
                                    "matches": matches,
                                    "truncated": True,
                                }
            except OSError:
                continue
        return {
            "pattern": pattern,
            "matches": matches,
            "truncated": False,
        }


__all__ = ["CheckoutFs", "CheckoutFsError"]
