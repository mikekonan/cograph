"""Per-`RepoKind` page-kind catalog.

The planner uses the catalog to constrain which `PageKind`s are allowed
for a given repo. `unknown` falls back to the `concept` page kind so
the wiki stays useful when the kind classifier hasn't agreed with
itself yet.

This is data-only; the prompt assembly (`prompts.py`) imports it to
inject the allowed list into the planner system message.
"""

from __future__ import annotations

from typing import Final

from backend.app.wiki.schemas import PageKind, RepoKind


PAGE_CATALOGS: Final[dict[RepoKind, tuple[PageKind, ...]]] = {
    RepoKind.CLI: (
        PageKind.QUICK_START,
        PageKind.CLI_REFERENCE,
        PageKind.CONFIGURATION,
        PageKind.EXAMPLES,
        PageKind.TROUBLESHOOTING,
    ),
    RepoKind.LIBRARY: (
        PageKind.QUICK_START,
        PageKind.INSTALLATION,
        PageKind.PUBLIC_API_REFERENCE,
        PageKind.EMBEDDING_GUIDE,
        PageKind.COMPATIBILITY,
        PageKind.MIGRATION_GUIDE,
        PageKind.EXAMPLES,
    ),
    RepoKind.SERVICE: (
        PageKind.SERVICE_TOPOLOGY,
        PageKind.CONFIGURATION,
        PageKind.KEY_FLOW,
        PageKind.DOMAIN_MODEL,
        PageKind.API_REFERENCE,
        PageKind.SECURITY,
    ),
    RepoKind.CODE_GENERATOR: (
        PageKind.QUICK_START,
        PageKind.SUPPORTED_INPUT_FEATURES,
        PageKind.GENERATED_OUTPUT_SHAPE,
        PageKind.CUSTOMIZATION,
        PageKind.CONFIGURATION,
        PageKind.EXAMPLES,
    ),
    RepoKind.FRAMEWORK: (
        PageKind.QUICK_START,
        PageKind.CORE_ABSTRACTIONS,
        PageKind.EXTENSION_POINTS,
        PageKind.PLUGIN_GUIDE,
        PageKind.EXAMPLES,
    ),
    RepoKind.MONOREPO: (
        PageKind.OVERVIEW,
        PageKind.QUICK_START,
        PageKind.CONCEPT,
    ),
    RepoKind.HYBRID: (
        PageKind.OVERVIEW,
        PageKind.QUICK_START,
        PageKind.DOMAIN_MODEL,
        PageKind.API_REFERENCE,
        PageKind.CONFIGURATION,
        PageKind.KEY_FLOW,
        PageKind.CONCEPT,
    ),
    RepoKind.UNKNOWN: (PageKind.CONCEPT,),
}


_BASELINE_KINDS: Final = (PageKind.INDEX, PageKind.OVERVIEW)


def allowed_page_kinds(repo_kind: RepoKind) -> tuple[PageKind, ...]:
    """Return the catalog for `repo_kind` plus the universal baseline.

    Every wiki ships at least an `index` (landing page) and an
    `overview`; specific kinds add to that list.
    """
    catalog = PAGE_CATALOGS.get(repo_kind, PAGE_CATALOGS[RepoKind.UNKNOWN])
    seen: set[PageKind] = set()
    out: list[PageKind] = []
    for k in (*_BASELINE_KINDS, *catalog):
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return tuple(out)


__all__ = (
    "PAGE_CATALOGS",
    "allowed_page_kinds",
)
