"""Minimal provider-agnostic OIDC client.

Built on `httpx` (HTTP) + `python-jose` (JWT/JWKS) — no `authlib` dep.
The spec calls out `authlib` but everything we need (discovery, JWKS,
PKCE, ID-token verify) is a thin wrapper over what we already have.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import jwt
from jose.exceptions import JWTError

from backend.app.core.errors import ApiError

# Public clients (no client secret) are rare in our buyer profile but supported.
_USER_AGENT = "cograph-oidc/1.0"

# Cache the discovery doc + JWKS for an hour. JWKS bumps to a fresh fetch on
# `kid` miss within `verify_id_token` so a key rotation propagates within
# one request.
_DISCOVERY_TTL_S = 3600
_JWKS_TTL_S = 3600

# Tolerate small clock skew between Cograph and the IdP (60 s standard).
_CLOCK_SKEW_S = 60


@dataclass(slots=True, frozen=True)
class OIDCDiscovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None
    userinfo_endpoint: str | None


@dataclass(slots=True, frozen=True)
class TokenSet:
    id_token: str
    access_token: str | None
    refresh_token: str | None
    token_type: str | None
    expires_in: int | None


@dataclass(slots=True, frozen=True)
class IdTokenClaims:
    sub: str
    iss: str
    aud: str
    email: str | None
    email_verified: bool
    name: str | None
    groups: list[str]
    raw: dict[str, Any]


def generate_state() -> str:
    """Opaque, unguessable state value (also used as CSRF token)."""
    return secrets.token_urlsafe(32)


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) with S256 method."""
    verifier = secrets.token_urlsafe(64)[:96]  # within 43-128 char window
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def hash_state(state: str) -> bytes:
    return hashlib.sha256(state.encode("utf-8")).digest()


