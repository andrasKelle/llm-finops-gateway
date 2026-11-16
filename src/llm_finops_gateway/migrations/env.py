"""Alembic environment: runs the revisions on the connection ``migrate.py`` hands over."""

from alembic import context

context.configure(connection=context.config.attributes["connection"])
with context.begin_transaction():
    context.run_migrations()
