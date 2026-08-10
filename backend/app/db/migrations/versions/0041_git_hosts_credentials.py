"""Git hosts + credentials + webhook delivery dedup.

Revision ID: 0041_git_hosts_credentials
Revises: 0040_scim_clients_events

Phase 30.5 — owner-managed catalog of git hosts (github.com + GHES) with
one operator PAT per host. Tokens are encrypted at rest with
`GitCredentialCipher` (Fernet, domain-separated from OIDC + LLM secrets)
and never crossed onto a command line — clones go through `GIT_ASKPASS`
so the secret stays in env only.

Three new tables + one column:

- `git_hosts` — slug / display_name / kind / base_url / api_url /
  git_host (unique). `git_host` is what URL routing keys on
  (`https://git.example.com/owner/repo` resolves to whatever row owns
  `git.example.com`).
- `git_credentials` — operator PAT per host. `owner_user_id` is the
  human who pasted it (CASCADE on user delete; the row is treated as
  ownerless and removed). `is_default` partial-unique-per-host so
  routing always picks one. Optional `webhook_secret_encrypted` for
  the per-host webhook HMAC.
- `repo_webhook_deliveries` — `(host_id, X-GitHub-Delivery)` unique;
  GitHub retries collapse on the dedup row, no second sync job.
- `repositories.host_id` — nullable FK so historical rows don't break
  the migration (those keep the legacy "no credential" code path).

GHES with custom CA is explicitly deferred to V2 (CF-fronted assumption).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0041_git_hosts_credentials"
down_revision = "0040_scim_clients_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "git_hosts",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'github'"),
        ),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_url", sa.Text(), nullable=False),
        sa.Column("git_host", sa.Text(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
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
        sa.CheckConstraint("kind IN ('github')", name="ck_git_hosts_kind"),
        sa.UniqueConstraint("git_host", name="uq_git_hosts_git_host"),
    )

    op.create_table(
        "git_credentials",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "host_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "git_hosts.id",
                ondelete="CASCADE",
                name="fk_git_credentials_host_id_git_hosts",
            ),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="CASCADE",
                name="fk_git_credentials_owner_user_id_users",
            ),
            nullable=False,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column(
            "scopes_observed",
            sa.Text().with_variant(
                sa.dialects.postgresql.ARRAY(sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_tested_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "last_test_status",
            sa.String(length=16),
            nullable=True,
        ),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN "
            "('ok','unauthorized','forbidden','network')",
            name="ck_git_credentials_last_test_status",
        ),
    )
    op.create_index(
        "ix_git_credentials_host",
        "git_credentials",
        ["host_id"],
    )
    # Partial unique index — at most one default credential per host.
    op.create_index(
        "uq_git_credentials_host_default",
        "git_credentials",
        ["host_id"],
        unique=True,
        postgresql_where=sa.text("is_default = TRUE"),
        sqlite_where=sa.text("is_default = 1"),
    )

    op.create_table(
        "repo_webhook_deliveries",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "host_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "git_hosts.id",
                ondelete="CASCADE",
                name="fk_repo_webhook_deliveries_host_id_git_hosts",
            ),
            nullable=False,
        ),
        sa.Column("delivery_id", sa.Text(), nullable=False),
        sa.Column("repo_full_name", sa.Text(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sync_job_id", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "host_id", "delivery_id", name="uq_webhook_delivery"
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_received",
        "repo_webhook_deliveries",
        ["received_at"],
    )

    op.add_column(
        "repositories",
        sa.Column(
            "host_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "git_hosts.id",
                ondelete="RESTRICT",
                name="fk_repositories_host_id_git_hosts",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_repositories_host",
        "repositories",
        ["host_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_repositories_host", table_name="repositories")
    op.drop_constraint(
        "fk_repositories_host_id_git_hosts",
        "repositories",
        type_="foreignkey",
    )
    op.drop_column("repositories", "host_id")

    op.drop_index(
        "ix_webhook_deliveries_received", table_name="repo_webhook_deliveries"
    )
    op.drop_table("repo_webhook_deliveries")

    op.drop_index(
        "uq_git_credentials_host_default", table_name="git_credentials"
    )
    op.drop_index("ix_git_credentials_host", table_name="git_credentials")
    op.drop_table("git_credentials")

    op.drop_table("git_hosts")