class OIDCClient:
    """Per-provider OIDC client.

    Cheap to build — discovery + JWKS are cached on first use so repeated
    calls within `_DISCOVERY_TTL_S` reuse one HTTP fetch each.
    """

    def __init__(
        self,
        *,
        issuer_url: str,
        client_id: str,
        client_secret: str | None,
        scopes: list[str],
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.client_id = client_id
        self._client_secret = client_secret
        self.scopes = scopes
        self._http = http
        self._discovery: OIDCDiscovery | None = None
        self._discovery_fetched_at: float = 0.0
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at: float = 0.0

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=10.0, headers={"User-Agent": _USER_AGENT}
            )
        return self._http

    async def discovery(self) -> OIDCDiscovery:
        now = time.time()
        if self._discovery and now - self._discovery_fetched_at < _DISCOVERY_TTL_S:
            return self._discovery

        url = f"{self.issuer_url}/.well-known/openid-configuration"
        http = await self._get_http()
        try:
            resp = await http.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ApiError(
                502,
                "OIDC_DISCOVERY_FAILED",
                f"Could not reach OIDC discovery endpoint: {exc}",
            ) from exc

        data = resp.json()
        if data.get("issuer", "").rstrip("/") != self.issuer_url:
            raise ApiError(
                502,
                "OIDC_ISSUER_MISMATCH",
                "Discovery document issuer does not match configured issuer_url",
            )
        try:
            doc = OIDCDiscovery(
                issuer=data["issuer"].rstrip("/"),
                authorization_endpoint=data["authorization_endpoint"],
                token_endpoint=data["token_endpoint"],
                jwks_uri=data["jwks_uri"],
                end_session_endpoint=data.get("end_session_endpoint"),
                userinfo_endpoint=data.get("userinfo_endpoint"),
            )
        except KeyError as exc:
            raise ApiError(
                502,
                "OIDC_DISCOVERY_INVALID",
                f"Discovery document missing required field: {exc.args[0]}",
            ) from exc

        self._discovery = doc
        self._discovery_fetched_at = now
        return doc

    async def jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if (
            not force_refresh
            and self._jwks is not None
            and now - self._jwks_fetched_at < _JWKS_TTL_S
        ):
            return self._jwks

        doc = await self.discovery()
        http = await self._get_http()
        try:
            resp = await http.get(doc.jwks_uri)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ApiError(
                502,
                "OIDC_JWKS_FAILED",
                f"Could not fetch JWKS: {exc}",
            ) from exc

        self._jwks = resp.json()
        self._jwks_fetched_at = now
        return self._jwks

    async def authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        nonce: str,
        prompt: str | None = None,
    ) -> str:
        doc = await self.discovery()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
        }
        if prompt:
            params["prompt"] = prompt
        return f"{doc.authorization_endpoint}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> TokenSet:
        doc = await self.discovery()
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        if self._client_secret:
            body["client_secret"] = self._client_secret

        http = await self._get_http()
        try:
            resp = await http.post(
                doc.token_endpoint,
                data=body,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ApiError(
                502,
                "OIDC_TOKEN_EXCHANGE_FAILED",
                f"Token endpoint unreachable: {exc}",
            ) from exc

        if resp.status_code >= 400:
            raise ApiError(
                400,
                "OIDC_TOKEN_EXCHANGE_REJECTED",
                f"Token endpoint rejected exchange ({resp.status_code})",
            )

        data = resp.json()
        if "id_token" not in data:
            raise ApiError(
                502,
                "OIDC_TOKEN_EXCHANGE_INVALID",
                "Token endpoint response missing id_token",
            )
        return TokenSet(
            id_token=data["id_token"],
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            token_type=data.get("token_type"),
            expires_in=data.get("expires_in"),
        )

    async def verify_id_token(
        self,
        id_token: str,
        *,
        nonce: str,
        groups_claim: str | None = None,
    ) -> IdTokenClaims:
        try:
            header = jwt.get_unverified_header(id_token)
        except JWTError as exc:
            raise ApiError(
                401,
                "OIDC_ID_TOKEN_HEADER_INVALID",
                "ID token header could not be parsed",
            ) from exc

        kid = header.get("kid")
        alg = header.get("alg", "RS256")

        async def _find_key() -> dict[str, Any]:
            jwks = await self.jwks()
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    return key
            return {}

        key = await _find_key()
        if not key:
            # `kid` may have rotated between cache and now — refresh once.
            await self.jwks(force_refresh=True)
            key = await _find_key()
        if not key:
            raise ApiError(
                401,
                "OIDC_ID_TOKEN_KEY_NOT_FOUND",
                "Signing key for ID token not found in JWKS",
            )

        try:
            payload = jwt.decode(
                id_token,
                key,
                algorithms=[alg],
                audience=self.client_id,
                issuer=self.issuer_url,
                options={
                    "verify_at_hash": False,
                    "leeway": _CLOCK_SKEW_S,
                },
            )
        except JWTError as exc:
            raise ApiError(
                401,
                "OIDC_ID_TOKEN_INVALID",
                f"ID token failed verification: {exc}",
            ) from exc

        if payload.get("nonce") != nonce:
            raise ApiError(
                401,
                "OIDC_NONCE_MISMATCH",
                "ID token nonce does not match the one we issued",
            )

        sub = payload.get("sub")
        if not sub:
            raise ApiError(
                401,
                "OIDC_ID_TOKEN_INVALID",
                "ID token is missing the `sub` claim",
            )

        groups: list[str] = []
        if groups_claim:
            raw = payload.get(groups_claim)
            if isinstance(raw, list):
                groups = [str(g) for g in raw]
            elif isinstance(raw, str):
                groups = [raw]

        return IdTokenClaims(
            sub=str(sub),
            iss=str(payload.get("iss", self.issuer_url)),
            aud=str(payload.get("aud", self.client_id)),
            email=payload.get("email"),
            email_verified=bool(payload.get("email_verified", False)),
            name=payload.get("name"),
            groups=groups,
            raw=dict(payload),
        )

    async def end_session_url(self, *, id_token_hint: str | None = None) -> str | None:
        doc = await self.discovery()
        if not doc.end_session_endpoint:
            return None
        params: dict[str, str] = {}
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        if not params:
            return doc.end_session_endpoint
        return f"{doc.end_session_endpoint}?{urlencode(params)}"

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
