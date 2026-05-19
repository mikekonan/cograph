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

import pytest

from backend.app.core.auth import TokenType, create_token, hash_password
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import (
    CodeNodeType,
    MdCollectionVisibility,
    RepositoryStatus,
    RepositoryVisibility,
    UserRole,
)
from backend.app.models.md_collection import MdCollection, MdDocument
from backend.app.models.repo_document import RepoDocument
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.rag.source_router import route_sources


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
        name="payments",
        readme="# Payments service\n\nHandles checkout and routing.",
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
        db_session, host="github.com", owner="acme", name="payments-api"
    )
    hits = await route_sources(
        db_session,
        query="where does payments live",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    assert any("payments-api" in h.label for h in hits)
    hit = next(h for h in hits if "payments-api" in h.label)
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
        name="payments",
        readme="# Payments\nAcquirer routing and 3DS lookups happen here.",
    )
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="ledger",
        readme="# Ledger\nLedger, settlements, payouts.",
    )
    hits = await route_sources(
        db_session,
        query="payments acquirer routing",
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
        name="secret-payments",
        git_url="https://github.com/acme/secret-payments.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add(private_repo)
    await db_session.commit()

    anon_hits = await route_sources(
        db_session,
        query="secret payments routing",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    # Anonymous callers must not learn about private repos via the router.
    assert all("secret-payments" not in h.label for h in anon_hits), anon_hits


@pytest.mark.asyncio
async def test_route_anti_fanout_returns_multiple_sources(db_session, settings) -> None:
    # Two repos that genuinely share a topic ("auth"). The router MUST
    # return both, NOT collapse to one — this is the load-bearing test for
    # the playbook's "≥0.7 take all" rule (without it, the agent would
    # only ever ladder into one source).
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="auth-service",
        readme="# Auth service\nOAuth login and session refresh.",
    )
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="auth-middleware",
        readme="# Auth middleware\nValidates session cookies and refresh tokens.",
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
        description="Domain terms and acronyms for the payments team.",
        owner=admin,
        heading_tree=[
            {"text": "Acquirer", "level": 2},
            {"text": "Issuer", "level": 2},
        ],
    )
    hits = await route_sources(
        db_session,
        query="what does acquirer mean",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    coll_hits = [h for h in hits if h.kind == "collection"]
    assert coll_hits, hits
    assert coll_hits[0].label == "Engineering glossary"
    assert "acquirer" in coll_hits[0].why.lower()


@pytest.mark.asyncio
async def test_route_finds_provider_only_in_code_symbols(
    db_session, settings
) -> None:
    """Reproducer for the 'AcmePay' incident (2026-05-19): the agent in chat
    mode asked Cograph about a payment provider whose name lives ONLY in
    code paths (`domain/payments/acmepay/terminal.go`, qualified_name
    `domain.payments.acmepay.terminal`). The router-then-fan-out playbook
    requires that the right repo come back with score ≥ 0.5 — anything
    less and the agent treats the hit as ignorable noise.

    Before the fix: score was 0.167 (router only saw slug + README, neither
    of which mentions AcmePay). Runner's README describes the runner
    mechanics in the abstract; the provider lives entirely in code.

    After the fix: the router also indexes module-level qualified_name and
    file_path tokens, and the formula normalises so single-source full
    coverage = 1.0 (was 0.333 before)."""
    runner = await _make_public_repo(
        db_session,
        host="git.example.com",
        owner="svc",
        name="runner",
        # Realistic README shape: runner is described as an abstract runner;
        # zero providers named here. All provider mentions live in code.
        readme=(
            "# Runner\n\nIntegration runner. Adapts the internal payment "
            "flow to provider-specific terminals.\n"
        ),
    )
    # Module-level code nodes — the indexer produces exactly one per Go
    # file. These are the chunky structural-skeleton rows the router will
    # pull as a routing signal.
    for fname in ("terminal", "builder_card", "process_error", "dictionary"):
        db_session.add(
            CodeNode(
                repository_id=runner.id,
                file_path=f"domain/payments/acmepay/{fname}.go",
                qualified_name=f"domain.payments.acmepay.{fname}#module",
                name=fname,
                language="go",
                node_type=CodeNodeType.MODULE,
                start_line=1,
                end_line=100,
                content=f"package acmepay // {fname}\n",
                content_hash="x" * 64,
            )
        )
    await db_session.commit()

    hits = await route_sources(
        db_session,
        query="AcmePay payment provider integration",
        current_user=None,
        settings=settings,
        top_k=3,
    )
    repo_hits = [h for h in hits if h.kind == "repository"]
    runner_hit = next((h for h in repo_hits if "runner" in h.label), None)
    assert runner_hit is not None, (
        f"router lost runner entirely — symbol-name match for 'acmepay' is the "
        f"single strongest signal we have; got: {repo_hits}"
    )
    assert runner_hit.score >= 0.5, (
        f"router should be ≥0.5 confident when 'acmepay' appears in 4 module "
        f"qualified_names; got {runner_hit.score:.3f}. The playbook treats "
        f"<0.5 as ignorable, so this is the user-visible 'agent gave up' "
        f"threshold."
    )
    assert "symbol" in runner_hit.why.lower() or "code" in runner_hit.why.lower(), (
        f"why should announce the symbol-name match so the agent's debug "
        f"trail can explain WHERE the match came from; got: {runner_hit.why!r}"
    )


@pytest.mark.asyncio
async def test_route_does_not_regress_readme_only_matches(
    db_session, settings
) -> None:
    """When a repo has NO indexed code but matches purely via README, it
    must still cross the playbook's 0.5 threshold for full token coverage.

    Pre-fix the formula divided by 3 (slug-weight + README-weight), capping
    a README-only full coverage at 0.333 — which silently demoted any wiki-
    style repo to 'ignorable'. The AcmePay fix changes the formula; this
    test pins the side effect so it doesn't get reverted later."""
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


# ---------- REST mirror tests -------------------------------------------------


@pytest.mark.asyncio
async def test_route_rest_returns_payload_shape(client, db_session) -> None:
    await _make_public_repo(
        db_session,
        host="github.com",
        owner="acme",
        name="payments",
        readme="# Payments\nAcquirer routing logic.",
    )
    response = await client.post(
        "/api/route", json={"query": "acquirer routing", "top_k": 3}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == "acquirer routing"
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
        "/api/route", json={"query": "payments", "top_k": 99}
    )
    assert response.status_code == 422
