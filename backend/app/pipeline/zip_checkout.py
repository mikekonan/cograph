"""Zip-archive ingest adapter.

Mirror of `GitCheckoutAdapter` for repos sourced from a user-uploaded
zip rather than a git remote. Two operations:

  - `persist_upload` — stream an inbound HTTP body into
    `<checkouts_root>/<repository_id>.zip`, validating compressed size
    on the way. Returns a sha256 digest of the bytes for use as a
    deterministic `last_commit` placeholder.

  - `prepare_checkout` — extract the persisted zip into
    `<checkouts_root>/<repository_id>/`, applying zip-bomb guards
    (decompressed cap, per-file cap, inflation ratio, entry count) and
    path-traversal / symlink guards. Idempotent: a second call wipes
    the previous extraction first so re-snapshots are deterministic.

The orchestrator picks one adapter based on `Repository.source`.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator
from uuid import UUID

import pathspec


class ZipCheckoutError(Exception):
    """Raised for any zip ingest / extraction failure surfaced to the API."""


_DEFAULT_STREAM_CHUNK = 1 * 1024 * 1024  # 1 MiB


@dataclass(slots=True, kw_only=True)
class PersistedUpload:
    archive_path: Path
    bytes_written: int
    sha256: str


@dataclass(slots=True, kw_only=True)
class PreparedZipCheckout:
    path: Path
    archive_path: Path
    sha256: str
    file_count: int
    decompressed_bytes: int
    skipped_count: int = 0
    skipped_bytes: int = 0


# Extensions of files we never want in a code-indexing checkout. The
# adapter is fed user-uploaded zips that often contain whole git working
# trees — `.git/objects/pack/*.pack` files, images, compiled artefacts,
# fonts. None of it is indexable as source code, all of it eats the
# decompressed cap. Conservative list — `.svg` is text (XML), `.json`,
# `.yaml`, `.toml`, `.md` are text. SVG is intentionally NOT here.
_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Images
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
        ".tiff", ".tif", ".heic", ".avif",
        # Audio / video
        ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".flac", ".wav",
        ".ogg", ".m4a", ".m4v", ".webm",
        # Nested archives — never useful for an indexer
        ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar",
        ".xz", ".zst",
        # Compiled artefacts
        ".exe", ".dll", ".so", ".dylib", ".a", ".o", ".class",
        ".jar", ".war", ".ear", ".pyc", ".pyo", ".pyd", ".wasm",
        ".whl", ".gem", ".node", ".pdb",
        # Scientific / numeric blobs
        ".npy", ".npz", ".h5", ".hdf5", ".parquet", ".arrow",
        ".feather", ".pkl",
        # Databases
        ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb",
        # Fonts
        ".ttf", ".otf", ".woff", ".woff2", ".eot",
        # Design / vector binaries
        ".psd", ".ai", ".sketch", ".fig", ".blend", ".xcf",
        # Office docs (binary or zipped binary)
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf",
        # Disk images / packaging
        ".iso", ".dmg", ".deb", ".rpm", ".apk", ".ipa", ".msi", ".cab",
    }
)


# Top-level directories that are pure tooling noise — never indexable
# as repo source, always safe to drop wholesale.
_SKIPPED_TOP_LEVEL_DIRS: frozenset[str] = frozenset(
    {
        ".git",  # git plumbing (objects/pack, refs, index)
        ".idea",  # JetBrains IDE workspace files
    }
)


def _should_skip_entry(rel: str) -> bool:
    """True for entries the adapter MUST NOT extract.

    Skip rules:
      - anything under a `_SKIPPED_TOP_LEVEL_DIRS` entry (`.git/`,
        `.idea/`) — IDE / VCS plumbing, large + binary + never
        indexable
      - `.DS_Store` and `Thumbs.db` (OS junk)
      - files whose extension is in `_BINARY_EXTENSIONS`

    Skipped entries do NOT count against the decompressed-total or
    per-file caps — the caller treats them as "as if missing".

    Gitignore matching is layered on top of this in the extraction
    loop via `_build_gitignore_matchers` / `_is_gitignored` — that
    pass is data-driven from `.gitignore` files in the archive.
    """
    parts = rel.split("/")
    if parts and parts[0] in _SKIPPED_TOP_LEVEL_DIRS:
        return True
    name = parts[-1].lower()
    if name in ("", ".ds_store", "thumbs.db"):
        return True
    dot = name.rfind(".")
    if dot < 0:
        return False
    return name[dot:] in _BINARY_EXTENSIONS


def _build_gitignore_matchers(
    zf: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    strip_prefix: str | None,
) -> list[tuple[str, pathspec.PathSpec]]:
    """Locate every `.gitignore` in the archive and compile each into a
    `PathSpec` keyed by its directory (relative to the strip-prefixed
    repo root; `""` for the root gitignore).

    Returned list is sorted root-first so callers iterate from the
    least-specific matcher to the most-specific. Cross-gitignore
    negation precedence isn't fully modelled — we err toward
    exclusion, which matches the user's intent ("don't analyse what
    git would ignore").
    """
    matchers: list[tuple[str, pathspec.PathSpec]] = []
    for info in infos:
        if info.is_dir():
            continue
        try:
            rel = _safe_relpath(info.filename, strip_prefix)
        except ZipCheckoutError:
            continue
        if rel is None:
            continue
        if rel != ".gitignore" and not rel.endswith("/.gitignore"):
            continue
        try:
            with zf.open(info) as fh:
                content = fh.read().decode("utf-8", errors="replace")
        except (KeyError, zipfile.BadZipFile):
            continue
        gi_dir = rel[: -len(".gitignore")].rstrip("/")
        try:
            spec = pathspec.PathSpec.from_lines(
                "gitignore", content.splitlines()
            )
        except Exception:
            continue
        matchers.append((gi_dir, spec))
    matchers.sort(key=lambda pair: len(pair[0]))
    return matchers


def _is_gitignored(
    rel: str, matchers: list[tuple[str, pathspec.PathSpec]]
) -> bool:
    """True when `rel` matches any in-scope `.gitignore` PathSpec.

    A matcher applies only inside its own subtree: a `.gitignore` at
    `web/` does not affect files outside `web/`. Patterns inside that
    matcher are tested against the entry's path RELATIVE to the
    gitignore directory, matching git's own semantics.
    """
    for gi_dir, spec in matchers:
        if gi_dir:
            if rel != gi_dir and not rel.startswith(gi_dir + "/"):
                continue
            sub_rel = rel[len(gi_dir) + 1 :]
        else:
            sub_rel = rel
        if not sub_rel:
            continue
        if spec.match_file(sub_rel):
            return True
    return False


class ZipCheckoutAdapter:
    def __init__(
        self,
        *,
        checkouts_root: Path,
        max_compressed_bytes: int,
        max_decompressed_bytes: int,
        max_per_file_bytes: int,
        max_inflation_ratio: float,
        max_entries: int,
    ) -> None:
        self._checkouts_root = Path(checkouts_root)
        self._max_compressed = int(max_compressed_bytes)
        self._max_decompressed = int(max_decompressed_bytes)
        self._max_per_file = int(max_per_file_bytes)
        self._max_ratio = float(max_inflation_ratio)
        self._max_entries = int(max_entries)

    # ----- public API ------------------------------------------------

    @property
    def checkouts_root(self) -> Path:
        return self._checkouts_root

    def archive_path_for(self, repository_id: UUID) -> Path:
        return self._checkouts_root / f"{repository_id}.zip"

    def checkout_path_for(self, repository_id: UUID) -> Path:
        return self._checkouts_root / str(repository_id)

    async def persist_upload(
        self,
        *,
        repository_id: UUID,
        stream: AsyncIterator[bytes],
        chunk_size: int = _DEFAULT_STREAM_CHUNK,
    ) -> PersistedUpload:
        """Write the inbound stream to disk while enforcing the compressed
        cap. Idempotent across retries — a partial write from a failed
        previous attempt is overwritten.

        The streaming + write loop runs on the calling event loop because
        the inbound `stream` (typically `UploadFile.read`) is bound to it;
        only the final ZIP integrity check is offloaded to a worker
        thread.
        """
        del chunk_size  # iterator is the source of truth on chunk shape
        self._checkouts_root.mkdir(parents=True, exist_ok=True)
        archive_path = self.archive_path_for(repository_id)
        return await self._persist_upload_loop(
            archive_path=archive_path,
            stream=stream,
        )

    async def prepare_checkout(
        self,
        *,
        repository_id: UUID,
    ) -> PreparedZipCheckout:
        """Re-extract the stored archive into the per-repo checkout dir."""
        return await asyncio.to_thread(
            self._prepare_checkout_sync,
            repository_id,
        )

    async def discard(self, *, repository_id: UUID) -> None:
        """Remove archive + extracted dir for a deleted repository."""
        await asyncio.to_thread(self._discard_sync, repository_id)

    # ----- sync impl -------------------------------------------------

    async def _persist_upload_loop(
        self,
        *,
        archive_path: Path,
        stream: AsyncIterator[bytes],
    ) -> PersistedUpload:
        digest = hashlib.sha256()
        bytes_written = 0
        tmp_path = archive_path.with_suffix(archive_path.suffix + ".part")

        try:
            with open(tmp_path, "wb") as out:
                async for chunk in stream:
                    if not chunk:
                        continue
                    bytes_written += len(chunk)
                    if bytes_written > self._max_compressed:
                        raise ZipCheckoutError(
                            f"upload exceeds compressed cap "
                            f"({self._max_compressed} bytes)"
                        )
                    digest.update(chunk)
                    out.write(chunk)
            if bytes_written == 0:
                raise ZipCheckoutError("uploaded archive is empty")

            # Validate ZIP structure before finalising — fail fast on
            # garbage uploads (truncated, wrong magic, etc). For the
            # configured 200 MB cap this runs in well under a second
            # on disk; we keep it on the event loop so it doesn't break
            # downstream session-bound code that the test client's
            # portal can be sensitive to.
            _validate_zip_integrity(tmp_path)

            tmp_path.replace(archive_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        return PersistedUpload(
            archive_path=archive_path,
            bytes_written=bytes_written,
            sha256=digest.hexdigest(),
        )

    def _prepare_checkout_sync(
        self,
        repository_id: UUID,
    ) -> PreparedZipCheckout:
        archive_path = self.archive_path_for(repository_id)
        if not archive_path.exists():
            raise ZipCheckoutError(f"archive missing for repository {repository_id}")
        target_root = self.checkout_path_for(repository_id)

        # Always wipe previous extraction so re-runs are deterministic.
        if target_root.exists():
            shutil.rmtree(target_root)
        target_root.mkdir(parents=True)

        digest = hashlib.sha256()
        with open(archive_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_DEFAULT_STREAM_CHUNK), b""):
                digest.update(chunk)

        decompressed_total = 0
        file_count = 0
        compressed_total = 0
        skipped_count = 0
        skipped_bytes = 0
        try:
            with zipfile.ZipFile(archive_path) as zf:
                infos = zf.infolist()
                if len(infos) > self._max_entries:
                    raise ZipCheckoutError(
                        f"archive has {len(infos)} entries (cap {self._max_entries})"
                    )

                # Detect a single top-level wrapper directory (the common
                # `repo-main/...` shape produced by GitHub `Download ZIP`).
                strip_prefix = _detect_strip_prefix(infos)

                # Compile every .gitignore in the archive so the
                # extraction loop drops files git would have ignored
                # (node_modules/, dist/, .venv/, target/, *.log, ...).
                gitignore_matchers = _build_gitignore_matchers(
                    zf, infos, strip_prefix
                )

                for info in infos:
                    rel = _safe_relpath(info.filename, strip_prefix)
                    if rel is None:
                        continue  # skip the wrapper dir itself
                    if not info.is_dir() and (
                        _should_skip_entry(rel)
                        or _is_gitignored(rel, gitignore_matchers)
                    ):
                        # Binary / .git plumbing / OS junk / gitignored —
                        # never written to the checkout, never counted
                        # against caps.
                        skipped_count += 1
                        skipped_bytes += info.file_size
                        continue
                    if info.file_size > self._max_per_file:
                        raise ZipCheckoutError(
                            f"entry {info.filename!r} decompresses to "
                            f"{info.file_size} bytes (per-file cap "
                            f"{self._max_per_file})"
                        )
                    decompressed_total += info.file_size
                    compressed_total += info.compress_size
                    if decompressed_total > self._max_decompressed:
                        raise ZipCheckoutError(
                            "archive decompresses past total cap "
                            f"({self._max_decompressed} bytes)"
                        )

                    target = (target_root / rel).resolve()
                    root_resolved = target_root.resolve()
                    if not _is_relative_to(target, root_resolved):
                        raise ZipCheckoutError(
                            f"entry {info.filename!r} resolves outside "
                            "the checkout root"
                        )

                    # Reject symlinks outright — they're a frequent
                    # vector for chroot escape and add no value for
                    # static indexing.
                    if _is_symlink(info):
                        raise ZipCheckoutError(
                            f"entry {info.filename!r} is a symlink "
                            "(not supported in zip uploads)"
                        )

                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue

                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as dst:
                        # Stream the entry to bound memory, but cap reads
                        # at the per-file limit so a malicious header
                        # under-reporting `file_size` cannot escape the
                        # check we did above.
                        remaining = self._max_per_file
                        while True:
                            buf = src.read(_DEFAULT_STREAM_CHUNK)
                            if not buf:
                                break
                            remaining -= len(buf)
                            if remaining < 0:
                                raise ZipCheckoutError(
                                    f"entry {info.filename!r} exceeds "
                                    "per-file cap during streaming"
                                )
                            dst.write(buf)
                    file_count += 1

                if compressed_total > 0:
                    ratio = decompressed_total / max(compressed_total, 1)
                    if ratio > self._max_ratio:
                        raise ZipCheckoutError(
                            f"archive inflation ratio {ratio:.1f}x exceeds "
                            f"cap {self._max_ratio:.1f}x (suspected zip bomb)"
                        )
        except ZipCheckoutError:
            shutil.rmtree(target_root, ignore_errors=True)
            raise
        except zipfile.BadZipFile as exc:
            shutil.rmtree(target_root, ignore_errors=True)
            raise ZipCheckoutError(f"not a valid zip archive: {exc}") from exc

        return PreparedZipCheckout(
            path=target_root,
            archive_path=archive_path,
            sha256=digest.hexdigest(),
            file_count=file_count,
            decompressed_bytes=decompressed_total,
            skipped_count=skipped_count,
            skipped_bytes=skipped_bytes,
        )

    def _discard_sync(self, repository_id: UUID) -> None:
        archive_path = self.archive_path_for(repository_id)
        archive_path.unlink(missing_ok=True)
        target_root = self.checkout_path_for(repository_id)
        if target_root.exists():
            shutil.rmtree(target_root, ignore_errors=True)


# ----- helpers -------------------------------------------------------


def _detect_strip_prefix(infos: list[zipfile.ZipInfo]) -> str | None:
    """If every entry shares one top-level directory, return it (with
    trailing slash). Otherwise return None.

    This matches the `repo-name-{sha}/...` wrapper that GitHub /
    GitLab archive downloads produce.
    """
    candidates: set[str] = set()
    for info in infos:
        name = info.filename
        if not name:
            continue
        parts = name.split("/", 1)
        if len(parts) < 2 or not parts[0]:
            return None
        # `..` or `.` are NEVER a legitimate wrapper directory — refuse
        # to treat them as a strip prefix so traversal payloads still
        # surface to `_safe_relpath` for explicit rejection.
        if parts[0] in ("..", "."):
            return None
        candidates.add(parts[0] + "/")
        if len(candidates) > 1:
            return None
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _safe_relpath(name: str, strip_prefix: str | None) -> str | None:
    """Return a sanitized relative path for the entry, or None to skip.

    Blocks absolute paths, drive letters, and `..` segments. The strip
    prefix is only removed when present.
    """
    if not name:
        return None
    if strip_prefix and name.startswith(strip_prefix):
        rel = name[len(strip_prefix) :]
    else:
        rel = name
    if not rel or rel.endswith("/"):
        # Pure directory entry — skip; we'll create dirs as needed.
        return None
    if rel.startswith("/") or rel.startswith("\\"):
        raise ZipCheckoutError(f"absolute path in archive: {name!r}")
    if ":" in rel.split("/", 1)[0]:
        raise ZipCheckoutError(f"drive-letter path in archive: {name!r}")
    parts = rel.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise ZipCheckoutError(f"path traversal segment in archive: {name!r}")
    return "/".join(part for part in parts if part not in ("", "."))


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


_SYMLINK_MODE = 0xA000


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # Unix mode is in the high 16 bits of external_attr. 0xA000 is
    # S_IFLNK — symbolic link.
    return ((info.external_attr >> 16) & 0xF000) == _SYMLINK_MODE


def _validate_zip_integrity(path: Path) -> None:
    """Run `zipfile.ZipFile.testzip` on `path`. Raises `ZipCheckoutError`
    on any structural issue. Lives at module scope so it can be offloaded
    to a worker thread without capturing self/closure."""
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
    except zipfile.BadZipFile as exc:
        raise ZipCheckoutError(f"not a valid zip archive: {exc}") from exc
    if bad is not None:
        raise ZipCheckoutError(f"corrupt zip entry: {bad}")
