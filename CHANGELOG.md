# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Until 1.0, minor versions may change the API; this
is called out here.

## [Unreleased]

## [0.5.1] - 2026-11-20

Pinned keys in the LiteLLM plugin. No schema revision: a database migrated for 0.5.0 needs
nothing.

### Added
- LiteLLM plugin: a key whose `finops` metadata block says `"pin": true` binds the project and
  use case it carries. A call whose `X-Project` or `X-Use-Case` says something else is refused
  with `403 project_not_allowed` or `403 use_case_not_allowed` before any provider call, so one
  key per phase makes the phase a boundary instead of a label from the client's settings.
  Without `pin` the key's values stay defaults and the headers win (ADR-0035, #97).

## [0.5.0] - 2026-11-19

The Copilot release: GitHub Copilot (VS Code and CLI) behind the LiteLLM plugin on the Responses
route, rules and a budget per use case so each phase of a development cycle gets its model, a
run id that ties the calls of one feature together, reports by use case and by run, and model
aliases. Schema revisions `0004` and `0005`: existing databases need
`llm-finops-gateway ledger migrate` once before this version starts.

LiteLLM plugin users should upgrade: see Security below.

### Added
- Model aliases: a price book row `{ alias_of: <row> }` is a name clients may call that is
  priced, allow-listed and budgeted as its target; the ledger row keeps both names
  (`model`, `model_alias`), and `/v1/models` lists aliases (ADR-0033).
- Reports by use case and by run: `/v1/finops/summary` and its CSV accept `group_by=use_case`,
  `group_by=run_id` and `run_id=`; the report page's chargeback table is grouped by use case,
  lists the runs with their cost and shows one run on request, and the budgets table has a
  use case column with a row per use case budget (ADR-0032).
- Rules per use case: `use_cases` may be a mapping whose entries narrow the model list
  (`allowed_models`) and set a budget, reserved and settled next to the team, project and model
  budgets under `team/project/use_case/<use_case>` and reported as
  `x_finops.policy.use_case_budget`, `X-FinOps-Use-Case-Budget-*` and `use_case_budgets` in
  `/v1/finops/budgets`. The list form of `use_cases` is unchanged (ADR-0031).
- `X-Run-Id`: an optional request header that ties the calls of one agent run, pipeline job or
  feature together. Stored in `cost_records.run_id`, echoed as `x_finops.run_id` and
  `X-FinOps-Run-Id`, exported in the FOCUS `Tags` column; gateway and LiteLLM plugin (ADR-0030).
- LiteLLM plugin: the OpenAI Responses route (`/v1/responses`) runs the same attribution, price
  book, policy and budget steps as chat completions and `/v1/messages`, and settles from the
  success event. GitHub Copilot CLI was measured behind the plugin on both wire APIs (ADR-0029).

### Changed
- README images: the report page and the report inside the LiteLLM dashboard retaken with use
  case rules, a use case budget and runs, on real GPT and Claude traffic; the LiteLLM diagram
  names `/v1/responses` and Copilot; `docs/reporting.md` shows the chargeback of one run.
- The report tables no longer break a team, project, use case or model name in the middle, and
  a budget's status note sits under its badge.
- `examples/copilot/`: a compose stack, policies with a use case per phase, and the client
  settings for VS Code and Copilot CLI; `examples/claude-code/` shows the use case and run id
  headers; the test plan has section 4c for Copilot.
- LiteLLM plugin, chat completions: a rejection's message is now `code: explanation`
  (`budget_exceeded: team 'dev' has used ...`), as on `/v1/messages` and `/v1/responses`, so an
  editor that prints only the message shows the reason. The bare code moved to
  `provider_specific_fields.code`; a client that compared `error.message` with the code must
  read it there. Status codes and the gateway's own rejections are unchanged (ADR-0034).

### Security
- LiteLLM plugin: a call to `/v1/responses` no longer passes without an owner, a price or a
  budget. Up to 0.4.2 a client of a proxy with the plugin could spend outside its team's budget
  by choosing that route, and the call left no ledger row. The gateway's own endpoints were not
  affected (ADR-0029).

