"""Schema migrations of the ledger database (ADR-0026).

The schema is defined by the Alembic revisions in ``migrations/versions``, shipped inside the
package. An empty database is brought to the head revision when the ledger opens it; an
existing one is changed only by ``llm-finops-gateway ledger migrate``, run with a role that
may alter tables, so the role the gateway serves with never needs DDL.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine

BASELINE = "0001"  # the schema of 0.3, which create_all produced before migrations existed


def _config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["connection"] = connection
    return config


def head() -> str:
    """The revision this version of the code expects."""
    revision = ScriptDirectory.from_config(_config()).get_current_head()
    assert revision is not None
    return revision


def current(engine: Engine) -> str | None:
    """The revision recorded in the database; ``None`` when none is (empty or pre-0.4)."""
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def is_empty(engine: Engine) -> bool:
    """True when the ledger tables do not exist yet."""
    return not inspect(engine).has_table("cost_records")


def upgrade(engine: Engine, target: str = "head") -> tuple[str | None, str | None]:
    """Bring the database to ``target``; returns the revision before and after.

    A database that has the ledger tables but no recorded revision was created before
    migrations existed: it is recorded as the baseline first, then upgraded."""
    before = current(engine)
    with engine.begin() as conn:
        config = _config(conn)
        if before is None and inspect(conn).has_table("cost_records"):
            command.stamp(config, BASELINE)
        command.upgrade(config, target)
    return before, current(engine)
