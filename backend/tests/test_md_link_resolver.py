"""Href resolution rules for markdown collection links.

Wiki-imported documents key themselves as ``<source>:<space>:<page-id>`` but
link to each other by absolute wiki URL, so URL → page-id → document is the
bridge that makes the cross-document graph resolvable at all.
"""

from __future__ import annotations

from uuid import uuid4

from backend.app.md_rag.link_resolver import (
    MdLinkResolver,
    _page_id_from_source_key,
    _page_id_from_url,
)


def test_absolute_wiki_url_resolves_via_page_id():
    doc_id = uuid4()
    lookup = {"confluence:DOCS:3474267#sec-000": doc_id, "page:3474267": doc_id}

    href = "https://example.atlassian.net/wiki/spaces/DOCS/pages/3474267/Some+Title"

    assert MdLinkResolver._resolve_href(href, lookup) == doc_id


def test_url_for_an_unknown_page_stays_unresolved():
    lookup = {"page:3474267": uuid4()}

    href = "https://example.atlassian.net/wiki/spaces/DOCS/pages/999999/Other"

    assert MdLinkResolver._resolve_href(href, lookup) is None


def test_external_url_without_page_id_stays_unresolved():
    assert MdLinkResolver._resolve_href("https://example.test/blog/post", {}) is None


def test_direct_source_key_match_still_wins():
    doc_id = uuid4()
    lookup = {"docs/guide.md": doc_id}

    assert MdLinkResolver._resolve_href("./docs/guide.md", lookup) == doc_id


def test_page_id_extraction():
    assert _page_id_from_source_key("confluence:DOCS:3474267#sec-000") == "3474267"
    assert _page_id_from_source_key("confluence:DOCS:3474267") == "3474267"
    assert _page_id_from_source_key("docs/guide.md") is None
    assert _page_id_from_url("https://example.atlassian.net/wiki/pages/42/T") == "42"
    assert _page_id_from_url("https://example.test/blog") is None