## [0.4.2] - 2026-11-18

The report page shows every budget scope. No schema revision: a database migrated for 0.4.0
needs nothing.

### Fixed
- The report page shows project and model budgets alongside team budgets, and the policy table
  includes the output-token reservation cap (#78).
- The budgets table keeps its columns aligned when sorting: a row without a budget has one cell
  per column, and a model budget's row names its model once.

### Changed
- The install instructions use the PyPI package (`pip install "llm-finops-gateway[litellm]"`).
- README images: the report page and the report inside the LiteLLM dashboard retaken with the
  budgets table per team, project and model.

## [0.4.1] - 2026-11-16

The first release on PyPI, with an image for both architectures and a fix to the report page.

### Fixed
- The report page keeps the budget percentage and the column headers on one line; a long list
  of allowed models used to push the "%" onto a second line.

### Changed
- Releases are published to PyPI from the `release` workflow through a trusted publisher.
- The README's images use absolute URLs, so they also render on the PyPI project page.
- README images: the report page and the report inside the LiteLLM dashboard retaken on 0.4.0
  with Claude and GPT traffic; the LiteLLM diagram names Claude Code and `/v1/messages`. The two
  diagrams are drawn by `docs/images/diagrams.py`.
- The release image is built for `linux/amd64` and `linux/arm64`; the `v0.4.0` and `latest`
  images were rebuilt that way. Before, a pull on Apple Silicon needed `--platform linux/amd64`.

## [0.4.0] - 2026-11-16

The coding-agent release: Claude Code and other Anthropic Messages clients behind the LiteLLM
plugin with cache reads and cache writes priced at their own rates, budgets per model, a cap on
what a large `max_tokens` reserves, and a ledger schema versioned with migrations. Existing
databases need `llm-finops-gateway ledger migrate` once before this version starts.

### Added
- Budgets per model (ADR-0025): `model_budgets` on a team or a registered project, same keys as
  `budget`, reserved after the team's and the project's and settled with the ledger row. The
  state travels as `x_finops.policy.model_budget`, the `X-FinOps-Model-Budget-*` headers and
  `model_budgets` in `/v1/finops/budgets`; the LiteLLM plugin inherits it. The `team` column of
  `budget_counters` is widened to 255 characters for the new scope keys (revision `0002`).
- Schema migrations (ADR-0026): the ledger schema is versioned with Alembic revisions shipped in
  the package. An empty database is set up at start as before; an existing one is upgraded with
  the new `llm-finops-gateway ledger migrate`, run with a role that may alter tables. `/healthz`
  reports `schema` and `schema_head`. `docs/database.md` has a local PostgreSQL walk-through.
- Claude Code behind the LiteLLM plugin (ADR-0027): the plugin handles LiteLLM's Anthropic
  `/v1/messages` route with the same attribution, pricing and budget rules as chat completions.
  The project comes from `X-Project` (`ANTHROPIC_CUSTOM_HEADERS` in Claude Code), a rejection
  carries its explanation in the error message, and every call is settled with its cache reads
  and cache writes. `examples/claude-code/` has a compose stack and a walk-through.
- Cache-write pricing: `cache_write` and `cache_write_1h` on a price book row, and
  `cache_write_input_tokens` and `cache_write_1h_input_tokens` on the ledger row (revision
  `0003`). The price book has rows for Claude Fable 5.2, Opus 5.5, Sonnet 5.5 and Haiku 5.5 at
  Anthropic's list prices (version `2026-11-16`).

- Reservation cap (ADR-0028): `reserve_output_tokens_max` on a team, project or model budget
  bounds the output tokens a pre-call reservation assumes, for clients that send a very large
  `max_tokens` (Claude Code asks for 128,000). The smallest cap among the call's budgets
  applies; the request itself is not changed. Without the key nothing changes.

### Changed
- **Upgrade step**: after installing this version run `llm-finops-gateway ledger migrate` once
  against an existing database (SQLite or PostgreSQL). It records a 0.3 database as the baseline
  and applies revisions `0002` and `0003`; the data stays. `alembic` is a new core dependency.
- The gateway does not start, and the plugin serves no call, on a database whose schema revision
  is not the one the code expects; the error names the revisions and the command. An empty
  database is still set up automatically.

## [0.3.1] - 2026-11-12

### Fixed
- The version reported by `/healthz`, the report page and the `x_finops` block was still
  `0.2.0` in 0.3.0 (`__version__` is set by hand); a test now checks it against `pyproject.toml`.

## [0.3.0] - 2026-11-11

The LiteLLM release: the same rules inside an existing LiteLLM proxy as a plugin and the report
inside its dashboard, a project registry with per-project budgets, hot reload of the price book
and the policy file, the FOCUS export, the assistant example, and the LiteLLM direction deployed
on Azure next to the gateway.

### Added
- `deploy/azure/litellm.bicep`, `litellm-deploy.sh` and `push-config.sh`: the LiteLLM direction
  on Azure Container Apps next to the gateway (proxy with the plugin, report as a dashboard
  plugin, assistant; config files on an Azure Files share with hot reload).
- Docs and images: both request paths as diagrams (`architecture.svg`,
  `architecture-litellm.svg`), fresh screenshots of the report page, the report inside the
  LiteLLM dashboard, the assistant and the Grafana dashboard; a FOCUS export example.
- LiteLLM plugin: the gateway's webhook alerts (`budget_warn`, `budget_block`,
  `upstream_error`, ADR-0017) are posted from the plugin too, from `LFG_ALERT_WEBHOOK_URL`.
