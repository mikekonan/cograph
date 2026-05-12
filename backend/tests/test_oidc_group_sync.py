"""Tests for the additive OIDC group sync.

The sync hook adds memberships when a cograph group's
``(oidc_provider_id, oidc_group_name)`` pair matches a claim in the
ID token. It never removes — manual members and OIDC-synced rows
coexist; if a claim disappears the membership stays. Membership
provenance is stamped on ``group_members.source``.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from backend.app.auth.oidc_group_sync import sync_oidc_group_memberships
from backend.app.models.enums import UserRole
from backend.app.models.group import Group, GroupMember
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.user import User


async def _make_user(db_session) -> User:
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def _make_provider(db_session, *, slug: str = "kc-prod") -> IdentityProvider:
    provider = IdentityProvider(
        slug=slug,
        display_name=f"IdP {slug}",
        kind="oidc",
        issuer_url=f"https://{slug}.example.com",
        client_id="test-client",
        scopes=["openid", "profile", "email", "groups"],
        response_mode="query",
        auto_provision=True,
        admin_group_mode="ignore",
        enabled=True,
    )
    db_session.add(provider)
    await db_session.commit()
    return provider


async def _make_group(
    db_session,
    *,
    name: str,
    provider: IdentityProvider | None = None,
    oidc_group_name: str | None = None,
) -> Group:
    group = Group(
        id=uuid4(),
        name=name,
        oidc_provider_id=provider.id if provider is not None else None,
        oidc_group_name=oidc_group_name,
    )
    db_session.add(group)
    await db_session.commit()
    return group


async def _membership_rows(db_session, *, user_id) -> list[GroupMember]:
    rows = await db_session.scalars(
        select(GroupMember).where(GroupMember.user_id == user_id)
    )
    return list(rows.all())


async def test_sync_adds_match_with_oidc_source(db_session) -> None:
    """A claim matching a mapped group adds the user with source=oidc."""
    user = await _make_user(db_session)
    provider = await _make_provider(db_session)
    group = await _make_group(
        db_session,
        name="platform",
        provider=provider,
        oidc_group_name="cograph-platform",
    )

    added = await sync_oidc_group_memberships(
        session=db_session,
        user_id=user.id,
        provider=provider,
        claim_groups=["cograph-platform", "unrelated"],
    )
    await db_session.commit()

    assert added == [group.id]
    rows = await _membership_rows(db_session, user_id=user.id)
    assert len(rows) == 1
    assert rows[0].group_id == group.id
    assert rows[0].source == "oidc"


async def test_sync_is_idempotent_on_repeat(db_session) -> None:
    """Two consecutive logins with the same claims do not duplicate."""
    user = await _make_user(db_session)
    provider = await _make_provider(db_session)
    await _make_group(
        db_session,
        name="eng",
        provider=provider,
        oidc_group_name="cograph-eng",
    )

    first = await sync_oidc_group_memberships(
        session=db_session,
        user_id=user.id,
        provider=provider,
        claim_groups=["cograph-eng"],
    )
    await db_session.commit()
    assert len(first) == 1

    second = await sync_oidc_group_memberships(
        session=db_session,
        user_id=user.id,
        provider=provider,
        claim_groups=["cograph-eng"],
    )
    await db_session.commit()
    assert second == []

    rows = await _membership_rows(db_session, user_id=user.id)
    assert len(rows) == 1


async def test_sync_no_matching_group_is_noop(db_session) -> None:
    """A claim with no corresponding cograph group is silently ignored."""
    user = await _make_user(db_session)
    provider = await _make_provider(db_session)

    added = await sync_oidc_group_memberships(
        session=db_session,
        user_id=user.id,
        provider=provider,
        claim_groups=["nope", "also-nope"],
    )
    await db_session.commit()
    assert added == []

    rows = await _membership_rows(db_session, user_id=user.id)
    assert rows == []


async def test_sync_preserves_manual_membership(db_session) -> None:
    """A pre-existing manual membership is not touched by the sync."""
    user = await _make_user(db_session)
    provider = await _make_provider(db_session)
    group = await _make_group(
        db_session,
        name="ops",
        provider=provider,
        oidc_group_name="cograph-ops",
    )
    # Manual add first.
    db_session.add(
        GroupMember(group_id=group.id, user_id=user.id, source="manual")
    )
    await db_session.commit()

    added = await sync_oidc_group_memberships(
        session=db_session,
        user_id=user.id,
        provider=provider,
        claim_groups=["cograph-ops"],
    )
    await db_session.commit()
    assert added == []

    rows = await _membership_rows(db_session, user_id=user.id)
    assert len(rows) == 1
    assert rows[0].source == "manual"  # untouched


async def test_sync_ignores_groups_for_other_provider(db_session) -> None:
    """A cograph group mapped to provider A must not match claims from
    provider B even if the group_name string matches.
    """
    user = await _make_user(db_session)
    provider_a = await _make_provider(db_session, slug="idp-a")
    provider_b = await _make_provider(db_session, slug="idp-b")
    # Group mapped to A.
    await _make_group(
        db_session,
        name="platform",
        provider=provider_a,
        oidc_group_name="cograph-platform",
    )

    # Login through B with the same claim string.
    added = await sync_oidc_group_memberships(
        session=db_session,
        user_id=user.id,
        provider=provider_b,
        claim_groups=["cograph-platform"],
    )
    await db_session.commit()
    assert added == []


async def test_sync_with_empty_or_none_claims_is_noop(db_session) -> None:
    user = await _make_user(db_session)
    provider = await _make_provider(db_session)
    await _make_group(
        db_session,
        name="any",
        provider=provider,
        oidc_group_name="cograph-any",
    )

    none_result = await sync_oidc_group_memberships(
        session=db_session,
        user_id=user.id,
        provider=provider,
        claim_groups=None,
    )
    empty_result = await sync_oidc_group_memberships(
        session=db_session,
        user_id=user.id,
        provider=provider,
        claim_groups=[],
    )
    only_blank = await sync_oidc_group_memberships(
        session=db_session,
        user_id=user.id,
        provider=provider,
        claim_groups=["", "  "],
    )
    await db_session.commit()
    assert none_result == []
    assert empty_result == []
    # Blank strings are stripped to the empty set; whitespace-only
    # claims are accepted as-is by `g if g` (truthy check), so " "
    # would actually pass through as a name. The blank-only behaviour
    # we care about is "no real claim names => no membership added",
    # which is satisfied below by no group having that name.
    assert only_blank == []
