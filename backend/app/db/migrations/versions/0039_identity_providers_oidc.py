"""Identity providers + OIDC login states + user identities.

Revision ID: 0039_identity_providers_oidc
Revises: 0038_personal_access_tokens

Phase 30.3 — provider-agnostic OIDC SSO.

Adds three tables:
- `identity_providers` — owner-managed OIDC IdPs (Okta / Auth0 / Azure AD /
  Keycloak / Google / JumpCloud / any compliant provider). `client_secret`
  is encrypted by `OIDCSecretCipher` (Fernet, derived from app secret).
- `user_identities` — links a user to an IdP `sub`. UNIQUE on
  `(provider_id, subject)` so multiple IdP rows can share an issuer URL
  (e.g. two Okta apps in the same Cograph instance).
- `oidc_login_states` — DB-backed PKCE / nonce / state cache for
  in-flight OIDC dances. Survives worker restart; we treat Postgres as the
  durable store and Redis as cache-not-state.

Group → admin promotion is OFF by default (`admin_group_mode='ignore'`).
Owner must explicitly opt into `owner_delegated`. Group removal NEVER
demotes — owner action required (lost-group must not lock owner out).

Local password auth stays first-class — instance can run with OIDC,
without OIDC, or with both side-by-side.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0039_identity_providers_oidc"
down_revision = "0038_personal_access_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_providers",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'oidc'"),
        ),
        sa.Column("issuer_url", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY['openid','profile','email']::text[]"),
        ),
        sa.Column(
            "response_mode",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'query'"),
        ),
        sa.Column("groups_claim", sa.Text(), nullable=True),
        sa.Column(
            "domain_allowlist",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "auto_provision",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "admin_group_mode",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'ignore'"),
        ),
        sa.Column(
            "admin_groups",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("slug", name="uq_identity_providers_slug"),
        sa.CheckConstraint(
            "kind IN ('oidc')",
            name="ck_identity_providers_kind",
        ),
        sa.CheckConstraint(
            "response_mode IN ('query','form_post')",
            name="ck_identity_providers_response_mode",
        ),
        sa.CheckConstraint(
            "admin_group_mode IN ('ignore','owner_approval','owner_delegated')",
            name="ck_identity_providers_admin_group_mode",
        ),
    )

    op.create_table(
        "user_identities",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="CASCADE",
                name="fk_user_identities_user_id_users",
            ),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "identity_providers.id",
                ondelete="CASCADE",
                name="fk_user_identities_provider_id_identity_providers",
            ),
            nullable=False,
        ),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email_at_link", sa.Text(), nullable=True),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider_id",
            "subject",
            name="uq_user_identities_provider_subject",
        ),
    )
    op.create_index(
        "ix_user_identities_user",
        "user_identities",
        ["user_id"],
    )

    op.create_table(
        "oidc_login_states",
        sa.Column("state_hash", sa.LargeBinary(), primary_key=True),
        sa.Column(
            "provider_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "identity_providers.id",
                ondelete="CASCADE",
                name="fk_oidc_login_states_provider_id_identity_providers",
            ),
            nullable=False,
        ),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("return_to", sa.Text(), nullable=True),
        sa.Column(
            "initiated_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_oidc_login_states_initiated_user_id_users",
            ),
            nullable=True,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_oidc_login_states_expires",
        "oidc_login_states",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_oidc_login_states_expires", table_name="oidc_login_states")
    op.drop_table("oidc_login_states")

    op.drop_index("ix_user_identities_user", table_name="user_identities")
    op.drop_table("user_identities")

    op.drop_table("identity_providers")
