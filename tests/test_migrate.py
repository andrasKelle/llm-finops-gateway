from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import String, create_engine, inspect, text
from sqlalchemy.engine import Engine

from llm_finops_gateway import migrate
from llm_finops_gateway.ledger import Base, CostEntry, Ledger


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'ledger.db'}"


def _legacy(url: str) -> Engine:
    """A database as 0.3 left it: the baseline tables with data, no revision recorded."""
    engine = create_engine(url)
    migrate.upgrade(engine, "0001")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(
            text(
                "INSERT INTO budget_counters (team, month, spent_usd, reserved_usd) "
                "VALUES ('platform', '2026-11', 1.5, 0)"
            )
        )
    return engine


def _team_width(engine: Engine) -> int:
    cols = {c["name"]: c for c in inspect(engine).get_columns("budget_counters")}
    kind = cols["team"]["type"]
    assert isinstance(kind, String) and kind.length is not None
    return kind.length


def test_head_is_the_latest_revision() -> None:
    assert migrate.head() == "0002"


def test_empty_database_is_created_at_head(tmp_path: Path) -> None:
    ledger = Ledger(_url(tmp_path))
    assert ledger.schema_revision == migrate.head()
    assert migrate.current(create_engine(_url(tmp_path))) == migrate.head()


def test_migrations_produce_the_schema_of_the_models(tmp_path: Path) -> None:
    engine = create_engine(_url(tmp_path))
    migrate.upgrade(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_unversioned_database_is_stamped_at_the_baseline_and_upgraded(tmp_path: Path) -> None:
    engine = _legacy(_url(tmp_path))
    assert migrate.current(engine) is None and _team_width(engine) == 64
    assert migrate.upgrade(engine) == (None, "0002")
    assert migrate.current(engine) == "0002" and _team_width(engine) == 255
    with engine.connect() as conn:
        spent = conn.execute(text("SELECT spent_usd FROM budget_counters")).scalar_one()
    assert Decimal(str(spent)) == Decimal("1.5")
    assert migrate.upgrade(engine) == ("0002", "0002")  # running it again changes nothing


def test_ledger_does_not_migrate_an_existing_database(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = _legacy(_url(tmp_path))
    with caplog.at_level(logging.WARNING):
        ledger = Ledger(_url(tmp_path))
    assert ledger.schema_revision is None and _team_width(engine) == 64
    assert "ledger migrate" in caplog.text
    ledger.record(
        CostEntry(
            request_id="r1",
            team="platform",
            project="p",
            use_case="u",
            provider="fake",
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal("0.5"),
            price_book_version="v",
        )
    )
    assert ledger.counter("platform").spent_usd == Decimal("2")


def test_cli_ledger_migrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from llm_finops_gateway.cli import main

    engine = _legacy(_url(tmp_path))
    monkeypatch.setenv("LFG_DATABASE_URL", _url(tmp_path))
    main(["ledger", "migrate"])
    assert "unversioned -> 0002" in capsys.readouterr().out
    assert _team_width(engine) == 255
    main(["ledger", "migrate"])
    assert "already at 0002" in capsys.readouterr().out

    monkeypatch.setenv("LFG_DATABASE_URL", f"sqlite:///{tmp_path / 'new.db'}")
    main(["ledger", "migrate"])
    assert "empty -> 0002" in capsys.readouterr().out
