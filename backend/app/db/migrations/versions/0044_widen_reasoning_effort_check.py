"""Widen ``reasoning_effort`` CHECK to OpenAI's full enum.

Revision ID: 0044_widen_reasoning_effort
Revises: 0043_llm_secrets

The original ``0043_llm_secrets`` migration was authored when only
``low|medium|high`` were modelled. OpenAI's GPT-5/GPT-5.4/GPT-5.5
reasoning models accept a wider set: ``minimal | none | low | medium |
high | xhigh``. The Python tuple in the model and the schema in
``0043`` were updated in-place to the new set, but operators who had
already applied ``0043`` keep the old CHECK rejecting any of the new
values. This migration drops and re-adds the constraint with the full
enum so live installs catch up.

This keeps existing databases compatible without asking operators to wipe
their volume.
"""

from __future__ import annotations

from alembic import op


revision = "0044_widen_reasoning_effort"
down_revision = "0043_llm_secrets"
branch_labels = None
depends_on = None


_NEW_EFFORTS = ("minimal", "none", "low", "medium", "high", "xhigh")
_OLD_EFFORTS = ("low", "medium", "high")
# The constraint is doubled-up because of SQLAlchemy's naming convention
# (``ck_<table>_<name>``); inside the migration we still pass the bare
# logical name and let the convention prefix it. ``op.drop_constraint``
# resolves to the same physical name PG ended up with.
_CONSTRAINT = "chk_llm_model_assignments_effort_value"


def _value_list(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        _CONSTRAINT,
        "llm_model_assignments",
        type_="check",
    )
    op.create_check_constraint(
        _CONSTRAINT,
        "llm_model_assignments",
        f"reasoning_effort IS NULL OR reasoning_effort IN {_value_list(_NEW_EFFORTS)}",
    )


def downgrade() -> None:
    op.drop_constraint(
        _CONSTRAINT,
        "llm_model_assignments",
        type_="check",
    )
    op.create_check_constraint(
        _CONSTRAINT,
        "llm_model_assignments",
        f"reasoning_effort IS NULL OR reasoning_effort IN {_value_list(_OLD_EFFORTS)}",
    )
