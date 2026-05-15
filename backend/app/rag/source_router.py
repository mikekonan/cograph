"""Cross-source routing — pick the repositories / collections most likely
to hold the answer to a natural-language question.

The MCP agent's biggest waste of tokens is firing a global retrieval against
every indexed source. `route(query)` runs *one* cheap query over the
metadata of every visible repo and collection, returns the top-k candidates
with a normalised confidence score in `[0, 1]` and a one-line `why`, and
lets the agent fan out to just those.

**v1 is BM25-style lexical only.** The plan called for "BM25 + vector"
hybrid, but Cograph has no per-source embedding (no repository.description
column, no collection-level vector) — building one requires a fresh
migration, a new embedder pass, and another corner of the embedding-cost
budget to maintain. We ship lexical-only first; once the eval shows the
router missing semantic-but-not-lexical matches, v2 can add embeddings.

Searchable fields:

* **Repositories**: `host/owner/name` slug + `branch` + the first ~2K
  chars of the repo's README (`RepoDocument` whose file_path matches
  `^README`, case-insensitive). The first 2K is the part humans actually
  describe-the-project in; later sections are install/contributing
  boilerplate that adds noise without signal.
* **Collections**: `name` + `description` + each `MdDocument`'s top
  `heading_path[0]` strings (the first level of the doc's outline).

Score is `matched_tokens / total_query_tokens` clamped to `[0, 1]` with
a small bonus when a token appears in the slug itself (`host/owner/name`)
— matching there is the strongest possible signal that the user means
that specific source.

ACL: callers MUST pass the `current_user` so the underlying scope helpers
filter out repos/collections the user can't read. The router never bypasses
this; an anonymous caller only sees PUBLIC sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.md_collection_access import apply_md_collection_read_scope
from backend.app.core.repository_access import apply_repository_read_scope
from backend.app.models.md_collection import MdCollection, MdDocument
from backend.app.models.repo_document import RepoDocument
from backend.app.models.repository import Repository
from backend.app.models.user import User


# Tokens of <2 chars are punctuation noise ("a", "of") that match too much;
# tokens above 64 chars are content rather than identifiers and should be
# treated as failure mode rather than routing fuel.
_MIN_TOKEN = 2
_MAX_TOKEN = 64

# Words too generic to discriminate sources. Adding "code" or "service" here
# is tempting but they ARE useful when paired with a domain word, so we keep
# the list short — only words that match literally every codebase.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "this", "that", "from", "what",
        "where", "which", "how", "when", "is", "in", "on", "to", "of",
        "a", "an", "are", "do", "does", "can", "could", "would",
    }
)

# Slug match counts double (a query hit in `host/owner/name` is by far the
# strongest "yes, this source" signal). Keep this conservative — too high
# and a typo'd slug fragment outweighs strong README matches; too low and
# we don't beat random README chatter.
_SLUG_HIT_WEIGHT = 2.0

# How much of a README to inspect. 2000 chars covers the "what is this"
# intro block on virtually every project; beyond it README content drifts
# into installation/CI/license boilerplate that adds match-noise.
_README_PREFIX_CHARS = 2000

# Cap how many heading_path[0] strings to gather per collection. Most
# collections have a handful of top-level sections; capping at 25 keeps the
# router's metadata fetch bounded even for pathological collections.
_COLLECTION_HEADING_CAP = 25


@dataclass(slots=True, frozen=True, kw_only=True)
class RouteHit:
    kind: str  # "repository" or "collection"
    id: str
    label: str  # slug for a repo, name for a collection
    score: float  # normalised to [0, 1]
    why: str  # one-line "matched on '<tokens>' in <field>"


def _tokenise(query: str) -> list[str]:
    """Split a query into routing tokens: lowercase, alpha-numeric, no stop
    words, length-bounded. Hyphens / dots become token boundaries so
    `host/owner/name` produces three tokens, not one."""
    raw = re.findall(r"[A-Za-z0-9]+", query.lower())
    return [
        t
        for t in raw
        if _MIN_TOKEN <= len(t) <= _MAX_TOKEN and t not in _STOPWORDS
    ]


def _score_against_text(
    tokens: set[str], text: str, *, slug_boost: bool
) -> tuple[float, set[str]]:
    """Return (raw_score, matched_tokens). Raw score = matched / total
    with a 2× weight on tokens that hit when `slug_boost=True`. Caller
    normalises across the whole candidate pool to a final [0, 1]."""
    if not tokens or not text:
        return 0.0, set()
    lowered = text.lower()
    matched: set[str] = set()
    for tok in tokens:
        if tok in lowered:
            matched.add(tok)
    if not matched:
        return 0.0, matched
    weight = _SLUG_HIT_WEIGHT if slug_boost else 1.0
    return weight * (len(matched) / len(tokens)), matched


def _why_line(matched: set[str], where: str) -> str:
    """Short human-readable explanation for the agent ('why this match')."""
    if not matched:
        return f"matched on {where}"
    sample = sorted(matched)[:4]
    return f"matched on {', '.join(repr(t) for t in sample)} in {where}"


async def route_sources(
    session: AsyncSession,
    *,
    query: str,
    current_user: User | None,
    settings: Settings,
    top_k: int = 3,
) -> list[RouteHit]:
    """Return up to `top_k` repositories AND `top_k` collections that
    match `query`. Empty query → empty list (caller should retry their
    user with a non-empty prompt rather than have the router pick a
    random pair).
    """

    tokens = set(_tokenise(query))
    if not tokens:
        return []

    repo_hits = await _route_repositories(
        session,
        tokens=tokens,
        current_user=current_user,
        settings=settings,
        top_k=top_k,
    )
    coll_hits = await _route_collections(
        session, tokens=tokens, current_user=current_user, top_k=top_k
    )
    # Don't normalise across both pools — repos and collections are
    # independent verticals (an agent uses them differently). Caller
    # gets the two ranked lists side by side. The top_k=3 each gives the
    # agent up to 6 candidates total, which the playbook's "≥0.7 take all,
    # else top-2" rule handles independently per kind.
    return repo_hits + coll_hits


async def _route_repositories(
    session: AsyncSession,
    *,
    tokens: set[str],
    current_user: User | None,
    settings: Settings,
    top_k: int,
) -> list[RouteHit]:
    scoped = apply_repository_read_scope(
        select(Repository), settings=settings, current_user=current_user
    )
    repos = list((await session.scalars(scoped)).all())
    if not repos:
        return []

    # Pull each repo's README first-section in one shot. Filtering on
    # `lower(file_path) LIKE 'readme%'` covers README.md, README.rst,
    # readme.txt, README — every casing we've seen in indexed repos.
    repo_ids = [r.id for r in repos]
    readme_rows = await session.execute(
        select(RepoDocument.repository_id, RepoDocument.content)
        .where(RepoDocument.repository_id.in_(repo_ids))
        .where(func.lower(RepoDocument.file_path).like("readme%"))
    )
    readme_by_repo: dict[UUID, str] = {}
    for repo_id, content in readme_rows.all():
        if repo_id in readme_by_repo:
            continue  # first README wins; ignore alt-language READMEs
        readme_by_repo[repo_id] = (content or "")[:_README_PREFIX_CHARS]

    scored: list[tuple[float, RouteHit]] = []
    for repo in repos:
        slug = f"{repo.host}/{repo.owner}/{repo.name}"
        slug_score, slug_matched = _score_against_text(
            tokens, f"{slug} {repo.branch or ''}", slug_boost=True
        )
        readme = readme_by_repo.get(repo.id, "")
        readme_score, readme_matched = _score_against_text(
            tokens, readme, slug_boost=False
        )
        all_matched = slug_matched | readme_matched
        if not all_matched:
            continue
        raw = slug_score + readme_score
        # Clamp to [0, 1]. The max achievable is (SLUG_HIT_WEIGHT + 1) when
        # every token matches both slug and README; divide by that to
        # normalise.
        score = min(1.0, raw / (_SLUG_HIT_WEIGHT + 1.0))
        where = "slug" if slug_matched else "README"
        if slug_matched and readme_matched:
            where = "slug+README"
        scored.append(
            (
                score,
                RouteHit(
                    kind="repository",
                    id=str(repo.id),
                    label=slug,
                    score=score,
                    why=_why_line(all_matched, where),
                ),
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in scored[:top_k]]


async def _route_collections(
    session: AsyncSession,
    *,
    tokens: set[str],
    current_user: User | None,
    top_k: int,
) -> list[RouteHit]:
    scoped = apply_md_collection_read_scope(
        select(MdCollection), current_user=current_user
    )
    collections = list((await session.scalars(scoped)).all())
    if not collections:
        return []

    collection_ids = [c.id for c in collections]
    heading_rows = await session.execute(
        select(MdDocument.collection_id, MdDocument.heading_tree).where(
            MdDocument.collection_id.in_(collection_ids)
        )
    )
    # heading_tree is `list[dict]`; we flatten the first level into one
    # string per collection (capped) so a single token-substring scan
    # covers the structural skeleton of every doc in the collection.
    headings_by_collection: dict[UUID, list[str]] = {}
    for coll_id, tree in heading_rows.all():
        bucket = headings_by_collection.setdefault(coll_id, [])
        if len(bucket) >= _COLLECTION_HEADING_CAP:
            continue
        if not isinstance(tree, list):
            continue
        for node in tree:
            if isinstance(node, dict) and isinstance(node.get("text"), str):
                bucket.append(node["text"])
                if len(bucket) >= _COLLECTION_HEADING_CAP:
                    break

    scored: list[tuple[float, RouteHit]] = []
    for coll in collections:
        title_text = f"{coll.name} {coll.description or ''}"
        title_score, title_matched = _score_against_text(
            tokens, title_text, slug_boost=True
        )
        headings = " ".join(headings_by_collection.get(coll.id, []))
        heading_score, heading_matched = _score_against_text(
            tokens, headings, slug_boost=False
        )
        all_matched = title_matched | heading_matched
        if not all_matched:
            continue
        raw = title_score + heading_score
        score = min(1.0, raw / (_SLUG_HIT_WEIGHT + 1.0))
        where = "title"
        if title_matched and heading_matched:
            where = "title+headings"
        elif heading_matched:
            where = "headings"
        scored.append(
            (
                score,
                RouteHit(
                    kind="collection",
                    id=str(coll.id),
                    label=coll.name,
                    score=score,
                    why=_why_line(all_matched, where),
                ),
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in scored[:top_k]]
