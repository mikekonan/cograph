"""Unit tests for the checkout language scanner (issue #66)."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.pipeline.language_scanner import (
    _MAX_FILE_BYTES,
    detect_language,
    scan_languages,
)


def test_detect_language_by_extension(tmp_path: Path) -> None:
    assert detect_language(tmp_path / "main.go") == "go"
    assert detect_language(tmp_path / "App.tsx") == "typescript"
    assert detect_language(tmp_path / "script.js") == "javascript"
    assert detect_language(tmp_path / "service.PY") == "python"
    assert detect_language(tmp_path / "build.gradle") == "groovy"


def test_detect_language_by_filename(tmp_path: Path) -> None:
    assert detect_language(tmp_path / "Makefile") == "makefile"
    assert detect_language(tmp_path / "makefile") == "makefile"
    assert detect_language(tmp_path / "Dockerfile") == "dockerfile"
    assert detect_language(tmp_path / "Rakefile") == "ruby"


def test_detect_language_unknown(tmp_path: Path) -> None:
    assert detect_language(tmp_path / "data.bin") is None
    assert detect_language(tmp_path / "image.png") is None
    assert detect_language(tmp_path / "weird.xyz") is None


def test_scan_languages_returns_empty_for_missing_path(tmp_path: Path) -> None:
    assert scan_languages(tmp_path / "does-not-exist") == {}


def test_scan_languages_counts_bytes_per_language(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "util.go").write_text("package util\nfunc X() {}\n", encoding="utf-8")
    (tmp_path / "index.js").write_text("console.log(1);\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    (tmp_path / "binary.png").write_bytes(b"\x89PNG\x00\x00\x00")

    result = scan_languages(tmp_path)

    assert "go" in result
    assert result["go"] == (tmp_path / "main.go").stat().st_size + (tmp_path / "util.go").stat().st_size
    assert result["javascript"] == (tmp_path / "index.js").stat().st_size
    assert result["makefile"] == (tmp_path / "Makefile").stat().st_size
    assert result["markdown"] == (tmp_path / "README.md").stat().st_size
    # Binary extension is not in the language map; should not appear.
    assert "png" not in result
    assert "unknown" not in result


def test_scan_languages_skips_vendored_dirs(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")

    node_modules = tmp_path / "node_modules" / "lodash"
    node_modules.mkdir(parents=True)
    (node_modules / "index.js").write_text("// huge vendored bundle\n" * 100, encoding="utf-8")

    vendor = tmp_path / "vendor" / "github.com" / "x"
    vendor.mkdir(parents=True)
    (vendor / "x.go").write_text("package x\n" * 50, encoding="utf-8")

    nested_git = tmp_path / "subrepo" / ".git"
    nested_git.mkdir(parents=True)
    (nested_git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    result = scan_languages(tmp_path)

    assert result == {"go": (tmp_path / "main.go").stat().st_size}


def test_scan_languages_skips_files_over_the_size_cap(tmp_path: Path) -> None:
    small = tmp_path / "small.go"
    small.write_text("package x\n", encoding="utf-8")

    huge = tmp_path / "huge.go"
    huge.write_bytes(b"// pad\n" * (_MAX_FILE_BYTES // 7 + 1))
    assert huge.stat().st_size > _MAX_FILE_BYTES

    result = scan_languages(tmp_path)
    assert result == {"go": small.stat().st_size}


def test_scan_languages_does_not_follow_symlinks(tmp_path: Path) -> None:
    target_dir = tmp_path / "real"
    target_dir.mkdir()
    (target_dir / "code.py").write_text("x = 1\n", encoding="utf-8")

    link = tmp_path / "link"
    try:
        link.symlink_to(target_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")

    result = scan_languages(tmp_path)

    # Python file is reachable via its real directory, but only counted once.
    assert result["python"] == (target_dir / "code.py").stat().st_size
    assert result["go"] == (tmp_path / "main.go").stat().st_size
