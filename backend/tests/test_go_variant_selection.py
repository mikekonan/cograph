from __future__ import annotations

from pathlib import Path

from backend.app.graph.go_variants import (
    resolve_go_index_profile,
    select_go_package_files,
)


def _package(tmp_path: Path, files: dict[str, str]) -> tuple[Path, ...]:
    (tmp_path / "go.mod").write_text("module example.com/pkg\ngo 1.22\n", encoding="utf-8")
    written: list[Path] = []
    for name, body in files.items():
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return tuple(written)


def _select(tmp_path: Path, files: dict[str, str]) -> set[str]:
    written = _package(tmp_path, files)
    selection = select_go_package_files(
        root_path=tmp_path,
        package_key="pkg",
        files=written,
        profile=resolve_go_index_profile(tmp_path),
    )
    return {selected.relative_path for selected in selection.selected_files}


def test_custom_tag_pair_selects_the_untagged_half(tmp_path: Path) -> None:
    # Verbatim shape of redis/go-redis extra/rediscmd, which used to fail the
    # whole repository: both files define `String`, so selecting both raises a
    # variant conflict. A tag nobody passed is absent, so `appengine` is false
    # and `!appengine` is true — the same choice `go build` makes.
    selected = _select(
        tmp_path,
        {
            "safe.go": '//go:build appengine\n\npackage pkg\n\nfunc String(b []byte) string {\n\treturn string(b)\n}\n',
            "unsafe.go": '//go:build !appengine\n\npackage pkg\n\nimport "unsafe"\n\nfunc String(b []byte) string {\n\treturn unsafe.String(&b[0], len(b))\n}\n',
        },
    )

    assert selected == {"unsafe.go"}


def test_file_behind_a_custom_tag_alone_is_excluded_rather_than_fatal(tmp_path: Path) -> None:
    # The cost of the rule above: a file the default build does not compile
    # claims no symbols. It stays an indexed source file, so its text is still
    # searchable — what it must not do is fail the package or duplicate a symbol.
    selected = _select(
        tmp_path,
        {
            "main.go": "package pkg\n\nfunc Run() {}\n",
            "tools.go": "//go:build tools\n\npackage pkg\n\nfunc Tool() {}\n",
        },
    )

    assert selected == {"main.go"}