- LiteLLM plugin: a key's metadata may carry a default project and use case
  (`{"finops": {"project": ..., "use_case": ...}}` on `/key/generate`), used when the
  `X-Project` / `X-Use-Case` headers are absent; the headers win when present. A service key
  bound to one project then needs no header at all.
- `X-FinOps-Cached-Input-Tokens` on every settled call (gateway and plugin): the prompt tokens
  the provider served from its cache and billed at the `cached_input` price, so a client can
  see why the same prompt cost less the second time.
- The report page as a LiteLLM dashboard plugin (ADR-0024): with `LFG_LITELLM_PLUGIN_NAME`
  and `LFG_LITELLM_SALT_KEY` set, the gateway serves the shell, manifest and auth endpoints
  the dashboard expects, verifies its identity claim and admits admin roles with a session
  cookie; extra `[litellm-ui]` (cryptography), in the container image by default.
- FOCUS export (ADR-0023): `GET /v1/finops/export.focus.csv` and `ledger export` write the
  ledger as FOCUS 1.1 rows per team, project, use case, provider, model, price book version
  and UTC day, with `x_` columns for calls and token detail.
- `examples/assistant/`: a Streamlit page that answers questions about the gateway from the
  repository's documentation (README and three pages, about 15k tokens as the system prompt)
  through the gateway or the plugin and shows the FinOps receipt next to every answer: sign in
  with a key only, the page reads the key's team, models, budget and registered projects from
  the policy; team and project budget bars, rejections, the provider's prompt cache visible from
  the second question on. Extra `[assistant]`, Dockerfile and compose service on port 8501; the
  example policy's assistant teams get budgets that fit a handful of such questions.
- `/v1/finops/budgets` lists each team's project registry (`projects`: effective models, use
  cases and budget state per registered project; `null` for teams with free-text projects).
- The LiteLLM plugin publishes the gateway's `lfg_*` Prometheus metrics into the registry
  LiteLLM serves on `/metrics/`, so the shipped Grafana dashboard shows LiteLLM traffic without
  changes; `observability/prometheus.yml` scrapes the example proxy.
- Hot reload (ADR-0022): the gateway and the LiteLLM plugin pick up edits to `pricing.yaml`
  and `policies.yaml` on the next request, without a restart; a file that fails to parse keeps
  the previous version in service. `/healthz` shows the versions in service.
