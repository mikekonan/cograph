"""Grammar-cache guards around tree-sitter-language-pack.

The pack (v1.6+) ships no grammar binaries — `get_parser` downloads
them from GitHub releases on first use with NO timeout. In prod that
turned a cold cache + stalled CDN into a sync job frozen inside an open
DB transaction (2026-06-11, kms reindex). The contract pinned here:

  * `missing_grammars()` reports exactly the parser names from
    `languages.py` that are absent from the local cache;
  * `download_missing_grammars()` downloads only what's missing and
    is a no-op when the cache is complete (so the Docker build step
    and the worker-startup fallback never re-download).
"""

from __future__ import annotations

import backend.app.graph.parser as parser_module
from backend.app.graph.parser import download_missing_grammars, missing_grammars


def _patch_pack(monkeypatch, *, downloaded: list[str]) -> list[list[str]]:
    """Stub the tree_sitter_language_pack functions parser.py imports
    lazily. Returns the recorder list that captures download() calls."""
    calls: list[list[str]] = []

    import tree_sitter_language_pack as pack

    monkeypatch.setattr(pack, "downloaded_languages", lambda: list(downloaded))
    monkeypatch.setattr(pack, "download", lambda names: calls.append(list(names)))
    return calls


def test_missing_grammars_reports_required_set_when_cache_empty(monkeypatch) -> None:
    _patch_pack(monkeypatch, downloaded=[])
    assert missing_grammars() == ("go", "python")


def test_missing_grammars_empty_when_cache_complete(monkeypatch) -> None:
    _patch_pack(monkeypatch, downloaded=["go", "python", "rust"])
    assert missing_grammars() == ()


def test_download_missing_grammars_downloads_only_the_gap(monkeypatch) -> None:
    calls = _patch_pack(monkeypatch, downloaded=["python"])
    download_missing_grammars()
    assert calls == [["go"]]


def test_download_missing_grammars_noop_when_cache_complete(monkeypatch) -> None:
    calls = _patch_pack(monkeypatch, downloaded=["go", "python"])
    download_missing_grammars()
    assert calls == []


def test_parser_module_exports_are_wired() -> None:
    # The Dockerfile bake step imports these by name — a rename must fail
    # tests, not the image build.
    assert callable(parser_module.missing_grammars)
    assert callable(parser_module.download_missing_grammars)
