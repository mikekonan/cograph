"""Tests for `ZipCheckoutAdapter`.

Cover the validation surface end-to-end: happy-path round-trip, the four
zip-bomb guards (per-file, total, ratio, entries), path-traversal guards,
absolute paths, drive-letter paths, symlink rejection, and idempotent
re-extraction.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

import pytest

from backend.app.pipeline.zip_checkout import (
    ZipCheckoutAdapter,
    ZipCheckoutError,
    _is_symlink,
    _safe_relpath,
    _should_skip_entry,
)
import pathspec  # noqa: E402

from backend.app.pipeline.zip_checkout import (  # noqa: E402
    _is_gitignored,
)


# ----- helpers -------------------------------------------------------


def _make_adapter(
    *,
    checkouts_root: Path,
    max_compressed: int = 200 * 1024 * 1024,
    max_decompressed: int = 1024 * 1024 * 1024,
    max_per_file: int = 50 * 1024 * 1024,
    max_ratio: float = 100.0,
    max_entries: int = 200_000,
) -> ZipCheckoutAdapter:
    return ZipCheckoutAdapter(
        checkouts_root=checkouts_root,
        max_compressed_bytes=max_compressed,
        max_decompressed_bytes=max_decompressed,
        max_per_file_bytes=max_per_file,
        max_inflation_ratio=max_ratio,
        max_entries=max_entries,
    )


def _zip_bytes(
    entries: dict[str, bytes | str], *, compression: int = zipfile.ZIP_DEFLATED
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=compression) as zf:
        for name, content in entries.items():
            payload = content.encode() if isinstance(content, str) else content
            zf.writestr(name, payload)
    return buf.getvalue()


def _zip_with_symlink(target: str = "/etc/passwd", arc_name: str = "link") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        info = zipfile.ZipInfo(arc_name)
        info.create_system = 3  # unix
        info.external_attr = 0xA1FF << 16  # S_IFLNK | 0o777
        zf.writestr(info, target)
    return buf.getvalue()


def _zip_with_absolute_path() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        info = zipfile.ZipInfo("/abs/file.txt")
        zf.writestr(info, b"x")
    return buf.getvalue()


def _zip_with_traversal() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("../escape.txt", b"x")
    return buf.getvalue()


def _zip_bomb_per_file(payload_size: int) -> bytes:
    """One entry whose declared file_size exceeds per-file cap."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", b"\x00" * payload_size)
    return buf.getvalue()


async def _stream_bytes(data: bytes, chunk: int = 1024 * 1024) -> AsyncIterator[bytes]:
    for i in range(0, len(data), chunk):
        yield data[i : i + chunk]


# ----- happy path ----------------------------------------------------


async def test_zip_adapter_persists_and_extracts(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()

    payload = _zip_bytes(
        {
            "src/main.py": "print('hi')\n",
            "README.md": "# demo\n",
            "data/empty/": b"",  # directory entry
        }
    )
    persisted = await adapter.persist_upload(
        repository_id=repository_id,
        stream=_stream_bytes(payload),
    )
    assert persisted.bytes_written == len(payload)
    assert persisted.archive_path.exists()
    assert len(persisted.sha256) == 64

    prepared = await adapter.prepare_checkout(repository_id=repository_id)
    assert prepared.path == tmp_path / str(repository_id)
    assert prepared.file_count == 2  # the directory entry doesn't count
    assert prepared.sha256 == persisted.sha256
    assert (prepared.path / "src/main.py").read_text() == "print('hi')\n"
    assert (prepared.path / "README.md").read_text() == "# demo\n"


async def test_zip_adapter_strips_single_top_level_dir(tmp_path: Path) -> None:
    """GitHub `Download ZIP` produces a `repo-{sha}/...` wrapper. Entries
    must land at the checkout root, not under the wrapper dir."""
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()

    payload = _zip_bytes(
        {
            "myproj-abc123/src/main.py": "x = 1\n",
            "myproj-abc123/README.md": "# y\n",
        }
    )
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    prepared = await adapter.prepare_checkout(repository_id=repository_id)

    assert (prepared.path / "src/main.py").exists()
    assert (prepared.path / "README.md").exists()
    assert not (prepared.path / "myproj-abc123").exists()


async def test_zip_adapter_re_extract_is_idempotent(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()

    first = _zip_bytes({"a.txt": "first"})
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(first)
    )
    prepared = await adapter.prepare_checkout(repository_id=repository_id)
    assert (prepared.path / "a.txt").read_text() == "first"

    # Replace the archive and re-extract — the previous file must be gone.
    second = _zip_bytes({"b.txt": "second"})
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(second)
    )
    prepared2 = await adapter.prepare_checkout(repository_id=repository_id)
    assert (prepared2.path / "b.txt").read_text() == "second"
    assert not (prepared2.path / "a.txt").exists()


