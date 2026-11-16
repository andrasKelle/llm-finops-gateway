"""The ledger against a real PostgreSQL. Runs only when LFG_TEST_DATABASE_URL points at one:

    docker run -d --rm --name lfg-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=lfg \
        -p 55432:5432 postgres:16-alpine
    LFG_TEST_DATABASE_URL=postgresql+psycopg://postgres:test@localhost:55432/lfg \
        pytest tests/test_ledger_postgres.py
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from llm_finops_gateway import migrate
from llm_finops_gateway.ledger import Base, CostEntry, Ledger

URL = os.environ.get("LFG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="LFG_TEST_DATABASE_URL not set")


@pytest.fixture
def pg_ledger() -> Ledger:
    assert URL is not None
    ledger = Ledger(URL)
    with ledger._engine.begin() as conn:  # noqa: SLF001 - test-only cleanup
        conn.execute(text("DELETE FROM cost_records"))
        conn.execute(text("DELETE FROM budget_counters"))
    return ledger


def _entry(team: str = "platform", cost: str = "0.5", **kw: object) -> CostEntry:
    return CostEntry(
        request_id=f"gw-{uuid.uuid4().hex}",
        team=team,
        project="p",
        use_case="u",
        provider="azure_openai",
        model="gpt-4.1-mini",
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal(cost),
        price_book_version="t",
        **kw,  # type: ignore[arg-type]
    )


def test_record_reserve_release_and_summary_on_postgres(pg_ledger: Ledger) -> None:
    assert pg_ledger.reserve("platform", Decimal("1"), limit=Decimal("2")) is True
    assert pg_ledger.reserve("platform", Decimal("1.5"), limit=Decimal("2")) is False  # over
    assert pg_ledger.counter("platform").reserved_usd == Decimal("1")
    pg_ledger.record(_entry(cost="0.5", cached_input_tokens=40), release_reserved=Decimal("1"))
    counter = pg_ledger.counter("platform")
    assert (counter.spent_usd, counter.reserved_usd) == (Decimal("0.5"), Decimal("0"))
    pg_ledger.release("platform", Decimal("5"))  # never below zero
    assert pg_ledger.counter("platform").reserved_usd == Decimal("0")
    rows = pg_ledger.summary()
    assert len(rows) == 1 and rows[0].cost_usd == Decimal("0.5") and rows[0].calls == 1


def test_usage_by_model_and_month_to_date_on_postgres(pg_ledger: Ledger) -> None:
    now = datetime.now(UTC)
    pg_ledger.record(_entry(cost="0.25", ts=now - timedelta(days=1)))
    pg_ledger.record(_entry(cost="0.75", ts=now))
    usage = pg_ledger.usage_by_model(now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(usage) == 1 and usage[0].calls == 1 and usage[0].cost_usd == Decimal("0.75")
    assert pg_ledger.month_to_date("platform", now - timedelta(days=2)) == Decimal("1")


def test_duplicate_request_id_is_rejected_on_postgres(pg_ledger: Ledger) -> None:
    entry = _entry()
    pg_ledger.record(entry)
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError wrapped by SQLAlchemy
        pg_ledger.record(entry)


def test_migrations_on_postgres_from_an_unversioned_database() -> None:
    assert URL is not None
    engine = create_engine(URL)
    with engine.begin() as conn:
        for table in ("cost_records", "budget_counters", "alembic_version"):
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    migrate.upgrade(engine, "0001")
    with engine.begin() as conn:  # as 0.3 left it: baseline tables, no revision recorded
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("INSERT INTO budget_counters VALUES ('platform', '2026-11', 1.5, 0)"))
    assert migrate.upgrade(engine) == (None, migrate.head())
    with engine.connect() as conn:
        width = conn.execute(
            text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'budget_counters' AND column_name = 'team'"
            )
        ).scalar_one()
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert width == 255 and diff == []
    ledger = Ledger(URL)
    assert ledger.schema_revision == migrate.head()
    assert ledger.counter("platform", "2026-11").spent_usd == Decimal("1.5")
    long_scope = "team/project/model/" + "m" * 120
    assert ledger.reserve(long_scope, Decimal("1"), None) is True
