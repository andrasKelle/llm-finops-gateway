# The ledger database

One database, two tables and a revision marker, three roles. Everything the gateway persists is here; everything it
decides with (prices, policies, key hashes) is a file in git and is not here (ADR-0002,
ADR-0005, ADR-0006).

## Engines

| Where | `LFG_DATABASE_URL` | Notes |
| --- | --- | --- |
| Laptop, demo | `sqlite:///./gateway.db` (default) | one file, one process |
| Azure reference deployment | `postgresql+psycopg://lfg_app:...@<server>:5432/lfg?sslmode=require` | Flexible Server, several replicas, readable by BI tools |
| Anything else SQLAlchemy speaks | any URL | PostgreSQL is the only engine the test suite runs against besides SQLite (`tests/test_ledger_postgres.py`) |

The driver for PostgreSQL comes with the `[postgres]` extra and is in the Docker image.

## Local PostgreSQL

An empty database is enough; the gateway creates the schema when it first opens it.

```bash
docker run -d --name lfg-pg -e POSTGRES_PASSWORD=change-me -e POSTGRES_DB=lfg \
    -p 5432:5432 postgres:16-alpine
pip install -e ".[postgres]"
export LFG_DATABASE_URL=postgresql+psycopg://postgres:change-me@localhost:5432/lfg
llm-finops-gateway ledger migrate        # prints: ledger schema: empty -> 0002
llm-finops-gateway serve                 # or skip the line above: an empty database is set up at start
```

To look at it: `docker exec -it lfg-pg psql -U postgres lfg`, then `\dt` lists `cost_records`,
`budget_counters` and `alembic_version`, and `select * from alembic_version;` shows the
revision. `/healthz` shows the same as `schema`, next to `schema_head`, the revision the
running version expects.

## Schema and migrations

The schema is versioned with Alembic revisions inside the package
(`src/llm_finops_gateway/migrations/versions`, ADR-0026):

| Revision | Change |
| --- | --- |
| `0001` | baseline: the two tables as 0.3 created them |
| `0002` | `budget_counters.team` widened from 64 to 255 characters (ADR-0025) |

- **Empty database**: brought to the latest revision when the gateway (or any `ledger`
  command) first opens it. The connecting role needs CREATE for that one start.
- **Existing database**: the gateway never alters it. After upgrading the software run
  `llm-finops-gateway ledger migrate` with `LFG_DATABASE_URL` set to a role that may alter
  tables (the admin, not the serving role), then start the new version. Until then the
  gateway logs a warning at start-up and `/healthz` shows `schema` behind `schema_head`.
- **A database from 0.3 or earlier** has no recorded revision (`schema: unversioned`). The
  same command records it as the baseline and applies what follows; the data stays.
- **In a container**: `docker run --rm -e LFG_DATABASE_URL=<admin url> <image> llm-finops-gateway ledger migrate`.
- **Adding a revision** (contributors): a new `rev_NNNN_<what>.py` next to the others with
  `revision`, `down_revision` and an `upgrade()`, plus the same change in the models in
  `ledger.py`; `tests/test_migrate.py` fails when the two differ. Forward only.

### `cost_records`: one row per completed call

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | integer, PK | |
| `ts` | timestamptz | when the call completed, UTC (index) |
| `request_id` | varchar(40), unique | the `id` returned to the client, `gw-...` |
| `team`, `project` | varchar(64) | attribution (indexes) |
| `use_case` | varchar(64) | optional dimension, `unspecified` when absent |
| `key_id` | varchar(64), nullable | which team key made the call |
| `provider` | varchar(32) | adapter that served it (`azure_openai`, `openai_compat`, `ollama`) |
| `model` | varchar(64) | the price book model name (index) |
| `input_tokens`, `output_tokens` | integer | as reported by the provider |
| `cached_input_tokens` | integer | part of `input_tokens` served from the prompt cache |
| `cost_usd` | numeric(14,8) | exact decimal in the price book's currency (name kept until 1.0) |
| `price_book_version` | varchar(32) | the version the row was priced with |

No prompt or completion text, no headers, no client IP.

### `budget_counters`: month-to-date per budget scope

| Column | Type | Meaning |
| --- | --- | --- |
| `team`, `month` | varchar(255), PK | the scope key: team, `team/project` (ADR-0021) or a model scope `team/model/<model>`, `team/<project>/model/<model>` (ADR-0025); `month` is `YYYY-MM` in UTC |
| `spent_usd` | numeric(14,8) | sum of recorded cost this month |
| `reserved_usd` | numeric(14,8) | estimates reserved by calls in flight (ADR-0007) |

Databases created before 0.4 have `team` as `varchar(64)` until `ledger migrate` has run;
PostgreSQL enforces the width, so a model budget with a long scope key needs revision `0002`.

Enforcement reads and updates this row with one conditional `UPDATE`; reports read
`cost_records`. The two are updated in the same transaction when a call is recorded.

## Roles on Azure (set up once by `deploy/azure/db_roles.py`)

| Role | Grants | Used by |
| --- | --- | --- |
| `lfgadmin` | server admin, owns the tables | the Bicep deployment, `db_roles.py`, `ledger migrate` |
| `lfg_app` | CONNECT, USAGE on `public`, SELECT/INSERT/UPDATE on the two tables, SELECT on `alembic_version`, USAGE on their sequences; the same on tables created later | the gateway (`database-url` in Key Vault) |
| `lfg_reporting` | CONNECT, USAGE, SELECT on every table, also future ones | BI tools, spreadsheets, `ledger reconcile` from a laptop, anyone who reads (`database-url-reporting` in Key Vault) |

The gateway cannot DELETE or TRUNCATE; the reporting role cannot write. The script also
rotates the two passwords when re-run and restarts the Container App so it picks up the new
`database-url`. Key Vault holds: `postgres-password` (admin), `postgres-app-password`,
`postgres-reporting-password`, `database-url`, `database-url-reporting`.

Connecting from a BI tool or psql: host and database from `database-url-reporting`, user
`lfg_reporting`, SSL required; the server's firewall must list the client's IP (Bicep
`clientIp`) or the client must come through the Azure-services rule.

## Operations

- **Upgrades**: read the release notes for a schema revision; when there is one, run
  `ledger migrate` with the admin URL before starting the new version. On Azure:
  `LFG_DATABASE_URL="postgresql+psycopg://lfgadmin:<postgres-password>@<server>:5432/lfg?sslmode=require" llm-finops-gateway ledger migrate`
  from a client the server's firewall allows.
- **Backups**: Flexible Server keeps 7 days of automatic backups (Bicep); SQLite is a file.
- **Retention**: nothing is deleted by the gateway. Archive or prune `cost_records` by `ts` with
  the admin role when the retention policy says so; `budget_counters` rows are one per team
  and month and stay small.
- **Reconciliation**: `llm-finops-gateway ledger reconcile` reads `cost_records` for one UTC
  day and compares it with Azure Cost Management (ADR-0018); run it with the reporting URL.
- **Reports**: `/finops`, `/v1/finops/summary(.csv)` and `/v1/finops/budgets` are served by the
  gateway from the same tables with the app role; a BI tool reads the tables directly with the
  reporting role.
