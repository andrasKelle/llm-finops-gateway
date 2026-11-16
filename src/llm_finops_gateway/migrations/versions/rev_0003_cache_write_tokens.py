"""cost_records: prompt tokens written to the provider's cache, and their one-hour part.

Revision ID: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    for column in ("cache_write_input_tokens", "cache_write_1h_input_tokens"):
        op.add_column(
            "cost_records",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )
