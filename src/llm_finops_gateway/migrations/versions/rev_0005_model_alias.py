"""cost_records: the alias the client called, when the model was a price book alias.

Revision ID: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.add_column("cost_records", sa.Column("model_alias", sa.String(64), nullable=True))
