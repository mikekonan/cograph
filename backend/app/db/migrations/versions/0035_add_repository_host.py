"""Add repositories.host column and swap unique constraint to (host, owner, name).

Revision ID: 0035_add_repository_host
Revises: 0034_add_mcp_tokens

Repositories are now externally addressed by a compound slug
`host/owner/name` (e.g. `github.com/mikekonan/cograph`) on every
user-facing surface — REST API, MCP tools/resources, FE routes. The
internal UUID PK stays unchanged; only the external identity changes.

Migration:
1. Add nullable `host` column.
2. Backfill from `git_url`:
   - HTTPS / http URLs: extract netloc via regexp_match.
   - SCP-like SSH (`git@host:owner/repo`): extract host between `@` and `:`.
   - `zip://` placeholders or anything else: fall back to `local.zip`.
3. Pre-flight duplicate check — fail loudly if any (host, owner, name)
   collisions exist before we add the unique constraint.
4. ALTER NOT NULL + CHECK `host <> ''`.
5. Drop the legacy `uq_repositories_git_url_branch` constraint and add
   the new `uq_repositories_host_owner_name`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0035_add_repository_host"
down_revision = "0034_add_mcp_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("host", sa.String(length=255), nullable=True),
    )

    # Backfill from git_url. Postgres-only because alembic upgrades run
    # against Postgres in dev/prod; the SQLite test suite drops + recreates
    # via Base.metadata.create_all and never executes this migration.
    op.execute(
        sa.text(
            """
            UPDATE repositories
            SET host = CASE
                WHEN git_url ~ '^https?://'
                    THEN substring(git_url FROM '^https?://([^/]+)/')
                WHEN git_url ~ '^git@'
                    THEN substring(git_url FROM '^git@([^:]+):')
                WHEN git_url ~ '^ssh://'
                    THEN substring(git_url FROM '^ssh://(?:[^@]+@)?([^/:]+)')
                ELSE 'local.zip'
            END
            """
        )
    )

    # Defensive fallback: anything still NULL (e.g. unexpected scheme) gets
    # `local.zip` so the NOT NULL conversion succeeds and operators see
    # which rows need manual fix-up.
    op.execute(
        sa.text("UPDATE repositories SET host = 'local.zip' WHERE host IS NULL OR host = ''")
    )

    # Pre-flight duplicate check — fail loudly rather than silently violate
    # the new unique constraint.
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
                dup_count integer;
            BEGIN
                SELECT COUNT(*) INTO dup_count FROM (
                    SELECT host, owner, name
                    FROM repositories
                    GROUP BY host, owner, name
                    HAVING COUNT(*) > 1
                ) AS dups;
                IF dup_count > 0 THEN
                    RAISE EXCEPTION
                        'Cannot upgrade: % duplicate (host, owner, name) tuples exist in repositories. Resolve before retrying.',
                        dup_count;
                END IF;
            END $$;
            """
        )
    )

    op.alter_column("repositories", "host", nullable=False)
    op.create_check_constraint(
        "ck_repositories_host_nonempty",
        "repositories",
        "host <> ''",
    )

    op.drop_constraint(
        "uq_repositories_git_url_branch",
        "repositories",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_repositories_host_owner_name",
        "repositories",
        ["host", "owner", "name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_repositories_host_owner_name",
        "repositories",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_repositories_git_url_branch",
        "repositories",
        ["git_url", "branch"],
    )
    op.drop_constraint("ck_repositories_host_nonempty", "repositories", type_="check")
    op.drop_column("repositories", "host")
