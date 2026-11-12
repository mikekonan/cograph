"""Tests for the cross-source router — both the underlying scorer and the
REST mirror at `POST /api/route`.

The hard cases are the score-shape ones:

* empty query returns nothing (`_tokenise` rejects everything → no
  results, never a random pair),
* ACL is respected — a private repo invisible to the caller must not
  surface even when its slug matches the query exactly,
* `why` is non-empty for every hit (no silent "matched on" stubs),
* score is in `[0, 1]` for every hit (the playbook keys on the 0.7 / 0.5
  thresholds — drifting above 1.0 would break it silently),
* anti-fan-out — when two repos genuinely share a topic, BOTH appear in
  the top-3 with scores within 0.5× of each other, not collapsed to one.

The MCP tool is a thin wrapper around the same `route_sources`; covering
the tool layer adds one happy-path assertion and otherwise reuses the
scoring tests via the REST mirror.
"""

from __future__ import annotations

import json

import pytest

from backend.app.core.auth import TokenType, create_token, hash_password
from backend.app.mcp.services import encode_payload
from backend.app.mcp.tools.route import _hit_payload
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import (
    CodeNodeType,
    MdCollectionVisibility,
    RepositoryStatus,
    RepositoryVisibility,
    UserRole,
)
from backend.app.models.md_collection import MdCollection, MdDocument
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.rag.source_router import _identifier_spellings, route_sources


_TEST_CSRF = "csrf-token"


