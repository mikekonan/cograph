"""Provider-agnostic OIDC login endpoints (Phase 30.3).

Mounted under `/api/auth/oidc/{slug}/...`. The dance is:

1. **Authorize** — `GET /login?return_to=…` builds a state + PKCE + nonce
   row in `oidc_login_states` and 302s to the IdP `authorization_endpoint`.
2. **Callback** — IdP redirects back with `?code=…&state=…`. We look up
   the state row (single-use, marked `consumed_at`), exchange the code at
   the `token_endpoint`, verify the ID token signature against JWKS, and
   provision-or-link a Cograph user (`oidc_provisioning.find_or_create_user`).
3. We then mint Cograph cookie sessions (the same machinery as
   password login).

PAT actors cannot initiate `link/start` — token-laundering guard.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.auth.actor import AuthenticatedActor
from backend.app.auth.oidc_cipher import OIDCSecretCipher
from backend.app.auth.oidc_client import (
    IdTokenClaims,
    OIDCClient,
    generate_pkce,
    generate_state,
    hash_state,
)
from backend.app.auth.oidc_provisioning import (
    find_or_create_user,
    link_existing_user,
)
from backend.app.auth.session_cookies import mint_session_cookies
from backend.app.config import Settings
from backend.app.core.deps import (
    get_db_session,
    get_settings_dep,
    require_authenticated,
)
from backend.app.core.errors import ApiError
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.oidc_login_state import OIDCLoginState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["auth", "oidc"])


def _external_origin(request: Request, settings: Settings) -> str:
    """Resolve the public origin used for `redirect_uri`.

    `settings.auth.external_url` overrides everything; otherwise we trust
    the request's `base_url` (which honours `X-Forwarded-*` when uvicorn
    is started with `--proxy-headers`).
    """
    if settings.auth.external_url:
        return settings.auth.external_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def _redirect_uri(request: Request, settings: Settings, slug: str) -> str:
    return f"{_external_origin(request, settings)}/api/auth/oidc/{slug}/callback"


def _safe_return_to(request: Request, settings: Settings, raw: str | None) -> str:
    """Sanitise `return_to` to a same-origin path.

    Reject absolute URLs (open-redirect) and anything that doesn't start
    with `/`. Default to `/` for the FE shell to land on.
    """
    if not raw:
        return "/"
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not parsed.path.startswith("/"):
        return "/"
    return parsed.path + (f"?{parsed.query}" if parsed.query else "") + (
        f"#{parsed.fragment}" if parsed.fragment else ""
    )


def _frontend_origin(request: Request, settings: Settings) -> str:
    """Resolve the FE origin for FE-side error redirects.

    For a typical deploy backend and FE share an origin so this matches
    `_external_origin`. The OIDC error redirect lands on `/login?error=…`
    so the SPA can render a friendly message.
    """
    return _external_origin(request, settings)


def _login_error_redirect(
    *,
    request: Request,
    settings: Settings,
    code: str,
    return_to: str | None = None,
) -> RedirectResponse:
    base = _frontend_origin(request, settings)
    target = f"{base}/login?error={code}"
    if return_to:
        from urllib.parse import quote_plus

        target += f"&return_to={quote_plus(return_to)}"
    return RedirectResponse(url=target, status_code=302)


async def _load_provider(
    session: AsyncSession,
    *,
    slug: str,
    require_enabled: bool = True,
) -> IdentityProvider:
    provider = await session.scalar(
        select(IdentityProvider).where(IdentityProvider.slug == slug)
    )
    if provider is None:
        raise ApiError(404, "IDP_NOT_FOUND", "Identity provider not found")
    if require_enabled and not provider.enabled:
        raise ApiError(410, "IDP_DISABLED", "Identity provider is disabled")
    return provider


def _build_oidc_client(
    *,
    provider: IdentityProvider,
    settings: Settings,
) -> OIDCClient:
    cipher = OIDCSecretCipher(settings)
    secret = (
        cipher.decrypt(provider.client_secret_encrypted)
        if provider.client_secret_encrypted
        else None
    )
    return OIDCClient(
        issuer_url=provider.issuer_url,
        client_id=provider.client_id,
        client_secret=secret,
        scopes=list(provider.scopes),
    )


# ---------------------------------------------------------------------------
# /login — start the OIDC dance
# ---------------------------------------------------------------------------


@router.get("/{slug}/login")
async def oidc_login(
    slug: str,
    request: Request,
    return_to: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> RedirectResponse:
    provider = await _load_provider(session, slug=slug)
    client = _build_oidc_client(provider=provider, settings=settings)
    try:
        state = generate_state()
        verifier, challenge = generate_pkce()
        nonce = generate_state()
        safe_return = _safe_return_to(request, settings, return_to)

        row = OIDCLoginState(
            state_hash=hash_state(state),
            provider_id=provider.id,
            code_verifier=verifier,
            nonce=nonce,
            return_to=safe_return,
            initiated_user_id=None,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.auth.oidc_state_ttl_seconds),
        )
        session.add(row)
        await session.commit()

        url = await client.authorize_url(
            redirect_uri=_redirect_uri(request, settings, slug),
            state=state,
            code_challenge=challenge,
            nonce=nonce,
        )
        logger.info(
            "OIDC[%s] login: redirecting to authorize endpoint return_to=%s",
            slug,
            safe_return,
        )
        return RedirectResponse(url=url, status_code=302)
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Link flow — start an OIDC dance whose callback links to the current user
# ---------------------------------------------------------------------------


@router.post("/{slug}/link/start")
async def oidc_link_start(
    slug: str,
    request: Request,
    actor: AuthenticatedActor = Depends(require_authenticated),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> RedirectResponse:
    if actor.method == "pat":
        raise ApiError(
            403,
            "FORBIDDEN_PAT_LINK",
            "Personal access tokens cannot start a link flow.",
        )
    provider = await _load_provider(session, slug=slug)
    client = _build_oidc_client(provider=provider, settings=settings)
    try:
        state = generate_state()
        verifier, challenge = generate_pkce()
        nonce = generate_state()

        row = OIDCLoginState(
            state_hash=hash_state(state),
            provider_id=provider.id,
            code_verifier=verifier,
            nonce=nonce,
            return_to="/account/identities",
            initiated_user_id=actor.user.id,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.auth.oidc_state_ttl_seconds),
        )
        session.add(row)
        await session.commit()

        url = await client.authorize_url(
            redirect_uri=_redirect_uri(request, settings, slug),
            state=state,
            code_challenge=challenge,
            nonce=nonce,
        )
        return RedirectResponse(url=url, status_code=302)
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Callback — finish the dance and mint a session
# ---------------------------------------------------------------------------


async def _consume_state(
    session: AsyncSession,
    *,
    state: str,
    expected_provider_id: UUID,
) -> OIDCLoginState:
    row = await session.scalar(
        select(OIDCLoginState).where(OIDCLoginState.state_hash == hash_state(state))
    )
    if row is None:
        raise ApiError(400, "OIDC_STATE_UNKNOWN", "Login state not recognised")
    if row.provider_id != expected_provider_id:
        raise ApiError(400, "OIDC_STATE_PROVIDER_MISMATCH", "State / provider mismatch")
    if row.consumed_at is not None:
        raise ApiError(400, "OIDC_STATE_CONSUMED", "Login state already used")
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        raise ApiError(400, "OIDC_STATE_EXPIRED", "Login state expired")
    row.consumed_at = datetime.now(UTC)
    return row


async def _exchange_and_verify(
    *,
    client: OIDCClient,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    nonce: str,
    groups_claim: str | None,
) -> IdTokenClaims:
    token_set = await client.exchange_code(
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )
    return await client.verify_id_token(
        token_set.id_token,
        nonce=nonce,
        groups_claim=groups_claim,
    )


async def _handle_callback(
    *,
    slug: str,
    code: str,
    state: str,
    request: Request,
    response: Response,
    session: AsyncSession,
    settings: Settings,
) -> RedirectResponse:
    provider = await _load_provider(session, slug=slug)
    logger.info("OIDC[%s] callback received", slug)
    state_row = await _consume_state(
        session, state=state, expected_provider_id=provider.id
    )

    client = _build_oidc_client(provider=provider, settings=settings)
    try:
        try:
            claims = await _exchange_and_verify(
                client=client,
                code=code,
                code_verifier=state_row.code_verifier,
                redirect_uri=_redirect_uri(request, settings, slug),
                nonce=state_row.nonce,
                groups_claim=provider.groups_claim,
            )
        except ApiError as exc:
            await session.commit()  # persist the consumed_at flip
            logger.warning(
                "OIDC[%s] token exchange/verify failed: code=%s",
                slug,
                exc.code,
            )
            return _login_error_redirect(
                request=request,
                settings=settings,
                code=exc.code,
                return_to=state_row.return_to,
            )
    finally:
        await client.aclose()

    try:
        if state_row.initiated_user_id is not None:
            await link_existing_user(
                session,
                user_id=state_row.initiated_user_id,
                provider=provider,
                claims=claims,
            )
            user_id = state_row.initiated_user_id
            from backend.app.models.user import User as _User  # local import

            user = await session.get(_User, user_id)
            assert user is not None
            logger.info(
                "OIDC[%s] identity linked: user_id=%s subject=%s",
                slug,
                user.id,
                claims.sub,
            )
        else:
            user = await find_or_create_user(
                session,
                provider=provider,
                claims=claims,
            )
            logger.info(
                "OIDC[%s] login ok: user_id=%s email=%s subject=%s",
                slug,
                user.id,
                user.email,
                claims.sub,
            )
    except ApiError as exc:
        await session.commit()  # keep the consumed state, audit trail intact
        logger.warning(
            "OIDC[%s] provisioning failed: code=%s subject=%s",
            slug,
            exc.code,
            claims.sub,
        )
        return _login_error_redirect(
            request=request,
            settings=settings,
            code=exc.code,
            return_to=state_row.return_to,
        )

    redirect = RedirectResponse(
        url=f"{_frontend_origin(request, settings)}{state_row.return_to or '/'}",
        status_code=302,
    )
    await mint_session_cookies(
        response=redirect,
        session=session,
        user=user,
        settings=settings,
    )

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=user.id,
            target_user_id=user.id,
            event_type="oidc_login",
            metadata={
                "provider_slug": provider.slug,
                "subject": claims.sub,
                "linked": state_row.initiated_user_id is not None,
            },
        ),
    )
    await session.commit()
    return redirect


@router.get("/{slug}/callback")
async def oidc_callback_get(
    slug: str,
    code: str,
    state: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> RedirectResponse:
    return await _handle_callback(
        slug=slug,
        code=code,
        state=state,
        request=request,
        response=response,
        session=session,
        settings=settings,
    )


@router.post("/{slug}/callback")
async def oidc_callback_post(
    slug: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> RedirectResponse:
    """Used by IdPs configured with `response_mode=form_post`."""
    form = await request.form()
    code = form.get("code")
    state = form.get("state")
    if not isinstance(code, str) or not isinstance(state, str):
        raise ApiError(400, "OIDC_CALLBACK_INVALID", "Missing code/state in form post")
    return await _handle_callback(
        slug=slug,
        code=code,
        state=state,
        request=request,
        response=response,
        session=session,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Janitor (called from /api/auth/config or scheduled task in V2).
# Inline pruning here keeps the row table from growing unbounded.
# ---------------------------------------------------------------------------


async def prune_expired_states(session: AsyncSession) -> int:
    rows = (
        await session.scalars(
            select(OIDCLoginState).where(
                OIDCLoginState.expires_at < datetime.now(UTC)
            )
        )
    ).all()
    for row in rows:
        await session.delete(row)
    if rows:
        await session.commit()
    return len(rows)


__all__ = ["router", "prune_expired_states"]
