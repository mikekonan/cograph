"""Tests for MdIndexer."""

from __future__ import annotations

import pytest

from backend.app.md_rag.indexer import MdIndexer
from backend.app.md_rag.parser import ParsedMarkdown
from backend.app.models.enums import MdLinkType


@pytest.mark.asyncio
async def test_replace_links_deduplicates_by_href() -> None:
    """Duplicate hrefs within a single document must not violate the unique
    constraint ``uq_md_links_source_href``.
    """
    indexer = MdIndexer()

    parsed = ParsedMarkdown(
        title="Test",
        links=[
            {"text": "First", "href": "https://example.com/a", "line": 1, "link_type": "absolute"},
            {"text": "Second", "href": "https://example.com/a", "line": 2, "link_type": "absolute"},
            {"text": "Third", "href": "https://example.com/b", "line": 3, "link_type": "absolute"},
        ],
    )

    class _FakeSession:
        def __init__(self) -> None:
            self.added: list = []

        async def execute(self, stmt):
            pass

        def add_all(self, rows):
            self.added = rows

    class _FakeDoc:
        id = "doc-id"

    session = _FakeSession()
    await indexer._replace_links(
        session=session,
        document=_FakeDoc(),
        parsed=parsed,
    )

    assert len(session.added) == 2
    assert session.added[0].href == "https://example.com/a"
    assert session.added[0].link_text == "First"
    assert session.added[0].link_type == MdLinkType.ABSOLUTE
    assert session.added[1].href == "https://example.com/b"
    assert session.added[1].link_text == "Third"
