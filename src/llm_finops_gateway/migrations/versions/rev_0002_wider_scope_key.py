"""budget_counters.team holds a scope key (team, team/project, model scope): 64 -> 255.

Revision ID: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    with op.batch_alter_table("budget_counters") as batch:  # SQLite cannot alter a column type
        batch.alter_column(
            "team", existing_type=sa.String(64), type_=sa.String(255), existing_nullable=False
        )
