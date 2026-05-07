"""End-to-end coverage for OIDCClient.verify_id_token after the
python-jose -> PyJWT migration (HIGH-04).

The existing OIDC tests cover discovery, PKCE, provisioning, and admin
provider config but stop short of actually verifying a signed ID token
against a JWKS — the path where the jose-to-PyJWT swap is most likely to
silently regress. These tests mint an RSA keypair locally, sign an ID
token with it, and assert OIDCClient produces matching IdTokenClaims
and rejects tokens signed by an unrelated key.
"""

from __future__ import annotations

import base64
import time
from typing import Any
from unittest.mock import AsyncMock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.app.auth.oidc_client import OIDCClient, OIDCDiscovery
from backend.app.core.errors import ApiError


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _make_rsa_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _public_key_to_jwk(public: rsa.RSAPublicKey, kid: str) -> dict[str, Any]:
    nums = public.public_numbers()
    n_bytes = nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")
    e_bytes = nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": _b64u(n_bytes), "e": _b64u(e_bytes)}


def _private_key_pem(private: rsa.RSAPrivateKey) -> bytes:
    return private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _make_client(jwks_doc: dict[str, Any]) -> OIDCClient:
    client = OIDCClient(
        issuer_url="https://issuer.example.com",
        client_id="client-id",
        client_secret="secret",
        scopes=["openid", "email", "profile"],
    )
    client.discovery = AsyncMock(  # type: ignore[method-assign]
        return_value=OIDCDiscovery(
            issuer="https://issuer.example.com",
            authorization_endpoint="https://issuer.example.com/auth",
            token_endpoint="https://issuer.example.com/token",
            jwks_uri="https://issuer.example.com/jwks",
            end_session_endpoint=None,
            userinfo_endpoint=None,
        )
    )
    client.jwks = AsyncMock(return_value=jwks_doc)  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_verify_id_token_accepts_token_signed_by_jwks_key() -> None:
    private, public = _make_rsa_keypair()
    jwk = _public_key_to_jwk(public, kid="kid-1")
    client = _make_client({"keys": [jwk]})

    now = int(time.time())
    payload = {
        "iss": "https://issuer.example.com",
        "sub": "user-123",
        "aud": "client-id",
        "iat": now,
        "exp": now + 60,
        "nonce": "nonce-xyz",
        "email": "alice@example.com",
        "email_verified": True,
        "name": "Alice",
        "groups": ["admins", "engineers"],
    }
    token = pyjwt.encode(
        payload,
        _private_key_pem(private),
        algorithm="RS256",
        headers={"kid": "kid-1"},
    )

    claims = await client.verify_id_token(token, nonce="nonce-xyz", groups_claim="groups")

    assert claims.sub == "user-123"
    assert claims.email == "alice@example.com"
    assert claims.email_verified is True
    assert claims.name == "Alice"
    assert claims.groups == ["admins", "engineers"]
    assert claims.iss == "https://issuer.example.com"
    assert claims.aud == "client-id"


@pytest.mark.asyncio
async def test_verify_id_token_rejects_token_signed_by_unrelated_key() -> None:
    """Signing key swap — token from attacker's RSA keypair must fail."""
    legit_private, legit_public = _make_rsa_keypair()
    attacker_private, _ = _make_rsa_keypair()

    # JWKS advertises legit public key under kid-1.
    jwk = _public_key_to_jwk(legit_public, kid="kid-1")
    client = _make_client({"keys": [jwk]})

    now = int(time.time())
    bad_token = pyjwt.encode(
        {
            "iss": "https://issuer.example.com",
            "sub": "evil",
            "aud": "client-id",
            "iat": now,
            "exp": now + 60,
            "nonce": "nonce-xyz",
        },
        _private_key_pem(attacker_private),
        algorithm="RS256",
        headers={"kid": "kid-1"},  # claim same kid as legit key
    )

    with pytest.raises(ApiError) as exc:
        await client.verify_id_token(bad_token, nonce="nonce-xyz")
    assert exc.value.code == "OIDC_ID_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_verify_id_token_rejects_nonce_mismatch() -> None:
    private, public = _make_rsa_keypair()
    jwk = _public_key_to_jwk(public, kid="kid-1")
    client = _make_client({"keys": [jwk]})

    now = int(time.time())
    token = pyjwt.encode(
        {
            "iss": "https://issuer.example.com",
            "sub": "user-1",
            "aud": "client-id",
            "iat": now,
            "exp": now + 60,
            "nonce": "issued-nonce",
        },
        _private_key_pem(private),
        algorithm="RS256",
        headers={"kid": "kid-1"},
    )

    with pytest.raises(ApiError) as exc:
        await client.verify_id_token(token, nonce="different-nonce")
    assert exc.value.code == "OIDC_NONCE_MISMATCH"


@pytest.mark.asyncio
async def test_verify_id_token_rejects_expired_token() -> None:
    private, public = _make_rsa_keypair()
    jwk = _public_key_to_jwk(public, kid="kid-1")
    client = _make_client({"keys": [jwk]})

    # Issued and expired well outside the 60-second clock-skew leeway.
    expired_at = int(time.time()) - 600
    token = pyjwt.encode(
        {
            "iss": "https://issuer.example.com",
            "sub": "user-1",
            "aud": "client-id",
            "iat": expired_at - 60,
            "exp": expired_at,
            "nonce": "n",
        },
        _private_key_pem(private),
        algorithm="RS256",
        headers={"kid": "kid-1"},
    )

    with pytest.raises(ApiError) as exc:
        await client.verify_id_token(token, nonce="n")
    assert exc.value.code == "OIDC_ID_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_verify_id_token_rejects_audience_mismatch() -> None:
    private, public = _make_rsa_keypair()
    jwk = _public_key_to_jwk(public, kid="kid-1")
    client = _make_client({"keys": [jwk]})

    now = int(time.time())
    token = pyjwt.encode(
        {
            "iss": "https://issuer.example.com",
            "sub": "user-1",
            "aud": "different-client",  # not our client_id
            "iat": now,
            "exp": now + 60,
            "nonce": "n",
        },
        _private_key_pem(private),
        algorithm="RS256",
        headers={"kid": "kid-1"},
    )

    with pytest.raises(ApiError) as exc:
        await client.verify_id_token(token, nonce="n")
    assert exc.value.code == "OIDC_ID_TOKEN_INVALID"
