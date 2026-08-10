"""Cross-source routing — pick the repositories / collections most likely
to hold the answer to a natural-language question.

The MCP agent's biggest waste of tokens is firing a global retrieval against
every indexed source. `route(query)` runs *one* cheap query over the
metadata of every visible repo and collection, returns the top-k candidates
with a normalised confidence score in `[0, 1]` and a one-line `why`, and
lets the agent fan out to just those.

**v3 is BM25-style lexical with IDF weighting + body indexing for
collections + structural always-include for the collections pool.** v1 had
no field for code (just slug + README); v2 (2026-05-19, 03c09da) added a
module-level symbol corpus and dropped the divide-by-three normalisation;
v3 (this commit) adds:

  1. IDF weighting — generic tokens like 'payment' / 'integration' get a
     low df-derived weight, so a multi-token query like
     `"AcmePay payment provider integration"` no longer scores all four
     payment-domain repos at 0.75 just for matching 3 of 4 generic tokens.
  2. Body indexing for collections via `md_chunks.content_tsv` (the existing
     GIN-indexed tsvector built by migration `2a54ef01f78c`). A glossary
     document mentioning "AcmePay" only in body text — not headings — now
     surfaces with score ≥ 0.5.
  3. Structural always-include for collections: every `route()` call
     returns up to `top_k` collections, even if their score is 0. Below
     the weak/fallback threshold the `why` line is marked so the agent
     knows it's a "verify by reading" recommendation, not a confirmed
     match. This guarantees docs+code triangulation: the agent always
     SEES that a collection exists, even when lexical search finds
     nothing.

Searchable fields:

* **Repositories**: `host/owner/name` slug + `branch` + the first ~2K
  chars of the repo's README (`RepoDocument` whose file_path matches
  `^README`, case-insensitive) + **module-level symbol corpus**:
  distinct `qualified_name` and `file_path` strings from each
  `code_node` with `node_type='module'`. This is the chunky structural
  skeleton (one row per file); pulling it gives the router fuel for
  provider-/feature-named queries whose terms NEVER reach the README.
* **Collections**: `name` + `description` + each `MdDocument`'s heading
  texts (concatenated up to a char-budget) + **body via tsvector**:
  for each query token, an indexed `plainto_tsquery('english', :tok)
  @@ md_chunks.content_tsv` lookup tells us which collections contain
  the token in any chunk's body. SQLite (test) falls back to ILIKE on
  `md_documents.content`.

**Scoring formula:**

    idf(t)              = log((N+1) / (df(t)+1))     # Lidstone-smoothed
    weighted_coverage   = Σ idf(t) for t ∈ matched  /  Σ idf(t) for t ∈ query
    label_boost         = 1.0 + 0.5 × (|label_matched| / |query|)
    score               = min(1.0, weighted_coverage × label_boost)

`df(t)` is the document-frequency of token `t` in the **pool** (repos pool
counted separately from collections pool, cross-field within a source so a
token is counted once per source regardless of which field it appears in).
If `Σ idf == 0` (e.g. N=1 fixture, or all tokens are universally present),
weighted_coverage falls back to `|matched| / |query|` so single-source
tests don't collapse to 0.

ACL: callers MUST pass the `current_user` so the underlying scope helpers
filter out repos/collections the user can't read. The router never bypasses
this; an anonymous caller only sees PUBLIC sources.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text
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

# Words too generic to discriminate sources. IDF would push these to near
# zero weight anyway, but pre-filtering keeps the df-counting loop cheap.
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

# Char-budget for collection headings: aggregate up to ~64 KB of heading
# text per collection (was a row-count cap of 25, which barely scratched a
# Confluence mirror with thousands of pages). 64 KB ≈ ~500-1000 docs worth
# of titles + section headings.
_COLLECTION_HEADINGS_CHAR_BUDGET = 65536

# Cap how many module-level code node rows we pull *per repository* for the
# symbol corpus. A typical service ships 200–800 files; pathological mono-
# repos go above 5K. We index the first N qualified_name + file_path pairs
# only — past the cap the marginal token-add is mostly auto-generated /
# vendored noise.
_SYMBOL_CORPUS_PER_REPO_CAP = 1500

# Below this score, a collection hit is marked as weak/fallback in `why` so
# the agent treats it as a "verify by reading" recommendation rather than a
# confirmed match. Structurally we always emit the top_k collections (even
# at score 0) so the agent SEES that docs exist — chosen specifically to
# guarantee docs+code triangulation on every route call.
_COLLECTION_WEAK_THRESHOLD = 0.3


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


def _why_line(
    matched: set[str], where: str, *, idf: dict[str, float] | None = None
) -> str:
    """Short human-readable explanation for the agent ('why this match').

    When `idf` is provided, tokens whose idf clears the rare-token
    threshold (≈ df=1 in current pool — see `_rare_threshold`) get a
    `(rare)` annotation. This is debug-fuel for the agent: when one rare
    token drives a high score and the other tokens are common, the agent
    can see which match did the work.
    """
    if not matched:
        return f"matched on {where}"
    threshold = _rare_threshold(idf) if idf else None

    def _fmt(tok: str) -> str:
        repr_tok = repr(tok)
        if threshold is not None and idf and idf.get(tok, 0.0) >= threshold:
            return f"{repr_tok} (rare)"
        return repr_tok

    sample = sorted(matched)[:4]
    return f"matched on {', '.join(_fmt(t) for t in sample)} in {where}"


def _rare_threshold(idf: dict[str, float] | None) -> float | None:
    """idf value above which a token is considered 'rare' for the pool.

    Tokens with df ≤ 1 land at or above `log((N+1)/2)` — that's the
    operational definition of 'rare' for the playbook ('appears in at
    most one source'). Returns None on an empty idf-map so callers can
    short-circuit annotation.
    """
    if not idf:
        return None
    return max(idf.values()) * 0.95  # within 5% of the max — close enough


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


def _compute_idf(
    n_sources: int, df_map: dict[str, int], tokens: set[str]
) -> dict[str, float]:
    """Lidstone-smoothed inverse document frequency, computed per pool.

        idf(t) = log((N + 1) / (df(t) + 1))  if df(t) ≥ 1
        idf(t) = 0                            if df(t) == 0

    The `df == 0` override is load-bearing: a query token that exists in
    zero sources is **noise** (typo, irrelevant phrasing, term unique to
    the user's mental model). Without the override, Lidstone smoothing
    gives a noise token `idf = log(N+1)` — the maximum — which sits in
    the denominator of weighted coverage and silently zeros out real
    matches against universal-in-pool tokens. This was the failure mode
    seen on the anti-fanout test: `'work'` appeared in zero seeded repos
    but inflated the IDF denominator so heavily that the legitimate
    {`auth`, `session`, `refresh`} matches scored 0.

    Tokens with df ≥ 1: standard Lidstone. At small N (10-50) and df=N
    (token universal in pool), idf=0 — the token has no discriminating
    power. At df=1 (unique to one source), idf=log((N+1)/2) — for N=10
    that's a 20× weight ratio over a fully-generic token. BM25's
    `log((N-df+0.5)/(df+0.5))` flips sign at df > N/2 and behaves
    chaotically on small N, so we use Lidstone instead.
    """
    out: dict[str, float] = {}
    for t in tokens:
        df = df_map.get(t, 0)
        if df <= 0:
            out[t] = 0.0
        else:
            out[t] = math.log((n_sources + 1) / (df + 1))
    return out


def _combine_score(
    tokens: set[str],
    *,
    all_matched: set[str],
    label_matched: set[str],
    idf: dict[str, float],
) -> float:
    """Score = weighted_coverage × label_boost, clamped to [0, 1].

    weighted_coverage = Σ idf(t) for t ∈ matched / Σ idf(t) for t ∈ tokens
    label_boost       = 1.0 + 0.5 × (|label_matched| / |tokens|)

    When `Σ idf(t) == 0` (degenerate single-source pool where every token
    has df = N, or N = 1) the formula falls back to plain coverage
    `|matched| / |tokens|` — otherwise the formula would collapse to 0/0
    and a fresh test fixture with a single repo would score 0.0 for a
    perfectly-matching query.

    The boost is multiplicative, not additive, so a partial label match
    doesn't pay for missing content coverage (a slug match of 'runner' with
    0 README / symbol hits is still 0.0).
    """
    if not tokens:
        return 0.0
    total = len(tokens)
    total_weight = sum(idf.values())
    if total_weight <= 0.0:
        coverage = len(all_matched) / total
    else:
        coverage = sum(idf.get(t, 0.0) for t in all_matched) / total_weight
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

    # Per-repo matched-by-field. Computed once so we can both build the
    # cross-field df-map and produce final scores without re-running the
    # substring scan.
    per_repo_matched: dict[
        UUID, tuple[str, set[str], set[str], set[str]]
    ] = {}
    df_map: dict[str, int] = {t: 0 for t in tokens}
    for repo in repos:
        slug = f"{repo.host}/{repo.owner}/{repo.name}"
        slug_matched = _matches_in(tokens, f"{slug} {repo.branch or ''}")
        readme_matched = _matches_in(tokens, readme_by_repo.get(repo.id, ""))
        symbol_matched = _matches_in(
            tokens, symbol_corpus_by_repo.get(repo.id, "")
        )
        per_repo_matched[repo.id] = (
            slug,
            slug_matched,
            readme_matched,
            symbol_matched,
        )
        # Cross-field df: a token counts once per repo regardless of which
        # field it surfaced in. Counting per-field would double-count
        # 'payment' against repos that mention it in both README and
        # symbols, biasing idf low for those tokens.
        for tok in slug_matched | readme_matched | symbol_matched:
            df_map[tok] = df_map.get(tok, 0) + 1

    idf = _compute_idf(len(repos), df_map, tokens)

    scored: list[tuple[float, RouteHit]] = []
    for repo in repos:
        slug, slug_matched, readme_matched, symbol_matched = per_repo_matched[
            repo.id
        ]
        all_matched = slug_matched | readme_matched | symbol_matched
        if not all_matched:
            continue
        score = _combine_score(
            tokens,
            all_matched=all_matched,
            label_matched=slug_matched,
            idf=idf,
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
                    why=_why_line(all_matched, where, idf=idf),
                ),
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in scored[:top_k]]


async def _match_query_in_collection_bodies(
    session: AsyncSession,
    collection_ids: list[UUID],
    tokens: set[str],
) -> dict[UUID, set[str]]:
    """For each collection, return the subset of query tokens that occur in
    any document's body (chunked) within that collection.

    Postgres path uses `md_chunks.content_tsv @@ plainto_tsquery('english',
    :tok)` — the existing GIN-indexed tsvector (migration `2a54ef01f78c`,
    already used by `rag/lexical.py`). One indexed lookup per token; the
    GIN index makes each query 1-5 ms on a Confluence-scale collection.

    SQLite (unit-test) path falls back to `LOWER(md_documents.content) LIKE
    '%tok%'` — fine for fixture-scale data, terrible for prod (which
    always runs Postgres). The dialect guard is the only correctness-
    critical piece here.
    """
    matches: dict[UUID, set[str]] = {cid: set() for cid in collection_ids}
    if not tokens or not collection_ids:
        return matches

    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else ""

    if dialect != "postgresql":
        # SQLite test fallback. Fixture content is small; this is O(rows ×
        # tokens) but rows are <100 in any test seed.
        for token in tokens:
            pattern = f"%{token.lower()}%"
            rows = await session.execute(
                select(MdDocument.collection_id)
                .where(MdDocument.collection_id.in_(collection_ids))
                .where(func.lower(MdDocument.content).like(pattern))
                .distinct()
            )
            for (cid,) in rows.all():
                matches[cid].add(token)
        return matches

    # Postgres path — one indexed query per token via plainto_tsquery.
    sql = text(
        """
        SELECT DISTINCT md.collection_id
        FROM md_chunks mc
        JOIN md_documents md ON md.id = mc.document_id
        WHERE md.collection_id = ANY(:collection_ids)
          AND mc.content_tsv @@ plainto_tsquery('english', :tok)
        """
    )
    for token in tokens:
        rows = await session.execute(
            sql, {"collection_ids": collection_ids, "tok": token}
        )
        for (cid,) in rows.all():
            matches[cid].add(token)
    return matches


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
    # heading_tree is `list[dict]`; we flatten every heading-node text into
    # one string per collection (capped by a char budget) so a single
    # token-substring scan covers the structural skeleton of every doc.
    # Char-budget (vs the old row-count cap of 25) lets large Confluence
    # mirrors actually contribute their titles instead of stopping after
    # the first 25 random headings.
    headings_by_collection: dict[UUID, list[str]] = {}
    headings_bytes: dict[UUID, int] = {}
    for coll_id, tree in heading_rows.all():
        if headings_bytes.get(coll_id, 0) >= _COLLECTION_HEADINGS_CHAR_BUDGET:
            continue
        if not isinstance(tree, list):
            continue
        bucket = headings_by_collection.setdefault(coll_id, [])
        for node in tree:
            if isinstance(node, dict) and isinstance(node.get("text"), str):
                heading_text = node["text"]
                bucket.append(heading_text)
                headings_bytes[coll_id] = (
                    headings_bytes.get(coll_id, 0) + len(heading_text) + 1
                )
                if (
                    headings_bytes[coll_id]
                    >= _COLLECTION_HEADINGS_CHAR_BUDGET
                ):
                    break

    body_matches = await _match_query_in_collection_bodies(
        session, collection_ids, tokens
    )

    # Per-collection matched-by-field, computed once for both df and final
    # scoring (mirror of the repos loop).
    per_coll_matched: dict[UUID, tuple[set[str], set[str], set[str]]] = {}
    df_map: dict[str, int] = {t: 0 for t in tokens}
    for coll in collections:
        title_text = f"{coll.name} {coll.description or ''}"
        title_matched = _matches_in(tokens, title_text)
        headings = " ".join(headings_by_collection.get(coll.id, []))
        heading_matched = _matches_in(tokens, headings)
        body_matched = body_matches.get(coll.id, set())
        per_coll_matched[coll.id] = (
            title_matched,
            heading_matched,
            body_matched,
        )
        for tok in title_matched | heading_matched | body_matched:
            df_map[tok] = df_map.get(tok, 0) + 1

    idf = _compute_idf(len(collections), df_map, tokens)

    scored: list[tuple[float, RouteHit]] = []
    for coll in collections:
        title_matched, heading_matched, body_matched = per_coll_matched[
            coll.id
        ]
        all_matched = title_matched | heading_matched | body_matched
        score = _combine_score(
            tokens,
            all_matched=all_matched,
            label_matched=title_matched,
            idf=idf,
        )
        # Structural always-include: even at score 0 (no lexical match)
        # the collection is emitted with a weak/fallback why so the agent
        # SEES that a docs source exists. The playbook then requires
        # skimming the top collection's outline for entity-type questions.
        if score < _COLLECTION_WEAK_THRESHOLD:
            why = (
                "(weak/fallback) no strong lexical match — recommended to "
                "verify by reading the outline before assuming the topic is "
                "code-only"
            )
        else:
            parts: list[str] = []
            if title_matched:
                parts.append("title")
            if heading_matched:
                parts.append("headings")
            if body_matched:
                parts.append("body")
            where = "+".join(parts) if parts else "unknown"
            why = _why_line(all_matched, where, idf=idf)
        scored.append(
            (
                score,
                RouteHit(
                    kind="collection",
                    id=str(coll.id),
                    label=coll.name,
                    score=score,
                    why=why,
                ),
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in scored[:top_k]]
