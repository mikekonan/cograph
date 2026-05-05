"""Deterministic, URL-safe slug derivation from repo document file_path values.

Strategy
--------
1. Strip a leading ``docs/`` or ``doc/`` directory prefix (these are very common
   and produce redundant slugs like ``docs-readme``).
2. Drop the file extension (``.md``, ``.rst``, etc.).
3. Lowercase and replace every run of non-alphanumeric characters with ``-``.
4. Collapse repeated hyphens; strip leading/trailing hyphens.
5. Truncate to 80 characters to keep URLs readable.

Collision handling
------------------
``file_path_to_slug`` is a pure function — it does not guarantee uniqueness.
Callers that need a collision-free mapping should use ``build_slug_map``, which
appends a 4-hex-character suffix derived from the document UUID when two paths
produce the same base slug.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from uuid import UUID

# Extensions to strip before slugifying.
_STRIP_EXTENSIONS = {".md", ".rst", ".txt", ".mdx", ".markdown"}

# Leading prefixes that add no meaningful slug information.
_STRIP_PREFIXES = {"docs", "doc", "documentation", "pages"}

# Normalise any run of non-alphanumeric chars to a single hyphen.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_MAX_SLUG_LEN = 80


def file_path_to_slug(path: str) -> str:
    """Return a deterministic URL-safe slug for *path*.

    >>> file_path_to_slug("docs/getting-started/installation.md")
    'getting-started-installation'
    >>> file_path_to_slug("README.md")
    'readme'
    >>> file_path_to_slug("src/auth/login.py")
    'src-auth-login-py'
    """
    p = PurePosixPath(path)

    # Strip recognised extension.
    stem = p.stem if p.suffix.lower() in _STRIP_EXTENSIONS else p.name

    # Rebuild parts without the extension; strip boring leading prefix parts.
    parts = list(p.parent.parts) + [stem]
    while parts and parts[0].lower() in _STRIP_PREFIXES:
        parts.pop(0)

    joined = "/".join(parts)
    lower = joined.lower()
    slugified = _NON_ALNUM_RE.sub("-", lower).strip("-")

    # Collapse any leftover adjacent hyphens created by sub-path separators.
    slugified = re.sub(r"-{2,}", "-", slugified)

    return slugified[:_MAX_SLUG_LEN]


def build_slug_map(items: list[tuple[UUID, str]]) -> dict[str, UUID]:
    """Return a collision-free ``slug → document_id`` mapping.

    *items* is an iterable of ``(document_id, file_path)`` pairs.

    When two paths produce the same base slug the second one gets a
    ``-<hex4>`` suffix taken from the last 4 hex digits of its UUID so the
    result is still deterministic (same UUID always gets the same suffix).
    """
    slug_to_id: dict[str, UUID] = {}
    for doc_id, file_path in items:
        base = file_path_to_slug(file_path)
        candidate = base
        if candidate in slug_to_id:
            # Append 4-char hex suffix from the document UUID.
            suffix = doc_id.hex[-4:]
            candidate = f"{base[:_MAX_SLUG_LEN - 5]}-{suffix}"
        slug_to_id[candidate] = doc_id
    return slug_to_id
