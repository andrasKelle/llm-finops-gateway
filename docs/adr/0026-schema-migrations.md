# 0026. The ledger schema is versioned with Alembic migrations

Date: 2026-11-16
Status: Accepted

## Context

The ledger's two tables were created by SQLAlchemy `create_all` on the first start, and
nothing altered them afterwards. Every schema change since (the `cached_input_tokens` column,
the wider scope key of ADR-0025) became a sentence in the documentation that asked the operator
to run an `ALTER TABLE` by hand. A database did not record which schema it had, so neither the
gateway nor the operator could tell whether an upgrade was complete. Someone who started an
empty PostgreSQL had no documented path either, although the gateway would have created the
tables on its own.

Two constraints shape the answer. The role the gateway serves with must not need DDL
(`lfg_app` in `docs/database.md` has no CREATE or ALTER). And several replicas start against
the same database, so a schema change at start-up would race.

## Decision

- The schema is defined by Alembic revisions shipped inside the package
  (`llm_finops_gateway/migrations/versions`), with no `alembic.ini`: `migrate.py` builds the
  configuration in code and runs the revisions on a connection of the ledger's engine.
  Revision `0001` is the baseline, the schema as 0.3 created it; `0002` widens
  `budget_counters.team` to 255 characters (ADR-0025).
- An empty database is brought to the head revision when the ledger opens it, with the same
  privilege `create_all` needed before. A laptop, the demo and a fresh PostgreSQL need no extra
  step.
- An existing database is never altered by the gateway. `llm-finops-gateway ledger migrate`
  does it, run with `LFG_DATABASE_URL` naming a role that may alter tables. A database that
  has the ledger tables but no recorded revision predates migrations: the command records it
  as the baseline and upgrades from there.
- When the recorded revision differs from the one the code expects, the gateway logs a warning
  at start-up and keeps serving; `/healthz` reports both as `schema` and `schema_head`.
- Revisions are written by hand, forward only, one per schema change, in the same pull request
  as the change to the models in `ledger.py`. A test compares the migrated schema with the
  models and fails when they differ.
- Alembic becomes a core dependency: SQLite deployments need migrations as much as PostgreSQL
  ones.

## Consequences

- Upgrading is one documented command, and the database says which schema it has.
- The serving role needs `SELECT` on `alembic_version` and still no DDL; `db_roles.py` grants
  it.
- A release whose code cannot work on the previous schema (a new column the ledger writes)
  will fail on its first insert if the operator skipped `ledger migrate`. The warning and
  `/healthz` make that visible, but they do not stop it. The first release that adds such a
  change will make the gateway refuse to start on an older schema; revision `0002` does not
  need it.
- No downgrade path: going back means restoring a backup.
- Two replicas that start against the same empty database at once can still race on the first
  creation, as they could with `create_all`; initialise a shared database with
  `ledger migrate` (or `db_roles.py`) before the first start.
