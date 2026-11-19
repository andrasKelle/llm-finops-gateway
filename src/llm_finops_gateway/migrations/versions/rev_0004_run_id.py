"""cost_records: the optional run id that ties the calls of one agent run together.

Revision ID: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column("cost_records", sa.Column("run_id", sa.String(128), nullable=True))
    op.create_index("ix_cost_records_run_id", "cost_records", ["run_id"])
