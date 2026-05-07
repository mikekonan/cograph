"""Widen ``md_jobs.kind`` CHECK to include ``upload``.

Revision ID: 0046_widen_md_job_kind
Revises: 0045_drop_banks

The original ``md_jobs`` table (migration ``6050517e0201``) constrained
``kind`` to ``embed | resolve_links``. Bulk uploads at the 2k-file scale
need a third value, ``upload``, used purely for FE progress tracking
(no arq function backs it; the embed + resolve_links jobs do the work).

The original CHECK was created by an inline ``Enum(name='mdjobkind',
native_enum=False)`` column inside ``op.create_table``. Alembic's
``op.create_table`` does NOT thread the metadata-level naming_convention
into inline column constraints, so the constraint name in PG is the
raw ``mdjobkind`` rather than the convention-formatted
``ck_md_jobs_mdjobkind``. We drop both names defensively with
``IF EXISTS`` and re-add the widened CHECK; ``op.create_check_constraint``
applies naming_convention so the new name is canonical.
"""

from __future__ import annotations

from alembic import op


revision = "0046_widen_md_job_kind"
down_revision = "0045_drop_banks"
branch_labels = None
depends_on = None


_OLD_KINDS = ("embed", "resolve_links")
_NEW_KINDS = ("embed", "resolve_links", "upload")
_CONSTRAINT = "mdjobkind"


def _value_list(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _drop_kind_constraint() -> None:
    """Drop the kind-CHECK constraint regardless of its actual name."""
    bind = op.get_bind()
    bind.exec_driver_sql(
        "ALTER TABLE md_jobs DROP CONSTRAINT IF EXISTS mdjobkind"
    )
    bind.exec_driver_sql(
        "ALTER TABLE md_jobs DROP CONSTRAINT IF EXISTS ck_md_jobs_mdjobkind"
    )


def upgrade() -> None:
    _drop_kind_constraint()
    op.create_check_constraint(
        _CONSTRAINT,
        "md_jobs",
        f"kind IN {_value_list(_NEW_KINDS)}",
    )


def downgrade() -> None:
    _drop_kind_constraint()
    op.create_check_constraint(
        _CONSTRAINT,
        "md_jobs",
        f"kind IN {_value_list(_OLD_KINDS)}",
    )
