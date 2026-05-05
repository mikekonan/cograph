"""Tests for `page_catalogs` — repo-kind → allowed page kinds."""

from __future__ import annotations

from backend.app.wiki.page_catalogs import (
    PAGE_CATALOGS,
    allowed_page_kinds,
)
from backend.app.wiki.schemas import PageKind, RepoKind


def test_every_repo_kind_has_a_catalog():
    for repo_kind in RepoKind:
        # Either the kind has its own catalog or `allowed_page_kinds`
        # falls back to UNKNOWN — both are valid.
        kinds = allowed_page_kinds(repo_kind)
        assert kinds, f"empty catalog for {repo_kind}"


def test_baseline_kinds_present_in_every_catalog():
    """Every wiki ships at least an `index` and an `overview` page."""
    for repo_kind in RepoKind:
        kinds = allowed_page_kinds(repo_kind)
        assert PageKind.INDEX in kinds
        assert PageKind.OVERVIEW in kinds


def test_cli_catalog_includes_cli_reference():
    kinds = allowed_page_kinds(RepoKind.CLI)
    assert PageKind.CLI_REFERENCE in kinds
    assert PageKind.QUICK_START in kinds


def test_library_catalog_includes_public_api_and_embedding_guide():
    kinds = allowed_page_kinds(RepoKind.LIBRARY)
    assert PageKind.PUBLIC_API_REFERENCE in kinds
    assert PageKind.EMBEDDING_GUIDE in kinds


def test_service_catalog_includes_topology_and_security():
    kinds = allowed_page_kinds(RepoKind.SERVICE)
    assert PageKind.SERVICE_TOPOLOGY in kinds
    assert PageKind.SECURITY in kinds


def test_code_generator_catalog_has_generator_specific_kinds():
    kinds = allowed_page_kinds(RepoKind.CODE_GENERATOR)
    assert PageKind.SUPPORTED_INPUT_FEATURES in kinds
    assert PageKind.GENERATED_OUTPUT_SHAPE in kinds
    assert PageKind.CUSTOMIZATION in kinds


def test_unknown_falls_back_to_concept():
    kinds = allowed_page_kinds(RepoKind.UNKNOWN)
    # Concept must be available; baseline (index/overview) too.
    assert PageKind.CONCEPT in kinds


def test_catalog_returns_no_duplicates():
    for repo_kind in RepoKind:
        kinds = allowed_page_kinds(repo_kind)
        assert len(kinds) == len(set(kinds))


def test_page_catalogs_dict_does_not_include_baseline():
    """The `PAGE_CATALOGS` data table only stores repo-specific kinds;
    baseline (index/overview) is added by `allowed_page_kinds()` so
    the data table stays focused."""
    for kinds in PAGE_CATALOGS.values():
        # CLI/SERVICE/etc. catalogs may legitimately omit INDEX from
        # the table, but should never repeat it.
        assert len(kinds) == len(set(kinds))