async def test_zip_adapter_discard_removes_archive_and_extracted(
    tmp_path: Path,
) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(_zip_bytes({"a.txt": "hi"}))
    )
    await adapter.prepare_checkout(repository_id=repository_id)
    assert adapter.archive_path_for(repository_id).exists()
    assert adapter.checkout_path_for(repository_id).exists()

    await adapter.discard(repository_id=repository_id)
    assert not adapter.archive_path_for(repository_id).exists()
    assert not adapter.checkout_path_for(repository_id).exists()


# ----- validation: compressed cap ------------------------------------


async def test_zip_adapter_rejects_oversize_compressed(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path, max_compressed=64)
    payload = _zip_bytes({"a.bin": b"x" * 10_000})  # well over the 64-byte cap
    repository_id = uuid4()
    with pytest.raises(ZipCheckoutError, match="compressed cap"):
        await adapter.persist_upload(
            repository_id=repository_id, stream=_stream_bytes(payload)
        )
    # Partial file must be cleaned up.
    assert not adapter.archive_path_for(repository_id).exists()


async def test_zip_adapter_rejects_empty_upload(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()
    with pytest.raises(ZipCheckoutError, match="empty"):
        await adapter.persist_upload(
            repository_id=repository_id, stream=_stream_bytes(b"")
        )


async def test_zip_adapter_rejects_non_zip_content(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()
    with pytest.raises(ZipCheckoutError, match="not a valid zip"):
        await adapter.persist_upload(
            repository_id=repository_id, stream=_stream_bytes(b"not a zip")
        )


# ----- validation: bomb guards ---------------------------------------


async def test_zip_adapter_rejects_per_file_oversize(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path, max_per_file=1024)
    repository_id = uuid4()
    payload = _zip_bomb_per_file(2048)
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    with pytest.raises(ZipCheckoutError, match="per-file cap"):
        await adapter.prepare_checkout(repository_id=repository_id)


async def test_zip_adapter_rejects_total_decompressed_oversize(tmp_path: Path) -> None:
    adapter = _make_adapter(
        checkouts_root=tmp_path,
        max_per_file=4096,
        max_decompressed=2048,
    )
    repository_id = uuid4()
    payload = _zip_bytes(
        {
            "a.bin": b"x" * 1500,
            "b.bin": b"y" * 1500,  # cumulative: 3000 > 2048 cap
        }
    )
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    with pytest.raises(ZipCheckoutError, match="total cap"):
        await adapter.prepare_checkout(repository_id=repository_id)


async def test_zip_adapter_rejects_entry_count_explosion(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path, max_entries=2)
    repository_id = uuid4()
    payload = _zip_bytes({f"f{i}.txt": str(i) for i in range(10)})
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    with pytest.raises(ZipCheckoutError, match="entries"):
        await adapter.prepare_checkout(repository_id=repository_id)


async def test_zip_adapter_rejects_high_inflation_ratio(tmp_path: Path) -> None:
    """Highly-compressible payload of all-zeros — DEFLATE achieves
    massive ratio on this. Per-file cap stays high so we test the
    cumulative ratio path, not the per-file one."""
    adapter = _make_adapter(
        checkouts_root=tmp_path,
        max_per_file=10 * 1024 * 1024,
        max_decompressed=10 * 1024 * 1024,
        max_ratio=10.0,
    )
    repository_id = uuid4()
    payload = _zip_bytes({"zeros.bin": b"\x00" * (1024 * 1024)})  # ~1MB of zeros
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    with pytest.raises(ZipCheckoutError, match="inflation ratio"):
        await adapter.prepare_checkout(repository_id=repository_id)


# ----- validation: path / symlink ------------------------------------


async def test_zip_adapter_rejects_absolute_paths(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()
    payload = _zip_with_absolute_path()
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    with pytest.raises(ZipCheckoutError, match="absolute path"):
        await adapter.prepare_checkout(repository_id=repository_id)


async def test_zip_adapter_rejects_path_traversal(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()
    payload = _zip_with_traversal()
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    with pytest.raises(ZipCheckoutError, match="traversal"):
        await adapter.prepare_checkout(repository_id=repository_id)


async def test_zip_adapter_rejects_symlink_entry(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()
    payload = _zip_with_symlink()
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    with pytest.raises(ZipCheckoutError, match="symlink"):
        await adapter.prepare_checkout(repository_id=repository_id)


async def test_zip_adapter_missing_archive_raises(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    with pytest.raises(ZipCheckoutError, match="missing"):
        await adapter.prepare_checkout(repository_id=uuid4())


# ----- helper-level coverage -----------------------------------------


def test_safe_relpath_strips_prefix_and_drops_dirs() -> None:
    assert _safe_relpath("repo-x/src/a.py", "repo-x/") == "src/a.py"
    assert _safe_relpath("repo-x/", "repo-x/") is None
    assert _safe_relpath("dir/", None) is None


def test_safe_relpath_rejects_absolute_and_traversal() -> None:
    with pytest.raises(ZipCheckoutError, match="absolute"):
        _safe_relpath("/etc/x", None)
    with pytest.raises(ZipCheckoutError, match="traversal"):
        _safe_relpath("a/../b", None)
    with pytest.raises(ZipCheckoutError, match="drive-letter"):
        _safe_relpath("C:/Windows/x", None)


def test_is_symlink_detects_symbolic_link_mode() -> None:
    info = zipfile.ZipInfo("link")
    info.external_attr = 0xA1FF << 16
    assert _is_symlink(info) is True
    info.external_attr = 0x81FF << 16  # regular file
    assert _is_symlink(info) is False


def test_should_skip_entry_blocks_git_dir_and_binaries() -> None:
    # `.git/` plumbing always skipped regardless of extension
    assert _should_skip_entry(".git/HEAD") is True
    assert _should_skip_entry(".git/objects/pack/pack-abc.pack") is True
    assert _should_skip_entry(".git/config") is True
    # `.idea/` IDE files always skipped regardless of extension (workspace.xml is XML/text)
    assert _should_skip_entry(".idea/workspace.xml") is True
    assert _should_skip_entry(".idea/inspectionProfiles/profiles_settings.xml") is True
    assert _should_skip_entry(".idea/modules.xml") is True
    # Binary extensions
    assert _should_skip_entry("docs/logo.png") is True
    assert _should_skip_entry("assets/font.woff2") is True
    assert _should_skip_entry("dist/app.exe") is True
    assert _should_skip_entry("vendor/lib.so") is True
    assert _should_skip_entry("nested/archive.zip") is True
    assert _should_skip_entry("paper.pdf") is True
    # OS junk
    assert _should_skip_entry(".DS_Store") is True
    assert _should_skip_entry("subdir/.DS_Store") is True
    assert _should_skip_entry("subdir/Thumbs.db") is True
    # Source / docs / data formats stay
    assert _should_skip_entry("src/main.py") is False
    assert _should_skip_entry("README.md") is False
    assert _should_skip_entry("config.yaml") is False
    assert _should_skip_entry("logo.svg") is False  # SVG is text
    assert _should_skip_entry("Makefile") is False
    assert _should_skip_entry("data/sample.json") is False


async def test_zip_adapter_skips_binaries_and_git_dir(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()

    payload = _zip_bytes(
        {
            # Source — kept
            "src/main.py": "print('hi')\n",
            "README.md": "# demo\n",
            "logo.svg": "<svg/>",
            # Binaries — skipped
            "assets/logo.png": b"\x89PNG\r\n\x1a\nbinary",
            "fonts/Inter.woff2": b"woff2-binary-blob",
            "build/app.exe": b"MZ\x90\x00binary",
            "nested.zip": b"PK\x03\x04",
            # .git/ plumbing — skipped wholesale
            ".git/HEAD": "ref: refs/heads/main\n",
            ".git/objects/pack/pack-deadbeef.pack": b"\x00\x01PACK",
            ".git/config": "[core]\n",
            # OS junk
            ".DS_Store": b"\x00DSstore",
            "subdir/Thumbs.db": b"\x00thumb",
        }
    )
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    prepared = await adapter.prepare_checkout(repository_id=repository_id)

    # Only the 3 text-ish files were written
    assert prepared.file_count == 3
    assert prepared.skipped_count == 9
    assert prepared.skipped_bytes > 0
    assert (prepared.path / "src/main.py").exists()
    assert (prepared.path / "README.md").exists()
    assert (prepared.path / "logo.svg").exists()

    # Skipped entries never landed on disk
    assert not (prepared.path / "assets/logo.png").exists()
    assert not (prepared.path / "fonts/Inter.woff2").exists()
    assert not (prepared.path / "build/app.exe").exists()
    assert not (prepared.path / "nested.zip").exists()
    assert not (prepared.path / ".git").exists()
    assert not (prepared.path / ".DS_Store").exists()
    assert not (prepared.path / "subdir/Thumbs.db").exists()


async def test_zip_adapter_skipped_files_do_not_count_against_per_file_cap(
    tmp_path: Path,
) -> None:
    """A 100 MB image must NOT trip the 1 MB per-file cap — it's skipped
    before the cap check runs. This is the whole point of the skip: a git
    repo with a multi-MB binary asset still ingests cleanly."""
    adapter = _make_adapter(checkouts_root=tmp_path, max_per_file=1024 * 1024)
    repository_id = uuid4()

    big_blob = b"\x00" * (5 * 1024 * 1024)  # 5 MB > 1 MB per-file cap
    payload = _zip_bytes(
        {
            "src/main.py": "print('hi')\n",
            "assets/huge.png": big_blob,
        }
    )
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    prepared = await adapter.prepare_checkout(repository_id=repository_id)
    assert prepared.file_count == 1
    assert prepared.skipped_count == 1
    assert prepared.skipped_bytes >= len(big_blob)


def test_is_gitignored_root_gitignore_blocks_matching_paths() -> None:
    spec = pathspec.PathSpec.from_lines(
        "gitignore", ["node_modules/", "dist/", "*.log", ".env"]
    )
    matchers = [("", spec)]
    # Direct dir matches
    assert _is_gitignored("node_modules/foo/bar.js", matchers) is True
    assert _is_gitignored("dist/bundle.js", matchers) is True
    # Glob extension match anywhere
    assert _is_gitignored("logs/app.log", matchers) is True
    assert _is_gitignored("nested/deep/x.log", matchers) is True
    # Filename anchored
    assert _is_gitignored(".env", matchers) is True
    # Source files unaffected
    assert _is_gitignored("src/main.py", matchers) is False
    assert _is_gitignored("README.md", matchers) is False


def test_is_gitignored_nested_gitignore_scoped_to_subtree() -> None:
    """A `.gitignore` in `web/` only applies to files under `web/`."""
    nested_spec = pathspec.PathSpec.from_lines("gitignore", ["*.local"])
    matchers = [("web", nested_spec)]
    assert _is_gitignored("web/config.local", matchers) is True
    assert _is_gitignored("web/sub/deep/x.local", matchers) is True
    # `.local` outside `web/` is NOT covered by the nested gitignore
    assert _is_gitignored("backend/x.local", matchers) is False
    assert _is_gitignored("x.local", matchers) is False


async def test_zip_adapter_skips_idea_dir(tmp_path: Path) -> None:
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()

    payload = _zip_bytes(
        {
            "src/main.py": "print('hi')\n",
            ".idea/workspace.xml": "<project/>",
            ".idea/modules.xml": "<modules/>",
            ".idea/inspectionProfiles/profile.xml": "<profile/>",
        }
    )
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    prepared = await adapter.prepare_checkout(repository_id=repository_id)

    assert prepared.file_count == 1
    assert prepared.skipped_count == 3
    assert (prepared.path / "src/main.py").exists()
    assert not (prepared.path / ".idea").exists()


async def test_zip_adapter_honors_root_gitignore(tmp_path: Path) -> None:
    """A `.gitignore` at the archive root drops matching files before
    they hit the per-file / decompressed caps."""
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()

    payload = _zip_bytes(
        {
            ".gitignore": "node_modules/\ndist/\n*.log\n.env\n",
            "src/main.py": "print('hi')\n",
            "README.md": "# demo\n",
            "node_modules/foo/index.js": "module.exports = {}",
            "node_modules/bar/package.json": "{}",
            "dist/bundle.js": "// built\n",
            "logs/server.log": "INFO",
            ".env": "SECRET=x",
            "src/app.log": "should-also-be-ignored",
        }
    )
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    prepared = await adapter.prepare_checkout(repository_id=repository_id)

    # 3 kept: .gitignore (text, useful evidence), src/main.py, README.md
    assert prepared.file_count == 3
    assert (prepared.path / ".gitignore").exists()
    assert (prepared.path / "src/main.py").exists()
    assert (prepared.path / "README.md").exists()

    # 6 dropped via gitignore matching
    assert prepared.skipped_count == 6
    assert not (prepared.path / "node_modules").exists()
    assert not (prepared.path / "dist").exists()
    assert not (prepared.path / ".env").exists()
    assert not (prepared.path / "logs/server.log").exists()
    assert not (prepared.path / "src/app.log").exists()


async def test_zip_adapter_honors_nested_gitignore(tmp_path: Path) -> None:
    """A nested `web/.gitignore` only filters within `web/`."""
    adapter = _make_adapter(checkouts_root=tmp_path)
    repository_id = uuid4()

    payload = _zip_bytes(
        {
            "web/.gitignore": "*.local\n",
            "web/main.py": "print('hi')\n",
            "web/dev.local": "secret",  # gitignored by nested rule
            "backend/dev.local": "secret",  # outside web/, NOT gitignored
            "README.md": "# demo\n",
        }
    )
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    prepared = await adapter.prepare_checkout(repository_id=repository_id)

    assert (prepared.path / "web/main.py").exists()
    assert (prepared.path / "backend/dev.local").exists()  # outside scope
    assert (prepared.path / "README.md").exists()
    assert not (prepared.path / "web/dev.local").exists()


async def test_zip_adapter_skipped_files_do_not_count_against_total_cap(
    tmp_path: Path,
) -> None:
    """Total decompressed cap counts only KEPT files, so a repo full of
    binaries still extracts under a tight cap."""
    adapter = _make_adapter(
        checkouts_root=tmp_path,
        max_decompressed=2 * 1024,
        max_per_file=10 * 1024 * 1024,
    )
    repository_id = uuid4()

    payload = _zip_bytes(
        {
            "src/main.py": "print('hi')\n",  # tiny — kept
            "assets/big.png": b"\x00" * (10 * 1024),  # 10 KB — skipped
            ".git/objects/pack/pack-x.pack": b"\x00" * (50 * 1024),  # 50 KB skipped
        }
    )
    await adapter.persist_upload(
        repository_id=repository_id, stream=_stream_bytes(payload)
    )
    prepared = await adapter.prepare_checkout(repository_id=repository_id)
    assert prepared.file_count == 1
    assert prepared.skipped_count == 2
