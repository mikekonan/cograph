"""Grammar-cache guards around tree-sitter-language-pack.

The pack (v1.6+) ships no grammar binaries — `get_parser` downloads
them from GitHub releases on first use with NO timeout. In prod that
turned a cold cache + stalled CDN into a sync job frozen inside an open
DB transaction (2026-06-11, a production reindex). The contract pinned here:

  * `missing_grammars()` reports exactly the parser names from
    `languages.py` that are absent from the local cache;
  * `download_missing_grammars()` downloads only what's missing and
    is a no-op when the cache is complete (so the Docker build step
    and the worker-startup fallback never re-download);
  * it then builds a parser for each name it downloaded. Since pack 1.17
    `download` only caches the bundle archive — the grammar is unpacked,
    and reported by `downloaded_languages`, on the first `get_parser`.
    Skip that and every later check still says "missing", which is what
    broke the Docker bake step and CI when the pin moved to 1.17.
"""

from __future__ import annotations

import backend.app.graph.parser as parser_module
from backend.app.graph.parser import download_missing_grammars, missing_grammars


def _patch_pack(
    monkeypatch, *, downloaded: list[str]
) -> tuple[list[list[str]], list[str]]:
    """Stub the tree_sitter_language_pack functions parser.py imports
    lazily. Returns the recorders for download() and get_parser() calls."""
    calls: list[list[str]] = []
    parsed: list[str] = []

    import tree_sitter_language_pack as pack

    monkeypatch.setattr(pack, "downloaded_languages", lambda: list(downloaded))
    monkeypatch.setattr(pack, "download", lambda names: calls.append(list(names)))
    monkeypatch.setattr(pack, "get_parser", lambda name: parsed.append(name))
    return calls, parsed


# The full grammar set: one per language plus the `tsx` extension override
# (TypeScript spans two grammars — see LanguageDefinition.parser_name_by_extension).
_ALL_GRAMMARS = ("go", "javascript", "python", "tsx", "typescript")


def test_missing_grammars_reports_required_set_when_cache_empty(monkeypatch) -> None:
    _patch_pack(monkeypatch, downloaded=[])
    assert missing_grammars() == _ALL_GRAMMARS


def test_missing_grammars_empty_when_cache_complete(monkeypatch) -> None:
    _patch_pack(monkeypatch, downloaded=[*_ALL_GRAMMARS, "rust"])
    assert missing_grammars() == ()


def test_download_missing_grammars_downloads_only_the_gap(monkeypatch) -> None:
    calls, _ = _patch_pack(
        monkeypatch, downloaded=["python", "typescript", "tsx", "javascript"]
    )
    download_missing_grammars()
    assert calls == [["go"]]


def test_download_missing_grammars_noop_when_cache_complete(monkeypatch) -> None:
    calls, parsed = _patch_pack(monkeypatch, downloaded=list(_ALL_GRAMMARS))
    download_missing_grammars()
    assert calls == []
    assert parsed == []


def test_download_missing_grammars_unpacks_what_it_downloaded(monkeypatch) -> None:
    # The archive `download` leaves behind is not a usable grammar and does not
    # count as downloaded until a parser is built from it. Without this the
    # Dockerfile's own `assert not missing_grammars()` fails on a clean cache.
    _, parsed = _patch_pack(monkeypatch, downloaded=["python", "typescript"])
    download_missing_grammars()
    assert parsed == ["go", "javascript", "tsx"]


def test_parser_module_exports_are_wired() -> None:
    # The Dockerfile bake step imports these by name — a rename must fail
    # tests, not the image build.
    assert callable(parser_module.missing_grammars)
    assert callable(parser_module.download_missing_grammars)