- Project registry and per-project budgets (ADR-0021): a team's `projects` map in
  `policies.yaml` makes an unregistered `X-Project` a `403 project_not_registered` before the
  call; a registered project may carry its own budget (reserved and settled next to the
  team's, counter scope `team/project`), its own `allowed_models` and a `use_cases` list.
  `x_finops.policy.project_budget` and `X-FinOps-Project-Budget-*` headers report its state.
- LiteLLM proxy plugin (`llm_finops_gateway.litellm_plugin`, extra `[litellm]`): the same
  attribution, price book, budget and ledger rules inside an existing LiteLLM proxy through
  three hooks; `X-FinOps-*` headers on non-streaming responses, streamed calls settled from the
  final usage; `examples/litellm/` with a compose stack, a Dockerfile for the official image and
  a demo walkthrough (ADR-0019). ADR-0020 states what stays in this repository and what an
  enterprise layer adds.

### Changed
- The HTTP shape of a policy decision (status code, error body, `X-FinOps-*` headers) moved
  from `main.py` into `enforcement.py`, shared by the gateway and the plugin; no behaviour change.

## [0.2.0] - 2026-11-02

The Azure release: everything needed to put the gateway in front of real traffic in Azure and
prove the ledger against the bill.

### Added
- Generic OpenAI-compatible provider (`LFG_PROVIDER=openai_compat`): base URL, optional bearer
  key, extra headers and a model name map. Covers the Azure OpenAI v1 endpoint of Azure AI
  Foundry resources, vLLM, LocalAI and OpenAI (ADR-0010).
- Several providers in one gateway: every configured provider is built at startup and a price
  book row's `provider` key says which one serves the model; `LFG_PROVIDER` is the default.
  `GET /v1/models` reports the serving provider, `/healthz` lists the configured ones (ADR-0011).
- Prompt-cache pricing: `cached_input` in the price book, `usage.prompt_tokens_details.cached_tokens`
  read from Azure OpenAI and OpenAI-compatible responses, billed at the cached price, recorded
  on the ledger row and echoed in the response `usage`.

- Reasoning models: a price book row with `reasoning: true` (gpt-5, gpt-5-mini, gpt-5-nano
  shipped) makes the gateway send `max_completion_tokens` instead of `max_tokens` and drop the
  temperature; adapters accept `max_completion_tokens`. `usage.completion_tokens_details.reasoning_tokens`
  is passed through when the provider reports it.

- Streaming: `stream: true` returns OpenAI-format server-sent events; the cost is settled
  from the provider's final usage chunk and delivered in the last event with the `x_finops`
  block. Streams that end without usage are billed at the reservation estimate
  (`usage_estimated: true`); mid-stream upstream failures release the reservation and emit one
  error event (ADR-0012). All adapters implement `stream()`.

- `llm-finops-gateway pricing sync --region <region> [--deployment ...] [--write]`: compares
  the `azure_openai` rows of the price book with the Azure Retail Prices API per deployment
  type and model version, exits non-zero on drift, rewrites the numbers in place with
  `--write` (ADR-0013). Price book rows accept `sku:` and `version:` hints.

- Entra ID authentication for Azure OpenAI: `LFG_AZURE_OPENAI_AUTH=entra` sends bearer tokens
  from `DefaultAzureCredential` (managed identity, service principal or `az login`) instead of
  an API key; tokens are cached and refreshed per request. New optional extra `[azure]`,
  included in the Docker image (ADR-0014).

- OpenAI request surface (ADR-0015): `tools`, `tool_choice`, `parallel_tool_calls`,
  `response_format`, `top_p`, `stop`, `seed`, `user`, penalties and `logprobs` are passed
  through; messages accept content parts, `tool_calls`, `tool_call_id` and the `developer`
  role; responses and streams return `tool_calls`. `max_completion_tokens` is accepted from
  clients. Unknown request fields are rejected with 422. The budget estimate counts content
  parts, tool-call payloads and tool definitions. Ollama translates tools and JSON output.

- Report page `GET /finops` (budgets, chargeback, totals; HTML, no JavaScript, refreshes every
  minute) and `GET /v1/finops/summary.csv` for Excel and Power BI. One access rule for every
  report view: a team key reads its own team, a key with the new `role: finops` reads every
  team, no key is 401 in `api_key` mode; the page has a login form with a session cookie
  (ADR-0016). `keys new --role finops`. Guidance in `docs/reporting.md`.

- Grafana dashboard JSON for the Prometheus metrics (`grafana/llm-finops-gateway.json`) and
  SDK examples in Python and TypeScript (`examples/`): completion with `x_finops`, streaming,
  tool-call round trip, rejected call.

- Currency: `pricing sync --currency EUR` fetches the retail list in that currency and
  `--write` sets the book's `currency:`; the price book validates the code; responses carry
  `x_finops.currency` and `X-FinOps-Currency`. Amounts in `cost_usd`, `monthly_usd` and the
  `*_usd` fields are in the book's currency; the names stay for API stability before 1.0.

- Webhook alerts (`LFG_ALERT_WEBHOOK_URL`, `LFG_ALERT_COOLDOWN_SECONDS`): `budget_warn`,
  `budget_block`, `upstream_error` and `upstream_rate_limited` events as JSON with a Slack-ready
  `text`, once per team/provider per cooldown, delivered in the background (ADR-0017).

- Report page: sortable columns, per-table text filter, team selector (all or a subset,
  totals follow), display currency switch (`LFG_REPORT_RATES`, display only), and the
  effective policy per team and the price book opened from their version links.

- `LFG_ALERT_FORMAT=teams` sends Adaptive Cards for Teams Workflows webhooks;
  `llm-finops-gateway alerts test` sends one sample event. Report page: the team selector is a
  dropdown with checkboxes; sort order survives a reload.

- Screenshots of the report page (ok, warn and block states from real traffic) and the Grafana
  dashboard in the README; the local Grafana allows anonymous viewing; stat panels show 0
  instead of "No data" when a counter is empty. Architecture document and diagram updated
  for providers per model, streaming, Entra ID, reports, alerts and the Azure deployment shape;
  demo chart regenerated.

- `llm-finops-gateway ledger reconcile --resource <id> --day YYYY-MM-DD`: one day of the
  ledger against Azure Cost Management per model, with the billed SKU and the two kinds of gap
  named (traffic that bypassed the gateway, price book SKU mismatch) (ADR-0018). Test plan 4.5
  reconciliation of 2026-10-30 documented.

- `deploy/azure/`: Bicep for Azure Container Apps with a user-assigned managed identity
  (Cognitive Services OpenAI User on the account, AcrPull on the registry), Azure Database
  for PostgreSQL Flexible Server for the ledger (Azure Files + SQLite as the fallback), Log
  Analytics, team key hashes as a secret file; README with costs and the production additions.
- `deploy/azure/`: Key Vault with the PostgreSQL password, the database URL (read by the app
  with its identity) and the raw team keys (`keys.sh`); the template can create the Azure
  OpenAI account and model deployments too (`createAzureOpenAi`); `postgresLocation` and
  `postgresSku` parameters, with the region decision recorded in the README.
- `deploy/azure/db_roles.py`: `lfg_app` (write) and `lfg_reporting` (read-only) PostgreSQL
  roles with passwords and URLs in Key Vault; the gateway stops running as the admin. Bicep
  `databaseUrl` parameter keeps the app-role URL across redeployments. `docs/database.md`
  documents engines, schema, roles and operations.
- `[postgres]` extra (psycopg); the ledger is tested against a real PostgreSQL
  (`tests/test_ledger_postgres.py`, runs when `LFG_TEST_DATABASE_URL` is set); the Docker
  image includes the driver.

- Docs: BI tools are a consumer of the CSV, JSON and the read-only database role; no
  product-specific BI instructions in the core.

### Changed
- An upstream 429 is returned as `429 upstream_rate_limited` with the provider's `Retry-After`
  instead of `502 upstream_error`; the reservation is still released and nothing is recorded.
- `GET /v1/finops/summary` and `/v1/finops/budgets` now require a key in `api_key` mode and
  are scoped to the key's team unless the key has `role: finops`.
- Price book version `2026-11-02`: cached-input prices for the GPT-4o and GPT-4.1 rows and a
  `provider` on every row.

## [0.1.0] - 2026-10-28

First public release.

### Added
- OpenAI-compatible `POST /v1/chat/completions` (non-streaming) in front of Azure OpenAI or
  Ollama (ADR-0001).
- Versioned price book (`config/pricing.yaml`); a model without a price is rejected with
  HTTP 422 before any upstream call (ADR-0002).
- Mandatory attribution: `X-Project` always, the team from a team-bound API key
  (`Authorization: Bearer lfg_...`, SHA-256 hashes in `config/keys.yaml`, `LFG_AUTH_MODE=api_key`,
  default) or from `X-Team` in `LFG_AUTH_MODE=headers` for development. Keys can be restricted
  to projects, expire or be disabled; `key_id` is recorded on every ledger row (ADR-0003, 0006).
  CLI: `llm-finops-gateway keys new | hash | check`.
- Policy as code (`config/policies.yaml`): per-team model allow-lists, monthly budgets with a
  warning threshold (default 80 %) and a blocking threshold (default 100 %), warn-only mode,
  and requests-per-minute limits with HTTP 429 and `Retry-After` (ADR-0005, 0008).
- Atomic budget reservation: the estimated cost of a call is reserved with one conditional
  update before the upstream call, settled with the actual cost in the same transaction as the
  ledger row, released on upstream failure (ADR-0007).
- Budget state on every response: `x_finops` block, `X-FinOps-Budget-*`, `X-FinOps-Warning`,
  `X-FinOps-Cost-USD`, `X-FinOps-Request-Id` headers; structured 401/403/422/429 bodies;
  `WARNING` log lines on warn and block.
- SQLite (any SQLAlchemy URL) ledger with one row per call including the price book version;
  `GET /v1/finops/summary` chargeback and `GET /v1/finops/budgets` status endpoints;
  `GET /v1/models`; `GET /healthz`.
- Prometheus `GET /metrics` and optional OpenTelemetry tracing (`[otel]` extra) with
  `finops.*` span attributes, OTLP to any backend including Langfuse.
- Application factory with injected collaborators (ADR-0004); typed Python, `mypy --strict`,
  111 tests.
- `demo/run_demo.py`: 300-call simulation with no keys, or live mode against a running
  gateway; Markdown report, JSON summary, chargeback chart.
- Docker image (non-root, healthcheck), docker-compose with Ollama, GitHub Actions CI on
  Python 3.11 and 3.12 with `pip-audit` and a Docker build, release workflow, pre-commit,
  Dependabot, CLA bot.
- Documentation: README, architecture, deployment guide, Azure setup, manual test plan with
  results, comparison with existing gateways, ADRs 0001 to 0009, CONTRIBUTING, GOVERNANCE,
  CODE_OF_CONDUCT, SECURITY, CLA, MAINTAINERS.

[Unreleased]: https://github.com/andrasKelle/llm-finops-gateway/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/andrasKelle/llm-finops-gateway/releases/tag/v0.5.1
[0.5.0]: https://github.com/andrasKelle/llm-finops-gateway/releases/tag/v0.5.0
[0.4.2]: https://github.com/andrasKelle/llm-finops-gateway/releases/tag/v0.4.2
[0.4.1]: https://github.com/andrasKelle/llm-finops-gateway/releases/tag/v0.4.1
[0.4.0]: https://github.com/andrasKelle/llm-finops-gateway/releases/tag/v0.4.0
[0.3.1]: https://github.com/andrasKelle/llm-finops-gateway/releases/tag/v0.3.1
[0.3.0]: https://github.com/andrasKelle/llm-finops-gateway/releases/tag/v0.3.0
[0.2.0]: https://github.com/andrasKelle/llm-finops-gateway/releases/tag/v0.2.0
[0.1.0]: https://github.com/andrasKelle/llm-finops-gateway/releases/tag/v0.1.0
