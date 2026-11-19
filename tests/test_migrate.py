from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import String, create_engine, inspect, text
from sqlalchemy.engine import Engine

from llm_finops_gateway import migrate
from llm_finops_gateway.ledger import Base, Ledger, SchemaOutOfDateError


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
        conn.execute(
            text(
                "INSERT INTO cost_records (ts, request_id, team, project, use_case, provider, "
                "model, input_tokens, output_tokens, cost_usd, price_book_version) VALUES "
                "('2026-11-10 10:00:00', 'old-1', 'platform', 'p', 'u', 'fake', 'm', 1, 1, 1.5, "
                "'v')"
            )
        )
    return engine


def _team_width(engine: Engine) -> int:
    cols = {c["name"]: c for c in inspect(engine).get_columns("budget_counters")}
    kind = cols["team"]["type"]
    assert isinstance(kind, String) and kind.length is not None
    return kind.length


def test_head_is_the_latest_revision() -> None:
    assert migrate.head() == "0005"


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
    assert migrate.upgrade(engine) == (None, "0005")
    assert migrate.current(engine) == "0005" and _team_width(engine) == 255
    with engine.connect() as conn:
        spent = conn.execute(text("SELECT spent_usd FROM budget_counters")).scalar_one()
        written = conn.execute(
            text(
                "SELECT cache_write_input_tokens, cache_write_1h_input_tokens, run_id, model_alias "
                "FROM cost_records"
            )
        ).one()
    assert Decimal(str(spent)) == Decimal("1.5")
    assert tuple(written) == (0, 0, None, None)  # rows from before the columns existed
    assert migrate.upgrade(engine) == ("0005", "0005")  # running it again changes nothing


def test_ledger_refuses_an_older_schema_and_does_not_alter_it(tmp_path: Path) -> None:
    engine = _legacy(_url(tmp_path))
    with pytest.raises(SchemaOutOfDateError, match="ledger migrate") as exc:
        Ledger(_url(tmp_path))
    assert "unversioned" in str(exc.value) and migrate.head() in str(exc.value)
    assert migrate.current(engine) is None and _team_width(engine) == 64  # untouched

    migrate.upgrade(engine, "0002")  # a versioned database one revision behind is refused too
    with pytest.raises(SchemaOutOfDateError, match="0002"):
        Ledger(_url(tmp_path))

    migrate.upgrade(engine)
    assert Ledger(_url(tmp_path)).schema_revision == migrate.head()


def test_cli_ledger_migrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from llm_finops_gateway.cli import main

    engine = _legacy(_url(tmp_path))
    monkeypatch.setenv("LFG_DATABASE_URL", _url(tmp_path))
    main(["ledger", "migrate"])
    assert "unversioned -> 0005" in capsys.readouterr().out
    assert _team_width(engine) == 255
    main(["ledger", "migrate"])
    assert "already at 0005" in capsys.readouterr().out

    monkeypatch.setenv("LFG_DATABASE_URL", f"sqlite:///{tmp_path / 'new.db'}")
    main(["ledger", "migrate"])
    assert "empty -> 0005" in capsys.readouterr().out
