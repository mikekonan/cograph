"""Tests for the agent's sandboxed filesystem facade."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from backend.app.wiki.checkout_fs import CheckoutFs, CheckoutFsError


def _make_fs(tmp_path: Path) -> CheckoutFs:
    return CheckoutFs(root=tmp_path)


# ---------------------------------------------------------------------------
# Path containment
# ---------------------------------------------------------------------------


def test_read_file_rejects_parent_traversal(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    (tmp_path / "ok.txt").write_text("hi")
    with pytest.raises(CheckoutFsError, match="escapes the checkout root"):
        fs.read_file("../etc/passwd")


def test_read_file_rejects_absolute_path(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    # On POSIX, `Path / "/etc/passwd"` discards the left side and yields
    # `/etc/passwd`. Resolve() escapes root, so the relative_to check
    # rejects it.
    with pytest.raises(CheckoutFsError, match="escapes the checkout root"):
        fs.read_file("/etc/passwd")


def test_read_file_rejects_symlink_pointing_outside_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside_target.txt"
    outside.write_text("secret")
    try:
        fs = _make_fs(tmp_path)
        link = tmp_path / "link.txt"
        os.symlink(outside, link)
        with pytest.raises(CheckoutFsError, match="escapes the checkout root"):
            fs.read_file("link.txt")
    finally:
        if outside.exists():
            outside.unlink()


# ---------------------------------------------------------------------------
# read_file behaviour
# ---------------------------------------------------------------------------


def test_read_file_returns_line_window_and_total(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    (tmp_path / "many.txt").write_text("\n".join(f"line {i}" for i in range(1, 51)))
    result = fs.read_file("many.txt", offset=10, limit=5)
    assert result["start_line"] == 10
    assert result["end_line"] == 14
    assert result["total_lines"] == 50
    assert result["body"].splitlines() == [
        "line 10",
        "line 11",
        "line 12",
        "line 13",
        "line 14",
    ]
    assert result["truncated"] is False


def test_read_file_clamps_limit_to_max(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    (tmp_path / "f.txt").write_text("\n".join(str(i) for i in range(2_000)))
    # Asking for 9_999 must clamp to the 400-line ceiling.
    result = fs.read_file("f.txt", offset=1, limit=9_999)
    assert result["end_line"] - result["start_line"] + 1 <= 400


def test_read_file_refuses_binary(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    (tmp_path / "blob.bin").write_bytes(b"hello\x00world")
    with pytest.raises(CheckoutFsError, match="binary"):
        fs.read_file("blob.bin")


def test_read_file_truncates_oversize_body(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    huge_line = "x" * 25_000
    (tmp_path / "fat.txt").write_text(huge_line)
    result = fs.read_file("fat.txt", offset=1, limit=400)
    assert result["truncated"] is True
    assert len(result["body"].encode("utf-8")) <= 20_000


def test_read_file_missing_path_raises(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(CheckoutFsError, match="not a file"):
        fs.read_file("does/not/exist.txt")


def test_read_file_rejects_empty_path(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(CheckoutFsError):
        fs.read_file("")


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


def test_list_files_skips_hidden_top_dirs(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    (tmp_path / ".cograph").mkdir()
    (tmp_path / ".cograph" / "wiki.json").write_text("{}")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.go").write_text("package src")
    (tmp_path / "README.md").write_text("hi")
    result = fs.list_files("**/*")
    matches = result["matches"]
    assert "src/a.go" in matches
    assert "README.md" in matches
    assert not any(m.startswith(".git") for m in matches)
    assert not any(m.startswith(".cograph") for m in matches)


def test_list_files_with_glob_filter(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.go").write_text("x")
    (tmp_path / "pkg" / "b.py").write_text("y")
    result = fs.list_files("**/*.go")
    assert result["matches"] == ["pkg/a.go"]


def test_list_files_rejects_absolute_glob(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(CheckoutFsError, match="must be relative"):
        fs.list_files("/etc/*")


# ---------------------------------------------------------------------------
# grep — Python fallback path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_python_fallback_finds_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = _make_fs(tmp_path)
    (tmp_path / "a.go").write_text("package main\n\nfunc Run() error {\n\treturn nil\n}\n")
    (tmp_path / "b.go").write_text("package main\n\nfunc Setup() {}\n")
    # Force the Python fallback even when ripgrep is on PATH.
    monkeypatch.setattr(shutil, "which", lambda _: None)
    result = await fs.grep("func Run", glob="*.go")
    assert result["pattern"] == "func Run"
    assert any(m["path"] == "a.go" for m in result["matches"])
    assert all("func Setup" not in m["text"] for m in result["matches"])


@pytest.mark.asyncio
async def test_grep_python_fallback_skips_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = _make_fs(tmp_path)
    (tmp_path / "blob.bin").write_bytes(b"PATTERN\x00stuff")
    (tmp_path / "ok.txt").write_text("PATTERN here\n")
    monkeypatch.setattr(shutil, "which", lambda _: None)
    result = await fs.grep("PATTERN")
    paths = {m["path"] for m in result["matches"]}
    assert "ok.txt" in paths
    assert "blob.bin" not in paths


@pytest.mark.asyncio
async def test_grep_python_fallback_rejects_invalid_regex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = _make_fs(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(CheckoutFsError, match="invalid regex"):
        await fs.grep("[unclosed")


@pytest.mark.asyncio
async def test_grep_rejects_empty_pattern(tmp_path: Path) -> None:
    fs = _make_fs(tmp_path)
    with pytest.raises(CheckoutFsError):
        await fs.grep("")


@pytest.mark.asyncio
async def test_grep_caps_match_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = _make_fs(tmp_path)
    (tmp_path / "spam.txt").write_text(
        "\n".join(f"hit {i}" for i in range(500))
    )
    monkeypatch.setattr(shutil, "which", lambda _: None)
    result = await fs.grep("hit", glob="**/*.txt")
    assert len(result["matches"]) <= 100
    assert result["truncated"] is True
