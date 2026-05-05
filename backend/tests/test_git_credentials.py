"""Phase 30.5 — git credential cipher, URL routing, redaction, askpass shim."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from backend.app.config import (
    AuthSettings,
    CorsSettings,
    DatabaseSettings,
    EmbeddingSettings,
    Environment,
    GitSettings,
    RedisSettings,
    Settings,
)
from backend.app.git.askpass import askpass_env
from backend.app.git.credentials import (
    GitCredentialCipher,
    _hostname_of,
    redact_token,
    resolve_credential_for_url,
    resolve_host_for_url,
)
from backend.app.models.git_credential import GitCredential
from backend.app.models.git_host import GitHost
from backend.app.models.user import User


def _make_settings(*, secret: str = "k0") -> Settings:
    return Settings(
        environment=Environment.TESTING,
        database=DatabaseSettings(url="sqlite+aiosqlite:///:memory:"),
        redis=RedisSettings(url="redis://localhost:6379/15"),
        git=GitSettings(checkouts_root=Path("/tmp/cograph-test")),
        auth=AuthSettings(jwt_secret=secret, secure_cookies=False),
        cors=CorsSettings(allowed_origins=[]),
        embedding=EmbeddingSettings(enabled=False),
    )


# ---------------------------------------------------------------------------
# Cipher
# ---------------------------------------------------------------------------


def test_cipher_round_trip():
    cipher = GitCredentialCipher(_make_settings())
    blob = cipher.encrypt("ghp_abc123")
    assert blob != "ghp_abc123"
    assert cipher.decrypt(blob) == "ghp_abc123"


def test_cipher_is_domain_separated_from_oidc():
    """Same jwt_secret → different ciphertext shape than OIDCSecretCipher.

    We don't compare bytes directly (Fernet randomises IVs), but we verify
    a value encrypted by GitCredentialCipher cannot be decrypted by
    OIDCSecretCipher — proves the keys are distinct.
    """
    from backend.app.auth.oidc_cipher import OIDCSecretCipher
    from backend.app.core.errors import ApiError

    settings = _make_settings()
    git = GitCredentialCipher(settings)
    oidc = OIDCSecretCipher(settings)

    blob = git.encrypt("token-x")
    with pytest.raises(ApiError):
        oidc.decrypt(blob)


# ---------------------------------------------------------------------------
# URL routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/owner/repo", "github.com"),
        ("https://github.com:443/owner/repo", "github.com"),
        ("https://Git.Example.COM/owner/repo.git", "git.example.com"),
        ("git@github.com:owner/repo.git", "github.com"),
        ("ssh://git@git.example.com:22/owner/repo.git", "git.example.com"),
        ("not-a-url", None),
    ],
)
def test_hostname_of(url, expected):
    assert _hostname_of(url) == expected


@pytest.mark.anyio
async def test_resolve_credential_picks_default_for_host(db_session):
    user = User(email="o@x", password_hash="x", name="o", role="owner")
    db_session.add(user)
    await db_session.flush()

    host = GitHost(
        slug="ghes-example",
        display_name="Example GHES",
        kind="github",
        base_url="https://git.example.com",
        api_url="https://git.example.com/api/v3",
        git_host="git.example.com",
        enabled=True,
    )
    db_session.add(host)
    await db_session.flush()

    other_host = GitHost(
        slug="github-com",
        display_name="GitHub.com",
        kind="github",
        base_url="https://github.com",
        api_url="https://api.github.com",
        git_host="github.com",
        enabled=True,
    )
    db_session.add(other_host)
    await db_session.flush()

    db_session.add_all(
        [
            GitCredential(
                host_id=host.id,
                owner_user_id=user.id,
                label="not default",
                token_encrypted="x",
                token_prefix="ghp_aaa",
                is_default=False,
            ),
            GitCredential(
                host_id=host.id,
                owner_user_id=user.id,
                label="default",
                token_encrypted="x",
                token_prefix="ghp_bbb",
                is_default=True,
            ),
            GitCredential(
                host_id=other_host.id,
                owner_user_id=user.id,
                label="github default",
                token_encrypted="x",
                token_prefix="ghp_ccc",
                is_default=True,
            ),
        ]
    )
    await db_session.commit()

    cred = await resolve_credential_for_url(
        "https://git.example.com:443/owner/repo", session=db_session
    )
    assert cred is not None
    assert cred.label == "default"

    cred = await resolve_credential_for_url(
        "https://github.com/owner/repo", session=db_session
    )
    assert cred is not None
    assert cred.label == "github default"

    cred = await resolve_credential_for_url(
        "https://example.org/owner/repo", session=db_session
    )
    assert cred is None


@pytest.mark.anyio
async def test_resolve_host_skips_disabled(db_session):
    host = GitHost(
        slug="ghes-example",
        display_name="GHES",
        kind="github",
        base_url="https://git.example.com",
        api_url="https://git.example.com/api/v3",
        git_host="git.example.com",
        enabled=False,
    )
    db_session.add(host)
    await db_session.commit()

    found = await resolve_host_for_url(
        "https://git.example.com/o/r", session=db_session
    )
    assert found is None


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_token_replaces_known_plaintext():
    msg = "git: fatal: bad token ghp_secret123 returned 401"
    assert "ghp_secret123" not in redact_token(msg, plaintext="ghp_secret123")


def test_redact_token_collapses_credential_url():
    msg = "fatal: could not read from https://x-access-token:hidden@github.com/owner/repo.git"
    out = redact_token(msg)
    assert "hidden" not in out
    assert "https://***:***@github.com" in out


def test_redact_token_handles_empty_plaintext():
    msg = "no creds in this string"
    assert redact_token(msg) == msg


# ---------------------------------------------------------------------------
# askpass tmpfile lifetime
# ---------------------------------------------------------------------------


def test_askpass_env_yields_executable_script_and_cleans_up():
    captured_path: str | None = None
    with askpass_env(plaintext_token="ghp_xyz") as env:
        captured_path = env["GIT_ASKPASS"]
        assert env["GIT_USERNAME"] == "x-access-token"
        assert env["GIT_PASSWORD"] == "ghp_xyz"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        # File exists and is owner-only.
        st = os.stat(captured_path)
        assert stat.S_IMODE(st.st_mode) & stat.S_IRWXU == stat.S_IRWXU
        assert stat.S_IMODE(st.st_mode) & 0o077 == 0  # no group / other
    assert captured_path is not None
    assert not Path(captured_path).exists()


def test_askpass_env_script_returns_credentials_when_run():
    """Spawn the askpass script with a Username/Password prompt arg and
    verify it prints the env-supplied secret. Proves the shim contract
    git relies on without needing a real `git clone`.
    """
    with askpass_env(plaintext_token="my-token", username="x-access-token") as env:
        result_user = subprocess.run(
            [env["GIT_ASKPASS"], "Username for 'https://github.com': "],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        result_pass = subprocess.run(
            [env["GIT_ASKPASS"], "Password for 'https://x-access-token@github.com': "],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
    assert result_user.stdout == "x-access-token"
    assert result_pass.stdout == "my-token"


def test_askpass_env_cleans_up_on_exception():
    captured_path: str | None = None
    with pytest.raises(RuntimeError):
        with askpass_env(plaintext_token="ghp_x") as env:
            captured_path = env["GIT_ASKPASS"]
            raise RuntimeError("simulated failure")
    assert captured_path is not None
    assert not Path(captured_path).exists()
