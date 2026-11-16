"""Baseline: the two ledger tables as 0.3 created them.

Revision ID: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "cost_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(40), nullable=False),
        sa.Column("team", sa.String(64), nullable=False),
        sa.Column("project", sa.String(64), nullable=False),
        sa.Column("use_case", sa.String(64), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(14, 8), nullable=False),
        sa.Column("price_book_version", sa.String(32), nullable=False),
        sa.UniqueConstraint("request_id"),
    )
    for column in ("ts", "team", "project", "model"):
        op.create_index(f"ix_cost_records_{column}", "cost_records", [column])
    op.create_table(
        "budget_counters",
        sa.Column("team", sa.String(64), primary_key=True),
        sa.Column("month", sa.String(7), primary_key=True),
        sa.Column("spent_usd", sa.Numeric(14, 8), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(14, 8), nullable=False),
    )
