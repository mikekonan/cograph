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
  `^README`, case-insensitive) + **module-level symbol corpus**:
  distinct `qualified_name` and `file_path` strings from each
  `code_node` with `node_type='module'`. This is the chunky structural
  skeleton (one row per file); pulling it gives the router fuel for
  provider-/feature-named queries whose terms NEVER reach the README
  (e.g. routing "AcmePay" to runner whose `domain/payments/acmepay/*.go`
  files lit up under the indexer but whose README describes only the
  abstract runner).
* **Collections**: `name` + `description` + each `MdDocument`'s top
  `heading_path[0]` strings (the first level of the doc's outline).

**Scoring formula** (changed 2026-05-19 alongside the symbol-corpus
addition):

    coverage    = |all_matched_tokens|        / |query_tokens|
    label_boost = 1.0 + 0.5 × (|label_matched| / |query_tokens|)
    score       = min(1.0, coverage × label_boost)

`label_matched` is slug-matched tokens for a repo, title/name-matched
tokens for a collection. The old formula divided by (slug_weight + 1) =
3, which capped any single-source full-coverage at 0.333 — silently
demoting README-only or symbol-only matches below the playbook's 0.5/0.7
thresholds. The new formula gives single-source full coverage = 1.0
and only multiplies (up to 1.5×) when the label itself matches.

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
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType
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

# How much of a README to inspect. 2000 chars covers the "what is this"
# intro block on virtually every project; beyond it README content drifts
# into installation/CI/license boilerplate that adds match-noise.
_README_PREFIX_CHARS = 2000

# Cap how many heading_path[0] strings to gather per collection. Most
# collections have a handful of top-level sections; capping at 25 keeps the
# router's metadata fetch bounded even for pathological collections.
_COLLECTION_HEADING_CAP = 25

# Cap how many module-level code node rows we pull *per repository* for the
# symbol corpus. A typical service ships 200–800 files; pathological mono-
# repos go above 5K. We index the first N qualified_name + file_path pairs
# only — past the cap the marginal token-add is mostly auto-generated /
# vendored noise.
_SYMBOL_CORPUS_PER_REPO_CAP = 1500


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


def _matches_in(tokens: set[str], text: str) -> set[str]:
    """Return the subset of `tokens` that appear as a substring in `text`
    (case-insensitive). Substring match — not whole-word — so `acmepay` hits
    inside `domain.payments.acmepay.terminal`, and a slug fragment like
    `runner` hits inside `git.example.com/svc/runner`.

    Scoring lives in `_combine_score`; this is just the field-level
    matcher."""
    if not tokens or not text:
        return set()
    lowered = text.lower()
    return {tok for tok in tokens if tok in lowered}


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


async def _load_symbol_corpus(
    session: AsyncSession, repo_ids: list[UUID]
) -> dict[UUID, str]:
    """For each repo, pull a single text blob containing distinct module-
    level `qualified_name` and `file_path` strings — separated by spaces so
    `_matches_in`'s substring scan can hit individual path/dotted
    tokens.

    Module-level rows only (`node_type = 'module'`) — that's the one-row-
    per-file structural skeleton. We deliberately ignore functions / classes
    / methods: each file typically has dozens, the names repeat, and adding
    them would balloon the corpus to 50× the size with marginal new
    information. The substring search in `_matches_in` matches a
    token like `acmepay` against any qualified_name fragment it appears in,
    so the module rows alone are enough to surface `domain.payments.acmepay.*`.

    Rows per repo are capped at `_SYMBOL_CORPUS_PER_REPO_CAP` — sorted by
    `qualified_name` for determinism (alphabetical hits the prefix tree of
    `domain/api/auth/…` first; auto-generated rows tend to sort later so the
    cap discards noise rather than signal)."""
    if not repo_ids:
        return {}
    rows = await session.execute(
        select(
            CodeNode.repository_id,
            CodeNode.qualified_name,
            CodeNode.file_path,
        )
        .where(CodeNode.repository_id.in_(repo_ids))
        .where(CodeNode.node_type == CodeNodeType.MODULE)
        .order_by(CodeNode.repository_id, CodeNode.qualified_name)
    )
    per_repo: dict[UUID, list[str]] = {}
    counts: dict[UUID, int] = {}
    for repo_id, qname, fpath in rows.all():
        if counts.get(repo_id, 0) >= _SYMBOL_CORPUS_PER_REPO_CAP:
            continue
        bucket = per_repo.setdefault(repo_id, [])
        if qname:
            bucket.append(qname)
        if fpath:
            bucket.append(fpath)
        counts[repo_id] = counts.get(repo_id, 0) + 1
    return {repo_id: " ".join(items) for repo_id, items in per_repo.items()}


def _combine_score(
    tokens: set[str],
    *,
    all_matched: set[str],
    label_matched: set[str],
) -> float:
    """Score = coverage × label_boost, clamped to [0, 1].

    coverage    = fraction of query tokens matched in ANY field (0..1)
    label_boost = 1.0 + 0.5 × (fraction matched in the label/slug/title)
                  → 1.0 when no label hit, 1.5 when the entire query is in
                  the label. The boost is multiplicative, not additive, so a
                  partial label match doesn't pay for missing content
                  coverage (a slug match of 'runner' with 0 README/symbol
                  hits is still 0.0 — we want substance behind the label).
    """
    if not tokens:
        return 0.0
    total = len(tokens)
    coverage = len(all_matched) / total
    label_boost = 1.0 + 0.5 * (len(label_matched) / total)
    return min(1.0, coverage * label_boost)


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

    symbol_corpus_by_repo = await _load_symbol_corpus(session, repo_ids)

    scored: list[tuple[float, RouteHit]] = []
    for repo in repos:
        slug = f"{repo.host}/{repo.owner}/{repo.name}"
        slug_matched = _matches_in(tokens, f"{slug} {repo.branch or ''}")
        readme = readme_by_repo.get(repo.id, "")
        readme_matched = _matches_in(tokens, readme)
        symbol_text = symbol_corpus_by_repo.get(repo.id, "")
        symbol_matched = _matches_in(tokens, symbol_text)
        all_matched = slug_matched | readme_matched | symbol_matched
        if not all_matched:
            continue
        score = _combine_score(
            tokens, all_matched=all_matched, label_matched=slug_matched
        )
        # Compose the `why` so the agent's debug trail can see which field
        # produced the hit — useful when the score is borderline and the
        # operator wants to understand which signal dominated.
        parts: list[str] = []
        if slug_matched:
            parts.append("slug")
        if readme_matched:
            parts.append("README")
        if symbol_matched:
            parts.append("symbols")
        where = "+".join(parts) if parts else "unknown"
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
        title_matched = _matches_in(tokens, title_text)
        headings = " ".join(headings_by_collection.get(coll.id, []))
        heading_matched = _matches_in(tokens, headings)
        all_matched = title_matched | heading_matched
        if not all_matched:
            continue
        score = _combine_score(
            tokens, all_matched=all_matched, label_matched=title_matched
        )
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
