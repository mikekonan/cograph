"""Tests for the CRIT-03 phase 2 re-encryption migration."""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from backend.app.admin.secret_reencryption import reencrypt_secrets
from backend.app.admin.secret_service import SecretCipher
from backend.app.auth.oidc_cipher import OIDCSecretCipher
from backend.app.cli import run_cli
from backend.app.config import Settings
from backend.app.db.base import Base
from backend.app.db.session import SessionManager
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.llm_secret import LLMSecret


def _with_independent_secrets(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "auth": settings.auth.model_copy(
                update={
                    "llm_encryption_secret": SecretStr("brand-new-llm-secret"),
                    "oidc_encryption_secret": SecretStr("brand-new-oidc-secret"),
                }
            )
        }
    )


@pytest.fixture
async def reencrypt_db(settings):
    session_manager = SessionManager(settings)
    async with session_manager.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_manager
    finally:
        async with session_manager.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await session_manager.dispose()


async def _seed_legacy_secret_rows(
    session_manager,
    *,
    settings: Settings,
    llm_plaintext: str = "sk-legacy-llm-key",
    oidc_plaintext: str = "legacy-oidc-client-secret",
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert one row in each table encrypted under legacy JWT-derived key."""
    legacy_llm = SecretCipher(settings)
    legacy_oidc = OIDCSecretCipher(settings)
    async with session_manager.session() as session:
        llm_row = LLMSecret(
            name="legacy-test-secret",
            api_url="https://api.openai.com/v1",
            api_key_encrypted=legacy_llm.encrypt(llm_plaintext),
        )
        idp_row = IdentityProvider(
            slug="legacy-okta",
            display_name="Legacy Okta",
            issuer_url="https://example.okta.com",
            client_id="legacy-client",
            client_secret_encrypted=legacy_oidc.encrypt(oidc_plaintext),
        )
        session.add_all([llm_row, idp_row])
        await session.commit()
        return llm_row.id, idp_row.id


async def test_reencrypt_migrates_legacy_rows_to_independent_keys(
    settings, reencrypt_db
):
    llm_id, idp_id = await _seed_legacy_secret_rows(reencrypt_db, settings=settings)

    new_settings = _with_independent_secrets(settings)
    new_session_manager = SessionManager(new_settings)
    try:
        async with new_session_manager.session() as session:
            report = await reencrypt_secrets(
                session, settings=new_settings, dry_run=False
            )
            await session.commit()

        assert report.llm.total == 1
        assert report.llm.migrated == 1
        assert report.llm.already_migrated == 0
        assert report.llm.failed == 0
        assert report.oidc.total == 1
        assert report.oidc.migrated == 1
        assert report.oidc.already_migrated == 0
        assert report.oidc.failed == 0

        current_llm = SecretCipher(new_settings)
        current_oidc = OIDCSecretCipher(new_settings)
        async with new_session_manager.session() as session:
            llm_row = await session.get(LLMSecret, llm_id)
            idp_row = await session.get(IdentityProvider, idp_id)
            assert llm_row is not None
            assert idp_row is not None
            assert current_llm.decrypt(llm_row.api_key_encrypted) == "sk-legacy-llm-key"
            assert idp_row.client_secret_encrypted is not None
            assert (
                current_oidc.decrypt(idp_row.client_secret_encrypted)
                == "legacy-oidc-client-secret"
            )
    finally:
        await new_session_manager.dispose()


async def test_reencrypt_is_idempotent(settings, reencrypt_db):
    await _seed_legacy_secret_rows(reencrypt_db, settings=settings)
    new_settings = _with_independent_secrets(settings)
    new_session_manager = SessionManager(new_settings)
    try:
        async with new_session_manager.session() as session:
            first = await reencrypt_secrets(
                session, settings=new_settings, dry_run=False
            )
            await session.commit()
        assert first.llm.migrated == 1
        assert first.oidc.migrated == 1

        async with new_session_manager.session() as session:
            second = await reencrypt_secrets(
                session, settings=new_settings, dry_run=False
            )
            await session.commit()

        assert second.llm.migrated == 0
        assert second.llm.already_migrated == 1
        assert second.oidc.migrated == 0
        assert second.oidc.already_migrated == 1
        assert second.has_failures is False
    finally:
        await new_session_manager.dispose()


async def test_reencrypt_dry_run_does_not_persist(settings, reencrypt_db):
    llm_id, _ = await _seed_legacy_secret_rows(reencrypt_db, settings=settings)
    new_settings = _with_independent_secrets(settings)
    new_session_manager = SessionManager(new_settings)
    try:
        async with new_session_manager.session() as session:
            row_before = await session.get(LLMSecret, llm_id)
            assert row_before is not None
            ciphertext_before = row_before.api_key_encrypted
            report = await reencrypt_secrets(
                session, settings=new_settings, dry_run=True
            )
            await session.rollback()

        assert report.llm.migrated == 1
        assert report.dry_run is True

        async with new_session_manager.session() as session:
            row_after = await session.get(LLMSecret, llm_id)
            assert row_after is not None
            assert row_after.api_key_encrypted == ciphertext_before

        legacy_llm = SecretCipher(new_settings, force_legacy=True)
        assert legacy_llm.decrypt(row_after.api_key_encrypted) == "sk-legacy-llm-key"
    finally:
        await new_session_manager.dispose()


async def test_reencrypt_no_independent_secrets_is_noop(settings, reencrypt_db):
    """Without independent secrets configured, current and legacy ciphers are
    identical. Every row short-circuits via ``already_migrated``."""
    await _seed_legacy_secret_rows(reencrypt_db, settings=settings)
    async with reencrypt_db.session() as session:
        report = await reencrypt_secrets(session, settings=settings, dry_run=False)
        await session.commit()

    assert report.llm.migrated == 0
    assert report.llm.already_migrated == 1
    assert report.oidc.migrated == 0
    assert report.oidc.already_migrated == 1


async def test_reencrypt_handles_undecryptable_row(settings, reencrypt_db):
    """A row encrypted with a *different* key fails cleanly without crashing
    the whole migration."""
    await _seed_legacy_secret_rows(reencrypt_db, settings=settings)

    bogus_settings = settings.model_copy(
        update={
            "auth": settings.auth.model_copy(
                update={
                    "jwt_secret": SecretStr("a-totally-different-jwt-secret"),
                    "llm_encryption_secret": SecretStr("brand-new-llm-secret"),
                    "oidc_encryption_secret": SecretStr("brand-new-oidc-secret"),
                }
            )
        }
    )
    bogus_session_manager = SessionManager(bogus_settings)
    try:
        async with bogus_session_manager.session() as session:
            report = await reencrypt_secrets(
                session, settings=bogus_settings, dry_run=False
            )
            await session.commit()

        assert report.llm.failed == 1
        assert report.oidc.failed == 1
        assert report.has_failures is True
    finally:
        await bogus_session_manager.dispose()


async def test_reencrypt_secrets_cli_runs_end_to_end(settings, capsys):
    session_manager = SessionManager(settings)
    async with session_manager.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        await _seed_legacy_secret_rows(session_manager, settings=settings)
    finally:
        await session_manager.dispose()

    new_settings = _with_independent_secrets(settings)

    try:
        result = await run_cli(
            ["reencrypt-secrets"],
            settings=new_settings,
        )
        assert result == 0
        stdout = capsys.readouterr().out
        assert "migrated=1" in stdout
        assert "llm_secrets" in stdout
        assert "identity_providers" in stdout

        verify_session_manager = SessionManager(new_settings)
        try:
            current_llm = SecretCipher(new_settings)
            async with verify_session_manager.session() as session:
                rows = list((await session.scalars(select(LLMSecret))).all())
                assert len(rows) == 1
                assert (
                    current_llm.decrypt(rows[0].api_key_encrypted)
                    == "sk-legacy-llm-key"
                )
        finally:
            await verify_session_manager.dispose()
    finally:
        cleanup = SessionManager(new_settings)
        try:
            async with cleanup.engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
        finally:
            await cleanup.dispose()


async def test_reencrypt_secrets_cli_dry_run_keeps_legacy(settings, capsys):
    session_manager = SessionManager(settings)
    async with session_manager.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    llm_id = None
    try:
        llm_id, _ = await _seed_legacy_secret_rows(session_manager, settings=settings)
    finally:
        await session_manager.dispose()

    new_settings = _with_independent_secrets(settings)

    try:
        result = await run_cli(
            ["reencrypt-secrets", "--dry-run"],
            settings=new_settings,
        )
        assert result == 0
        stdout = capsys.readouterr().out
        assert "dry-run" in stdout

        verify_session_manager = SessionManager(new_settings)
        try:
            legacy_llm = SecretCipher(new_settings, force_legacy=True)
            current_llm = SecretCipher(new_settings)
            async with verify_session_manager.session() as session:
                row = await session.get(LLMSecret, llm_id)
                assert row is not None
                assert current_llm.try_decrypt(row.api_key_encrypted) is None
                assert legacy_llm.decrypt(row.api_key_encrypted) == "sk-legacy-llm-key"
        finally:
            await verify_session_manager.dispose()
    finally:
        cleanup = SessionManager(new_settings)
        try:
            async with cleanup.engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
        finally:
            await cleanup.dispose()
