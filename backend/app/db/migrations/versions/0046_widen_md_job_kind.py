"""Widen ``md_jobs.kind`` CHECK to include ``upload``.

Revision ID: 0046_widen_md_job_kind
Revises: 0045_drop_banks

The original ``md_jobs`` table (migration ``6050517e0201``) constrained
``kind`` to ``embed | resolve_links``. Bulk uploads at the 2k-file scale
need a third value, ``upload``, used purely for FE progress tracking
(no arq function backs it; the embed + resolve_links jobs do the work).
This migration drops and re-adds the CHECK constraint with the wider
enum.

The constraint name is derived from SQLAlchemy's naming convention
(``ck_<table>_<constraint_name>``) combined with the Enum's ``name``
argument (``mdjobkind``), giving ``ck_md_jobs_mdjobkind``.
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


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "md_jobs", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "md_jobs",
        f"kind IN {_value_list(_NEW_KINDS)}",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "md_jobs", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "md_jobs",
        f"kind IN {_value_list(_OLD_KINDS)}",
    )