async def _make_user(
    db_session, *, email: str, role: UserRole = UserRole.USER
) -> User:
    user = User(
        email=email,
        password_hash=hash_password("password-1234"),
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _authenticate(client, settings, user: User) -> None:
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_TEST_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.cookies.set(settings.auth.csrf_cookie_name, _TEST_CSRF)


async def _make_public_repo(
    db_session, *, host: str, owner: str, name: str, readme: str | None = None
) -> Repository:
    repo = Repository(
        host=host,
        owner=owner,
        name=name,
        git_url=f"https://{host}/{owner}/{name}.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repo)
    await db_session.flush()
    if readme is not None:
        db_session.add(
            RepoDocument(
                repository_id=repo.id,
                file_path="README.md",
                title="README",
                content=readme,
                content_hash="x" * 64,
                bytes=len(readme.encode()),
            )
        )
    await db_session.commit()
    await db_session.refresh(repo)
    return repo


async def _make_collection(
    db_session,
    *,
    name: str,
    description: str | None = None,
    owner: User | None = None,
    visibility: MdCollectionVisibility = MdCollectionVisibility.PUBLIC,
    heading_tree: list[dict] | None = None,
) -> MdCollection:
    coll = MdCollection(
        name=name,
        description=description,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    db_session.add(coll)
    await db_session.flush()
    if heading_tree is not None:
        db_session.add(
            MdDocument(
                collection_id=coll.id,
                source_key="doc.md",
                title="doc",
                content="placeholder",
                content_hash="y" * 64,
                bytes=11,
                heading_tree=heading_tree,
            )
        )
    await db_session.commit()
    await db_session.refresh(coll)
    return coll


# ---------- direct (route_sources) tests -------------------------------------


@pytest.mark.asyncio
async def test_route_returns_no_results_for_empty_query(db_session, settings) -> None:
    hits = await route_sources(
        db_session,
        query="",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    assert hits == []


@pytest.mark.asyncio
async def test_route_returns_no_results_for_stopwords_only(db_session, settings) -> None:
    # "the and of" tokenises to nothing — must NOT pick a random
    # repository just because the query "exists".
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="shipping",
        readme="# Shipping service\n\nHandles orders and routing.",
    )
    hits = await route_sources(
        db_session,
        query="the and of",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    assert hits == []


@pytest.mark.asyncio
async def test_route_matches_slug_token(db_session, settings) -> None:
    await _make_public_repo(
        db_session, host="github.com", owner="acme", name="shipping-api"
    )
    hits = await route_sources(
        db_session,
        query="where does shipping live",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    assert any("shipping-api" in h.label for h in hits)
    hit = next(h for h in hits if "shipping-api" in h.label)
    assert hit.kind == "repository"
    assert 0.0 <= hit.score <= 1.0
    assert hit.why  # never empty


@pytest.mark.asyncio
async def test_route_score_in_unit_interval(db_session, settings) -> None:
    # Build several matches so the normalisation has real numbers to clamp.
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="shipping",
        readme="# Shipping\nCarrier routing and OTP lookups happen here.",
    )
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="catalog",
        readme="# Catalog\nCatalog, listings, stock levels.",
    )
    hits = await route_sources(
        db_session,
        query="shipping carrier routing",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    assert hits, "expected matches against the seeded repos"
    for hit in hits:
        assert 0.0 <= hit.score <= 1.0, hit
        assert hit.why  # explanation present


@pytest.mark.asyncio
async def test_route_respects_repository_acl(db_session, settings) -> None:
    # Admin-only repo (the non-public variant of RepositoryVisibility);
    # matching query, anonymous caller. The router must hide it.
    private_repo = Repository(
        host="github.com",
        owner="acme",
        name="secret-shipping",
        git_url="https://github.com/acme/secret-shipping.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add(private_repo)
    await db_session.commit()

    anon_hits = await route_sources(
        db_session,
        query="secret shipping routing",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    # Anonymous callers must not learn about private repos via the router.
    assert all("secret-shipping" not in h.label for h in anon_hits), anon_hits


@pytest.mark.asyncio
async def test_route_anti_fanout_returns_multiple_sources(db_session, settings) -> None:
    # Two repos that genuinely share a topic ("auth"). The router MUST
    # return both, NOT collapse to one — this is the load-bearing test for
    # the playbook's "≥0.7 take all" rule (without it, the agent would
    # only ever ladder into one source).
    #
    # Hardened for IDF (router v3): we deliberately use tokens with mixed
    # df. 'auth' is shared (df=2) so IDF compresses it; 'session' and
    # 'refresh' also appear in BOTH READMEs so they're equally weighted
    # across the two — that keeps the spread small after IDF normalises.
    # If we used tokens unique to only one of the two repos, IDF would
    # push the unique-token repo to ~1.0 and the other to ~0.5, exceeding
    # the 0.5×-spread invariant. Symmetric tokens preserve the anti-fanout
    # property under IDF.
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="auth-service",
        readme="# Auth service\nOAuth login and session refresh ladder.",
    )
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="auth-middleware",
        readme="# Auth middleware\nValidates session cookies and refresh logic.",
    )
    hits = await route_sources(
        db_session,
        query="how does auth session refresh work",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    labels = [h.label for h in hits if h.kind == "repository"]
    assert "github.com/acme/auth-service" in labels
    assert "github.com/acme/auth-middleware" in labels
    # And the two scores are in the same neighbourhood — the spread must
    # not be larger than 0.5× the top score, else the playbook's
    # "include any 0.5× of top-1" rule would routinely drop the second.
    repo_hits = [h for h in hits if h.kind == "repository"]
    top = max(h.score for h in repo_hits)
    runner_up = sorted((h.score for h in repo_hits), reverse=True)[1]
    assert runner_up >= 0.5 * top, (top, runner_up)


@pytest.mark.asyncio
async def test_route_matches_collection_title_and_headings(db_session, settings) -> None:
    admin = await _make_user(
        db_session, email="admin@example.com", role=UserRole.ADMIN
    )
    await _make_collection(
        db_session,
        name="Engineering glossary",
        description="Domain terms and acronyms for the shipping team.",
        owner=admin,
        heading_tree=[
            {"text": "Carrier", "level": 2},
            {"text": "Consignee", "level": 2},
        ],
    )
    # Two collections: both get a score, ranked against each other.
    await _make_collection(
        db_session, name="Frontend notes", description="UI build scripts."
    )
    hits = await route_sources(
        db_session,
        query="what does carrier mean",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    coll_hits = [h for h in hits if h.kind == "collection"]
    assert [h.label for h in coll_hits] == ["Engineering glossary", "Frontend notes"]
    assert "carrier" in coll_hits[0].why.lower()
    assert coll_hits[0].score is not None and coll_hits[1].score is not None
    assert coll_hits[0].score > coll_hits[1].score


@pytest.mark.asyncio
async def test_route_finds_provider_only_in_code_symbols(
    db_session, settings
) -> None:
    """A provider whose name lives ONLY in code paths
    (`domain/shipping/parcelco/rate_card.go`, qualified_name
    `domain.shipping.parcelco.rate_card`) must still route to its repo with
    score ≥ 0.5. The router-then-fan-out playbook treats anything less as
    ignorable noise.

    Slug + README alone score 0.167 here: the dispatch README describes the
    service in the abstract and names no provider. The router also indexes
    module-level qualified_name and file_path tokens, and the formula
    normalises so single-source full coverage = 1.0."""
    dispatch = await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="dispatch",
        # Realistic README shape: dispatch is described in the abstract;
        # zero providers named here. All provider mentions live in code.
        readme=(
            "# Dispatch\n\nIntegration dispatcher. Adapts the internal shipping "
            "flow to provider-specific rate cards.\n"
        ),
    )
    # Module-level code nodes — the indexer produces exactly one per Go
    # file. These are the chunky structural-skeleton rows the router will
    # pull as a routing signal.
    for fname in ("rate_card", "builder_label", "process_error", "dictionary"):
        db_session.add(
            CodeNode(
                repository_id=dispatch.id,
                file_path=f"domain/shipping/parcelco/{fname}.go",
                qualified_name=f"domain.shipping.parcelco.{fname}#module",
                name=fname,
                language="go",
                node_type=CodeNodeType.MODULE,
                start_line=1,
                end_line=100,
                content=f"package parcelco // {fname}\n",
                content_hash="x" * 64,
            )
        )
    await db_session.commit()

    hits = await route_sources(
        db_session,
        query="ParcelCo shipping provider integration",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    repo_hits = [h for h in hits if h.kind == "repository"]
    dispatch_hit = next((h for h in repo_hits if "dispatch" in h.label), None)
    assert dispatch_hit is not None, (
        f"router lost dispatch entirely — symbol-name match for 'parcelco' is the "
        f"single strongest signal we have; got: {repo_hits}"
    )
    assert dispatch_hit.score >= 0.5, (
        f"router should be ≥0.5 confident when 'parcelco' appears in 4 module "
        f"qualified_names; got {dispatch_hit.score:.3f}. The playbook treats "
        f"<0.5 as ignorable, so this is the user-visible 'agent gave up' "
        f"threshold."
    )
    assert "symbol" in dispatch_hit.why.lower() or "code" in dispatch_hit.why.lower(), (
        f"why should announce the symbol-name match so the agent's debug "
        f"trail can explain WHERE the match came from; got: {dispatch_hit.why!r}"
    )


@pytest.mark.asyncio
async def test_route_does_not_regress_readme_only_matches(
    db_session, settings
) -> None:
    """When a repo has NO indexed code but matches purely via README, it
    must still cross the playbook's 0.5 threshold for full token coverage.

    Pre-fix the formula divided by 3 (slug-weight + README-weight), capping
    a README-only full coverage at 0.333 — which silently demoted any wiki-
    style repo to 'ignorable'. The 03c09da formula changed this; this test
    pins the side effect so it doesn't get reverted later.

    Hardened for IDF (router v3): with a single seeded repo (N=1), every
    matched token has df=1 → idf = log(2/2) = 0, so `Σ idf == 0` and
    `_combine_score` falls back to plain coverage. This pins that
    fallback — without it, single-source fixtures collapse to score 0.0
    and this test was the first to break."""
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="auth-docs",
        readme="# Auth docs\nDescribes the session refresh ladder.",
    )
    hits = await route_sources(
        db_session,
        query="session refresh ladder",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    repo_hits = [h for h in hits if h.kind == "repository"]
    assert repo_hits, "README-only repo must surface for a tokens-all-in-README query"
    top = repo_hits[0]
    assert top.score >= 0.5, (
        f"full README coverage with no slug hit must clear 0.5; got "
        f"{top.score:.3f}. Anything lower revives the pre-fix bug where the "
        f"agent ignored wiki-style repos."
    )


@pytest.mark.asyncio
async def test_route_does_not_overrate_generic_shipping_repos(
    db_session, settings
) -> None:
    """IDF check: the 03c09da-era formula scored every shipping-domain
    repo at 0.75 on a query like 'ParcelCo shipping provider integration' (4
    tokens, 3 generic shared across many repos, 1 unique to dispatch). The
    playbook's USE-ALL ≥ 0.7 rule then forced the agent to fan out into 5
    shipping repos when only dispatch actually contained 'parcelco'.

    Router v3 uses IDF to dampen the 3 generic tokens to near-zero weight
    while the unique 'parcelco' token carries almost all the discrimination.
    The expected outcome: dispatch ≥ 0.85, the generic shipping repos drop
    below 0.5 (out of USE-ALL territory)."""
    dispatch = await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="dispatch",
        readme=(
            "# Dispatch\n\nIntegration dispatcher. Adapts the internal shipping "
            "flow to provider-specific rate cards.\n"
        ),
    )
    # Dispatch gets parcelco symbols (the discriminator).
    for fname in ("rate_card", "builder_label", "process_error", "dictionary"):
        db_session.add(
            CodeNode(
                repository_id=dispatch.id,
                file_path=f"domain/shipping/parcelco/{fname}.go",
                qualified_name=f"domain.shipping.parcelco.{fname}#module",
                name=fname,
                language="go",
                node_type=CodeNodeType.MODULE,
                start_line=1,
                end_line=100,
                content=f"package parcelco // {fname}\n",
                content_hash="x" * 64,
            )
        )

    # Three generic shipping-domain repos. They each mention 'shipping',
    # 'provider', 'integration' in their README — none mention 'parcelco'.
    for slug in ("api", "storefront", "catalog"):
        await _make_public_repo(
            db_session,
            host="git.example.com",
            owner="team",
            name=slug,
            readme=(
                f"# {slug}\n\nGeneric shipping provider integration service. "
                "Handles routing and tracking."
            ),
        )
    await db_session.commit()

    hits = await route_sources(
        db_session,
        query="ParcelCo shipping provider integration",
        current_user=None,
        settings=settings,
        top_k=5,
    )
    repo_hits = [h for h in hits if h.kind == "repository"]
    dispatch_hit = next((h for h in repo_hits if "dispatch" in h.label), None)
    assert dispatch_hit is not None, repo_hits
    assert dispatch_hit.score >= 0.85, (
        f"dispatch uniquely contains 'parcelco' (df=1) — IDF should put it at "
        f"≥0.85, not {dispatch_hit.score:.3f}. If this drops below 0.85, the "
        f"IDF formula stopped weighting rare tokens above generic ones."
    )
    for noisy in ("api", "storefront", "catalog"):
        hit = next((h for h in repo_hits if h.label.endswith(noisy)), None)
        if hit is None:
            continue
        assert hit.score < 0.5, (
            f"{noisy} matches only generic 'shipping/provider/integration' — "
            f"IDF must push it under 0.5 (out of USE-ALL bucket). Got "
            f"{hit.score:.3f}. Without this discrimination the agent fans "
            f"out into 5 irrelevant repos."
        )


@pytest.mark.asyncio
async def test_route_collection_finds_entity_in_body(
    db_session, settings
) -> None:
    """Body-via-tsvector check: a wiki-mirror collection whose
    'ParcelCo' mention lives in document body text (NOT in title, NOT in
    headings) must surface for `route("ParcelCo")`. Pre-fix the router only
    inspected `heading_tree[*]["text"]` so the entity stayed invisible.

    On Postgres this goes through `md_chunks.content_tsv @@
    plainto_tsquery`; on SQLite (default test DB) it falls back to ILIKE
    on `md_documents.content`. Either way the collection must come back
    with score ≥ 0.5 (not weak/fallback) and 'body' in the `why`."""
    admin = await _make_user(
        db_session, email="admin@example.com", role=UserRole.ADMIN
    )
    coll = await _make_collection(
        db_session,
        name="Shipping Glossary",
        description="Domain terms.",
        owner=admin,
        heading_tree=[
            {"text": "Overview", "level": 1},
            {"text": "Carriers", "level": 2},
        ],
    )
    # Replace the placeholder doc with one whose body explicitly names
    # ParcelCo (and NOT in title or headings).
    db_session.add(
        MdDocument(
            collection_id=coll.id,
            source_key="carriers/parcelco-overview.md",
            title="Carrier notes",
            content=(
                "# Carrier notes\n\n"
                "ParcelCo is an external carrier providing parcel delivery "
                "rate cards and OTP handshakes. See the integration dispatcher "
                "for the provider-specific contract."
            ),
            content_hash="z" * 64,
            bytes=200,
            heading_tree=[
                {"text": "Carrier notes", "level": 1},
            ],
        )
    )
    await db_session.commit()
    # A second collection, so the pool is ranked (one alone is not scored).
    await _make_collection(
        db_session, name="Frontend notes", description="UI build scripts."
    )

    hits = await route_sources(
        db_session,
        query="ParcelCo",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    coll_hits = [h for h in hits if h.kind == "collection"]
    coll_hit = next((c for c in coll_hits if c.label == "Shipping Glossary"), None)
    assert coll_hit is not None, (
        f"collection with 'ParcelCo' in body must surface even when title and "
        f"headings don't mention it; got: {coll_hits}"
    )
    assert coll_hit.score >= 0.5, (
        f"single rare-token full match must clear 0.5; got {coll_hit.score:.3f}"
    )
    assert "body" in coll_hit.why.lower(), (
        f"why must announce the body-match so the agent's trail can "
        f"explain WHERE the match came from; got: {coll_hit.why!r}"
    )


@pytest.mark.asyncio
async def test_route_always_returns_top_collection_even_without_lexical_match(
    db_session, settings
) -> None:
    """Structural always-include for collections: even when zero query
    tokens appear in any collection's title/headings/body, the router MUST
    still emit the top_k collections (with a weak/fallback `why`) so the
    agent SEES that docs exist. This is the docs+code triangulation
    guarantee: the agent should never skip the collection layer just
    because lexical search missed."""
    admin = await _make_user(
        db_session, email="admin2@example.com", role=UserRole.ADMIN
    )
    coll = await _make_collection(
        db_session,
        name="Frontend Tooling Notes",
        description="UI build scripts and bundling.",
        owner=admin,
        heading_tree=[{"text": "Webpack", "level": 1}],
    )
    db_session.add(
        MdDocument(
            collection_id=coll.id,
            source_key="webpack-tips.md",
            title="Webpack tips",
            content="# Webpack tips\n\nNothing to see here about shipping.",
            content_hash="w" * 64,
            bytes=80,
            heading_tree=[{"text": "Webpack tips", "level": 1}],
        )
    )
    await db_session.commit()
    await _make_collection(
        db_session, name="Warehouse runbook", description="Stock counts."
    )

    hits = await route_sources(
        db_session,
        query="ParcelCo",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    coll_hits = [h for h in hits if h.kind == "collection"]
    assert {h.label for h in coll_hits} == {
        "Frontend Tooling Notes",
        "Warehouse runbook",
    }, (
        "collections must always be returned (structural guarantee), even "
        "when no token matched — the agent needs to see that docs exist so "
        "it can decide to skim the outline. Got: " + repr(hits)
    )
    for coll_hit in coll_hits:
        assert coll_hit.score is not None and coll_hit.score < 0.3, (
            f"no lexical match → score must be in the weak/fallback bucket; "
            f"got {coll_hit.score}"
        )
        why_lower = coll_hit.why.lower()
        assert "weak" in why_lower or "fallback" in why_lower, (
            f"weak/fallback marker missing — agent won't know to treat this "
            f"as a 'verify by reading' recommendation; got: {coll_hit.why!r}"
        )


@pytest.mark.asyncio
async def test_route_lists_the_only_collection_without_a_score(
    client, db_session, settings
) -> None:
    """With one collection there is nothing to rank it against, and plain
    coverage over a large docs mirror scored it high for unrelated questions.
    It is listed so the agent knows docs exist, with no score."""
    await _make_collection(
        db_session,
        name="Shipping Glossary",
        description="Carrier terms and acronyms.",
        heading_tree=[{"text": "Carrier", "level": 2}],
    )
    hits = await route_sources(
        db_session,
        query="carrier terms",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    [only] = [h for h in hits if h.kind == "collection"]
    assert only.label == "Shipping Glossary"
    assert only.score is None
    assert "only collection" in only.why

    # MCP drops the key, REST sends null.
    mcp_payload = json.loads(encode_payload({"collections": [_hit_payload(only)]}))
    assert "score" not in mcp_payload["collections"][0]
    response = await client.post("/api/route", json={"query": "carrier terms"})
    assert response.status_code == 200, response.text
    assert response.json()["collections"][0]["score"] is None


@pytest.mark.asyncio
async def test_route_matches_whole_words_not_substrings(
    db_session, settings
) -> None:
    """`get` must not match a repo whose text only has `target`. It still
    matches a whole word, here the camelCase part of a module name."""
    await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="build-targets",
        readme="# Build targets\n\nThe default target packs the warehouse image.",
    )
    inventory = await _make_public_repo(
        db_session, host="git.example.com", owner="team", name="inventory"
    )
    db_session.add(
        CodeNode(
            repository_id=inventory.id,
            file_path="src/stock/getLevels.ts",
            qualified_name="src/stock/getLevels",
            name="getLevels",
            language="typescript",
            node_type=CodeNodeType.MODULE,
            start_line=1,
            end_line=40,
            content="export function getLevels() {}\n",
            content_hash="g" * 64,
        )
    )
    await db_session.commit()

    hits = await route_sources(
        db_session,
        query="get",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    labels = [h.label for h in hits if h.kind == "repository"]
    assert labels == ["git.example.com/team/inventory"], hits
    assert hits[0].score is not None and hits[0].score > 0.5
    assert "symbols" in hits[0].why


@pytest.mark.asyncio
async def test_route_folds_plurals_and_skips_filler(db_session, settings) -> None:
    """`secret` finds `secrets`; `already` and `every` in a README do not
    outrank it."""
    await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="handbook",
        readme="# Handbook\n\nEvery step is already described here.",
    )
    await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="vault",
        readme="# Vault\n\nStores secrets for the warehouse.",
    )
    await db_session.commit()

    hits = await route_sources(
        db_session,
        query="is the secret already kept on every start",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    labels = [h.label for h in hits if h.kind == "repository"]
    assert labels == ["git.example.com/team/vault"], hits


async def _add_document(
    db_session, repo: Repository, *, file_path: str, content: str, kind: str | None = None
) -> None:
    """A repository document whose text is one chunk."""
    doc = RepoDocument(
        repository_id=repo.id,
        file_path=file_path,
        title=file_path,
        content=content,
        content_hash="d" * 64,
        bytes=len(content.encode()),
        kind=kind,
    )
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        RepoDocumentChunk(
            document_id=doc.id,
            chunk_index=0,
            heading_path=[],
            content=content,
            mentions=[],
        )
    )
    await db_session.commit()


def test_identifier_spellings() -> None:
    assert _identifier_spellings(
        "onDone OrderShipped HTTPServer restockShelfIfEmpty iPhone OnDone"
    ) == {
        "ondone": {"onDone", "OnDone"},
        "ordershipped": {"OrderShipped"},
        "httpserver": {"HTTPServer"},
        "restockshelfifempty": {"restockShelfIfEmpty"},
        "iphone": {"iPhone"},
    }
    assert _identifier_spellings("YAML HTTP2 v2 Shipment README 2048 rate_card") == {}


@pytest.mark.asyncio
async def test_route_finds_identifier_only_in_document_text(
    db_session, settings
) -> None:
    """The flow's event name lives only in a YAML document. No field shows
    it, so the router looks it up in document text; without that the repo
    whose README says 'flow' and 'steps' came first."""
    dispatch = await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="dispatch",
        readme="# Dispatch\n\nDispatch flow definitions for the warehouse.",
    )
    await _add_document(
        db_session,
        dispatch,
        file_path="flows/dispatch.yml",
        kind="YAML",
        content=(
            "hand_over:\n"
            "  onDone: ShipmentDispatchSucceeded\n"
            "  onError: ShipmentDispatchFailed\n"
        ),
    )
    await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="handbook",
        readme="# Handbook\n\nEach flow is a list of steps.",
    )

    hits = await route_sources(
        db_session,
        query="flow steps onDone ShipmentDispatchSucceeded",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    repo_hits = [h for h in hits if h.kind == "repository"]
    assert [h.label for h in repo_hits] == [
        "git.example.com/team/dispatch",
        "git.example.com/team/handbook",
    ], repo_hits
    assert "documents" in repo_hits[0].why
    assert repo_hits[0].score > repo_hits[1].score


@pytest.mark.asyncio
async def test_route_finds_identifier_in_code_names(db_session, settings) -> None:
    """A function name the query spells exactly finds its repo, which beats
    a README that only shares the plain words 'shelf' and 'empty'."""
    stockroom = await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="stockroom",
        readme="# Stockroom\n\nKeeps each shelf of the warehouse stocked.",
    )
    db_session.add(
        CodeNode(
            repository_id=stockroom.id,
            file_path="shelves/restock.go",
            qualified_name="shelves.restockShelfIfEmpty",
            name="restockShelfIfEmpty",
            language="go",
            node_type=CodeNodeType.FUNCTION,
            start_line=10,
            end_line=30,
            content="func restockShelfIfEmpty() {}\n",
            content_hash="k" * 64,
        )
    )
    await db_session.commit()
    await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="handbook",
        readme="# Handbook\n\nWhat to do when a shelf is empty.",
    )
    await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="labels",
        readme="# Labels\n\nPrints a label when the queue is empty.",
    )

    hits = await route_sources(
        db_session,
        query="what does restockShelfIfEmpty do when the shelf is empty",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    repo_hits = [h for h in hits if h.kind == "repository"]
    assert repo_hits[0].label == "git.example.com/team/stockroom", repo_hits
    assert "symbols" in repo_hits[0].why
    assert repo_hits[0].score > repo_hits[1].score


@pytest.mark.asyncio
async def test_route_ignores_plain_words_in_document_text(
    db_session, settings
) -> None:
    """A plain word found only in document text adds nothing: large repos
    mention most words, so looking them up pulls in noise."""
    await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="dispatch",
        readme="# Dispatch\n\nSends parcels to carriers.",
    )
    catalog = await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="catalog",
        readme="# Catalog\n\nStock levels per shelf.",
    )
    query = "dispatch polls carriers"
    before = await route_sources(
        db_session, query=query, current_user=None, settings=settings, top_k=3
    )
    await _add_document(
        db_session,
        catalog,
        file_path="docs/sync.md",
        content="The catalog polls the shelf scanners every minute.",
    )
    after = await route_sources(
        db_session, query=query, current_user=None, settings=settings, top_k=3
    )
    assert before
    assert after == before


@pytest.mark.asyncio
async def test_route_identifier_lookup_respects_acl(db_session, settings) -> None:
    """A name that only a hidden repo's document holds changes nothing for a
    caller who cannot see that repo: not the list, not the scores."""
    await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="dispatch",
        readme="# Dispatch\n\nSends parcels to carriers.",
    )
    await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="handbook",
        readme="# Handbook\n\nCarrier contacts.",
    )
    query = "carriers ShipmentDispatchSucceeded"
    before = await route_sources(
        db_session, query=query, current_user=None, settings=settings, top_k=3
    )

    hidden = Repository(
        host="git.example.com",
        owner="team",
        name="hidden-flows",
        git_url="https://git.example.com/team/hidden-flows.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add(hidden)
    await db_session.commit()
    await _add_document(
        db_session,
        hidden,
        file_path="flows/dispatch.yml",
        content="onDone: ShipmentDispatchSucceeded\n",
    )
    after = await route_sources(
        db_session, query=query, current_user=None, settings=settings, top_k=3
    )
    assert before
    assert after == before


@pytest.mark.asyncio
async def test_route_known_identifier_is_not_looked_up(db_session, settings) -> None:
    # A name a field already shows is counted there; crediting other repos
    # that mention it in documents pushes the repo that defines it down.
    tracking = await _make_public_repo(
        db_session, host="git.example.com", owner="team", name="tracking"
    )
    db_session.add(
        CodeNode(
            repository_id=tracking.id,
            file_path="src/ShipmentTracker.ts",
            qualified_name="src/ShipmentTracker",
            name="ShipmentTracker",
            language="typescript",
            node_type=CodeNodeType.MODULE,
            start_line=1,
            end_line=80,
            content="export class ShipmentTracker {}\n",
            content_hash="t" * 64,
        )
    )
    await db_session.commit()
    handbook = await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="team",
        name="handbook",
        readme="# Handbook\n\nTeam notes.",
    )
    await _add_document(
        db_session,
        handbook,
        file_path="docs/tracking.md",
        content="Use ShipmentTracker to follow a parcel.",
    )

    hits = await route_sources(
        db_session,
        query="where is ShipmentTracker",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    labels = [h.label for h in hits if h.kind == "repository"]
    assert labels == ["git.example.com/team/tracking"], hits


# ---------- REST mirror tests -------------------------------------------------


@pytest.mark.asyncio
async def test_route_rest_returns_payload_shape(client, db_session) -> None:
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="shipping",
        readme="# Shipping\nCarrier routing logic.",
    )
    response = await client.post(
        "/api/route", json={"query": "carrier routing", "top_k": 3}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == "carrier routing"
    assert isinstance(body["repositories"], list)
    assert isinstance(body["collections"], list)
    for hit in body["repositories"] + body["collections"]:
        assert set(hit.keys()) == {"kind", "id", "label", "score", "why"}
        assert 0.0 <= hit["score"] <= 1.0


@pytest.mark.asyncio
async def test_route_rest_rejects_empty_query(client) -> None:
    response = await client.post("/api/route", json={"query": "", "top_k": 3})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_route_rest_rejects_invalid_top_k(client) -> None:
    response = await client.post(
        "/api/route", json={"query": "shipping", "top_k": 99}
    )
    assert response.status_code == 422
