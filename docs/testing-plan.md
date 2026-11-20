# Manual test plan

Scope: the gateway on a laptop (Ollama, or the simulated demo if Ollama is unavailable) and on
a disposable Azure OpenAI resource. Automated tests (`pytest -q`, 185 cases, plus 3 against
PostgreSQL when `LFG_TEST_DATABASE_URL` is set) cover the same behaviour with a fake provider;
this plan is for seeing it end to end and for collecting the screenshots used in the README
and the launch posts.

Runs: 2026-10-28 on macOS (Apple M1, 8 GB), Python 3.12, Ollama 0.40 with `llama3.2:3b`,
sections 0 to 3c; 2026-10-30 sections 3d, 4 and 5 on Azure OpenAI in Sweden Central;
2026-10-31 the Cost Management reconciliation (4.5) and the Container Apps deployment (5b).
See the sign-off table and the observations at the end.

Each step lists the command, the expected result and what to capture. Tick as you go.

## 0. Preconditions (10 min)

```bash
cd llm-finops-gateway
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,demo,otel]"
ruff check . && ruff format --check . && mypy src tests && pytest -q
```

- [ ] All four green. Capture: terminal with `111 passed`.

## 1. Simulated demo, no keys (5 min)

```bash
python demo/run_demo.py
open demo/out/chargeback.png      # macOS; xdg-open on Linux
cat demo/out/report.md
```

- [ ] Table shows five teams; `customer-support` at `100.x% (block)`, `platform` in the high
      90s `(warn)` with 2 budget blocks (reservations that did not fit), `research` at
      `>200% (warn)` warn-only, `marketing` with 9 `rate limited` and 5 `unpriced model`,
      `shadow-it` with 14 `no owner` rejections. "Unallocated spend: 0.00 USD".
- [ ] `demo/out/chargeback.png` renders two panels, labels readable.
- [ ] Capture: the PNG and the Markdown table (both go in the launch posts).

## 2. Local gateway against Ollama (20 min)

Terminal A:

```bash
ollama pull llama3.2:3b && ollama serve
```

Terminal B (headers mode, no keys yet):

```bash
LFG_AUTH_MODE=headers llm-finops-gateway serve --port 8080
```

Terminal C, run in order:

| # | Command | Expected |
| - | ------- | -------- |
| 2.1 | `curl -s localhost:8080/healthz` | `status: ok`, `price_book` and `policies` versions shown |
| 2.2 | `curl -s localhost:8080/v1/models \| jq '.data[].id'` | the models listed in `config/pricing.yaml` |
| 2.3 | call **without** `X-Team` | HTTP 400, `missing_headers: ["X-Team"]`, hint text |
| 2.4 | call with `X-Team: a b` (space) | HTTP 400, regex message |
| 2.5 | call with `"model": "gpt-9"` | HTTP 422, `No price configured for model 'gpt-9'` |
| 2.6 | call as `X-Team: customer-support` with `"model": "llama3.2:3b"` | HTTP 403, `model_not_allowed`, message lists allowed models |
| 2.7 | call as `X-Team: platform` with `llama3.2:3b` | HTTP 200, `x_finops.cost_usd` = `0` (local model), headers `X-FinOps-Budget-Status: ok` |
| 2.8 | `curl -s localhost:8080/v1/finops/summary \| jq` | one row, `calls: 1` |
| 2.9 | `curl -s localhost:8080/v1/finops/budgets \| jq` | every team from `policies.yaml`, `platform` at `0.0%` |
| 2.10 | `curl -s localhost:8080/docs` in a browser | OpenAPI UI lists 5 routes |
| 2.11 | `curl -s localhost:8080/metrics \| grep lfg_` | `lfg_requests_total`, `lfg_cost_usd_total`, `lfg_budget_used_ratio` lines |

Call template:

```bash
curl -s -i localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Team: platform" -H "X-Project: smoke" -H "X-Use-Case: manual-test" \
  -d '{"model":"llama3.2:3b","messages":[{"role":"user","content":"One sentence on FinOps."}]}'
```

- [ ] 2.1 to 2.10 pass. Capture: 2.3, 2.6 and 2.7 responses (headers visible with `-i`).

## 3. Budget lifecycle with a tiny budget (15 min)

Stop the gateway. Point it at the demo policies, whose budgets are cents:

```bash
LFG_POLICIES_PATH=demo/policies.yaml LFG_DATABASE_URL=sqlite:///./lifecycle.db \
  llm-finops-gateway --port 8080
```

Local models cost 0, so seed spend with the ledger directly (one-off, in a second shell with the
venv active). `Ledger.record()` updates both the ledger and the budget counter:

```bash
python - <<'PY'
from decimal import Decimal
from llm_finops_gateway.ledger import CostEntry, Ledger
Ledger("sqlite:///./lifecycle.db").record(CostEntry(
    request_id="seed-1", team="platform", project="seed", use_case="seed",
    provider="manual", model="gpt-4o-mini", input_tokens=0, output_tokens=0,
    cost_usd=Decimal("0.13"), price_book_version="manual"))
PY
```

`platform` in the demo policy has a 0.15 USD budget, warn at 80 %: 0.13 is 86.7 %.

| # | Command | Expected |
| - | ------- | -------- |
| 3.1 | call as `platform` | HTTP 200, `X-FinOps-Budget-Status: warn`, `X-FinOps-Warning` sentence with "blocked at 100%", `x_finops.policy.warnings[0]` same sentence, gateway log line `WARNING ... policy warn team=platform` |
| 3.2 | seed another `0.02` (total 0.15 = 100 %), call again | HTTP 403, `error: budget_exceeded`, `budget.level: block`, `remaining_usd: 0`, message ends with "estimated at 0 USD" (free model, still blocked), log line `policy block ... reason=budget_exceeded` |
| 3.3 | call as `research` after seeding it `0.50` (500 % of 0.10) | HTTP 200, status `warn`, warning ends with "enforcement is warn-only" |
| 3.4 | `GET /v1/finops/budgets` | `platform` level `block`, `research` level `warn` with `enforcement: warn` |
| 3.5 | edit `demo/policies.yaml`, set `platform.budget.monthly_usd: 1.00`, restart, call | HTTP 200, status `ok` (policy change is a file change, no DB edit) |

Note for 3.3: `research` needs its own key in `api_key` mode (`keys new --team research ...`)
and `llama3.2:3b` added to its allow-list in `demo/policies.yaml`; with the platform key plus
`X-Team: research` you get 403 `team_mismatch` instead, which is also correct.

- [ ] 3.1 to 3.5 pass. Capture: 3.1 response headers + log line side by side, 3.2 JSON body.
- [ ] `rm lifecycle.db` afterwards.

## 3b. Team keys (15 min)

```bash
llm-finops-gateway keys new --team platform --id platform-laptop
# paste the printed entry under keys: in config/keys.yaml, keep the raw key in a shell variable
export KEY=lfg_...
llm-finops-gateway keys check
llm-finops-gateway serve --port 8080        # api_key mode is the default
```

| # | Command | Expected |
| - | ------- | -------- |
| 3b.1 | call with `X-Team` and no `Authorization` | HTTP 401 `missing_api_key`, `WWW-Authenticate: Bearer` |
| 3b.2 | `-H "Authorization: Bearer lfg_wrong"` | HTTP 401 `invalid_api_key` |
| 3b.3 | `-H "Authorization: Bearer $KEY" -H "X-Project: smoke"` | HTTP 200, `x_finops.team` = `platform`, `x_finops.key_id` = `platform-laptop` |
| 3b.4 | same plus `-H "X-Team: research"` | HTTP 403 `team_mismatch` |
| 3b.5 | set `disabled: true` on the entry, restart, call | HTTP 401 `api_key_disabled` |
| 3b.6 | `python demo/run_demo.py --mode live --url http://localhost:8080 --model llama3.2:3b --api-key $KEY --calls 20` | all calls attributed to `platform` regardless of the simulated teams |

- [ ] 3b.1 to 3b.6 pass. Capture: 3b.3 body showing `key_id`, 3b.4 body.

## 3c. Rate limit (5 min)

Set `rate_limit: { requests_per_minute: 3 }` on `platform` in the policy file you are running
with, restart.

```bash
for i in 1 2 3 4; do
  curl -s -o /dev/null -w "%{http_code} " $U -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $KEY" -H 'X-Project: smoke' -d "$B"
done; echo
```

| # | Command | Expected |
| - | ------- | -------- |
| 3c.1 | four quick calls in a loop | three served (or `403` if the budget from section 3 is still exhausted), the fourth `429` with `Retry-After` and `error: rate_limited`. The rate limit runs before the budget check, so `403 403 403 429` is the expected pattern right after 3.2 |
| 3c.2 | `GET /v1/finops/budgets` | `platform.requests_per_minute: 3` |

- [ ] Capture: the 429 with headers.

## 3d. Tracing (10 min, optional)

```bash
docker run --rm -p 4318:4318 -p 16686:16686 jaegertracing/jaeger:2.5.0
LFG_OTEL_ENABLED=true OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
  LFG_AUTH_MODE=headers llm-finops-gateway serve --port 8080
```

- [ ] After a few calls, Jaeger at `localhost:16686` shows `POST /v1/chat/completions` spans
      with `finops.team`, `finops.cost_usd` attributes; `/healthz` is not traced.
- [ ] Capture: one span's attribute panel. For Langfuse, point the endpoint at
      `https://cloud.langfuse.com/api/public/otel` with the Basic header (see README).

## 4. Azure OpenAI, real money (30 min, well under 1 USD)

Follow `docs/azure-setup.md` to create the resource and `.env`. Then:

```bash
llm-finops-gateway --port 8080
python demo/run_demo.py --mode live --url http://localhost:8080 --model gpt-4.1-mini --calls 30
```

| # | Check | Expected |
| - | ----- | -------- |
| 4.1 | Live report | ~30 calls, non-zero `spend USD`, teams priced with `gpt-4.1-mini` (`gpt-4o-mini` can no longer be deployed, see `docs/azure-setup.md`) |
| 4.2 | One `x_finops.cost_usd` by hand | `(prompt_tokens * 0.40 + completion_tokens * 1.60) / 1_000_000` matches to the last digit |
| 4.3 | `GET /v1/finops/summary` | sums equal the report |
| 4.4 | Wrong deployment mapping (`LFG_AZURE_OPENAI_DEPLOYMENTS={"gpt-4.1-mini":"nope"}`) | HTTP 502, `upstream_error`, provider `azure_openai`, no ledger row written |
| 4.6 | Same resource through the v1 endpoint: `LFG_PROVIDER=openai_compat`, base URL `.../openai/v1` | HTTP 200, `x_finops.provider: openai_compat`, cost matches 4.2 |
| 4.8 | `stream: true` on `gpt-4.1-mini` and `gpt-5-mini` | `text/event-stream`, `X-FinOps-Request-Id` header, `chat.completion.chunk` deltas, final event with `usage` from the provider (`usage_estimated` absent) and the `x_finops` block, `data: [DONE]`; one ledger row per stream |
| 4.9 | Keyless: `LFG_AZURE_OPENAI_AUTH=entra`, no `LFG_AZURE_OPENAI_API_KEY`, after `az role assignment create ... --role "Cognitive Services OpenAI User"` | HTTP 200 with `Authorization: Bearer` from the `az login` identity; before the role propagates Azure answers `PermissionDenied`, returned as 502 |
| 4.10 | Tool calling on `gpt-4.1-mini`: a `get_weather` tool, two rounds (tool call, then tool result), the same streamed, `response_format: json_object`, content parts, and a request with an unknown field | round 1 `finish_reason: tool_calls` with arguments; round 2 a sentence using the tool result; streamed `delta.tool_calls` fragments assemble to the same call; JSON mode returns JSON; content parts accepted; unknown field is 422 naming it |
| 4.7 | Reasoning model (`gpt-5-mini` deployment, price book row `reasoning: true`) called with `temperature` and `max_tokens` the way OpenAI SDK clients do | HTTP 200 (Azure would reject raw `max_tokens` and a non-default temperature); `usage.completion_tokens_details.reasoning_tokens` present; a small `max_tokens` yields empty content with `finish_reason: length` |
| 4.5 | Azure Cost Management, next day | per meter, the token quantities equal the ledger's token sums and the charges equal the ledger priced at the deployment's SKU; any gap is explained (calls made outside the gateway, or a price book SKU mismatch) |

- [ ] 4.1 to 4.5 pass. Capture: 4.1 table with real dollars, 4.5 screenshot of Cost Management
      next to the gateway summary. This pair is the strongest image for the launch post.
- [ ] `az group delete` when done.

## 4b. Claude Code behind the LiteLLM plugin, real money (20 min, about 1 USD)

`examples/claude-code/` with an Anthropic API key in `.env`; a LiteLLM team `dev` and a key in
it; calls with `X-Project: my-repo`.

| # | Action | Expected |
| --- | --- | --- |
| 4b.1 | `/v1/messages` on each Claude row of the price book, non-streaming and streaming | HTTP 200; one ledger row per call, provider `anthropic`; tokens and cost equal the usage Anthropic returned, priced by hand |
| 4b.2 | A prompt with a `cache_control` breakpoint sent twice, per model; once more with `ttl: "1h"` | first call: the prefix in `cache_write_input_tokens` at the `cache_write` price; second call: the same tokens in `cached_input_tokens` at the `cached_input` price; the 1-hour write in `cache_write_1h_input_tokens` at `cache_write_1h` |
| 4b.3 | A tool call and its tool result on `/v1/messages` | two ledger rows, cost matches |
| 4b.4 | Claude Code (`claude -p ... --output-format json`) through the proxy: a task that writes, edits and runs a file, on Opus, Sonnet and Haiku; one that starts a subagent | every call attributed to the team and project; the session's ledger total equals the `total_cost_usd` Claude Code reports |
| 4b.5 | The ledger per model against LiteLLM's own spend log (`LiteLLM_SpendLogs`) | same calls, tokens and cost |
| 4b.6 | No `X-Project`; a project outside the registry; a model without a price | `400 missing_attribution`, `403 project_not_registered`, `422`, each with its explanation in Claude Code's error line; no ledger row |
| 4b.7 | A project with a 0.001 budget added to `policies.yaml` while the proxy runs | the next call is refused with `403 budget_exceeded` naming the project and the estimate, no restart |
| 4b.8 | `/v1/chat/completions` with a Claude model | HTTP 200 with `X-FinOps-Cost-USD`, cost matches by hand |

## 4c. GitHub Copilot behind the LiteLLM plugin, no model cost (30 min)

`examples/copilot/` with a stand-in upstream in place of the Anthropic API (any
OpenAI-compatible server that answers chat completions and the Responses API with a fixed
usage); a LiteLLM team `dev`, one key without metadata and one key per phase with
`{"finops": {"project": ..., "use_case": ...}}`; aliases `sdd-plan` and `sdd-implement` in the
price book and in the LiteLLM config.

| # | Action | Expected |
| --- | --- | --- |
| 4c.1 | Copilot CLI with a custom provider, `wireApi: completions`, the phase key, `COPILOT_OFFLINE=true` | HTTP 200 on `/v1/chat/completions`, streamed; one ledger row with the project and use case of the key, cached tokens at their price |
| 4c.2 | The same with `wireApi: responses` | HTTP 200 on `/v1/responses`; one ledger row |
| 4c.3 | An alias as the model, from the CLI | ledger row with `model` = the target and `model_alias` = the alias; the target's model budget is settled |
| 4c.4 | A model outside the phase's list; the alias of such a model; a use case outside the registry; no project | `403 model_not_allowed` naming the use case, twice; `403 use_case_not_registered`; `400 missing_attribution`; no ledger row |
| 4c.5 | A use case budget lowered in `policies.yaml` while the proxy runs | the next call of that phase is `403 budget_exceeded` naming the use case; the other phase keeps working |
| 4c.6 | `X-Run-Id` on chat completions and on the Responses route | `X-FinOps-Run-Id` on the response, `run_id` on the ledger row |
| 4c.7 | `/v1/finops/summary?group_by=use_case&group_by=run_id`, its CSV, `/v1/finops/budgets`, `/finops` and `/finops?run_id=...` | rows per use case and run, the use case budget with its state, the run list and the run view; the totals equal the team's counter |
| 4c.8 | VS Code, Custom Endpoint provider, model entries with `requestHeaders` (`X-Project`, `X-Use-Case`, `X-Run-Id`), `chat-completions` and `responses` | HTTP 200 on both routes; the three headers on every call; ledger rows with project, use case, alias and run id |
| 4c.9 | What each client shows for a `403` and a `400` | noted in the sign-off |
| 4c.10 | Real money (about 0.4 USD): `examples/copilot/` as shipped with an Anthropic API key; Copilot CLI on Opus (plain), on Sonnet (a task that reads a file) and on Haiku with `wireApi: responses` (the same task); VS Code on Haiku (the task) and on Sonnet | answers from the model, the file read through a tool call; one ledger row per call with the phase of the key; tokens and cost equal LiteLLM's spend log |
| 4c.11 | Real money (about 2.4 USD): VS Code against a deployed proxy, the three phases as model names (aliases with a use case, ADR-0036), no `X-Use-Case`; then the same with `cache_control_injection_points` on the three LiteLLM entries | one ledger row per call under the phase of its name, `model` = the target, `model_alias` = the name; tokens and cost equal LiteLLM's spend log; with the injection points a cache write on a model's first call and cache reads after it |

## 5. Docker (10 min)

```bash
docker compose up --build
curl -s localhost:8080/healthz
docker compose exec ollama ollama pull llama3.2:3b
# repeat 2.7 against the container
docker compose down
```

- [ ] Image builds, runs as non-root, healthcheck green (`docker ps` shows `healthy`).

## 5b. Azure Container Apps (20 min, costs about 0.5 USD/day while it runs)

`deploy/azure/README.md`, end to end: registry, cloud build, keys, Bicep.

| # | Check | Expected |
| - | ----- | -------- |
| 5b.1 | `az deployment group create ... main.bicep` | `Succeeded`, output `url` |
| 5b.2 | `GET /healthz` on the public URL | `auth_mode: api_key`, providers listed |
| 5b.3 | chat with the team key | HTTP 200 through the managed identity (no provider key in the app) |
| 5b.4 | `/finops` without a key, then with the finops key | login form, then the all-teams page |
| 5b.5 | restart the revision | the ledger on the Azure Files share survives |

- [x] 5b.7 `deploy/azure/db_roles.py`: `lfg_app` and `lfg_reporting` created, grants verified from the database; the app's connections show as `lfg_app`, a call through the public URL still lands; `lfg_reporting` can SELECT and gets `permission denied` on INSERT (2026-10-31).
- [x] 5b.6 PostgreSQL Flexible Server (North Europe, B1ms) as the ledger: a call through the public URL lands in `cost_records`, read back directly from the laptop; the app reads `database-url` from Key Vault with its identity; raw team keys and the admin password are in the vault (2026-10-31).
- [x] 5b.1 to 5b.4 pass 2026-10-31. First attempt failed at startup with `sqlite3 ... database
      is locked` on the Azure Files share; fixed with the `nobrl,uid=10001,gid=10001` mount
      options in the Bicep. The identity's `Cognitive Services OpenAI User` role worked on the
      first call this time (the user's own assignment the day before took 35 minutes).

## 6. Known limitations to confirm, not fix

- [ ] A call whose *estimate* does not fit the remaining budget is rejected even if its actual
      cost would have (ADR-0007); lower `reserve_output_tokens` or send `max_tokens` to tighten.
- [ ] Rate limits are per gateway instance (ADR-0008).
- [ ] `stream: true` streams; the cost arrives in the final event, not in headers (ADR-0012).
- [ ] Budgets reset at UTC month start, not local time.

## Sign-off

| Section | Result | Date | Notes |
| ------- | ------ | ---- | ----- |
| 0 Preconditions | pass | 2026-10-28 | ruff, mypy strict, pytest green on Python 3.12 |
| 1 Simulated demo | pass | 2026-10-28 | 300 calls: 240 served, 60 rejected, 0.00 unallocated; chart rendered |
| 2 Ollama | pass | 2026-10-28 | 400 missing attribution, 403 model_not_allowed, 200 with `X-FinOps-*` headers, summary row; first call 7.4 s (model load), then about 3 s |
| 3 Budget lifecycle | pass (3.1, 3.2) | 2026-10-28 | 86.7 % warn with header and log line; 100 % block with structured 403; 3.3 to 3.5 not run |
| 3b Team keys | pass | 2026-10-28 | 401 missing_api_key with `WWW-Authenticate`, 401 invalid_api_key, 200 with `key_id`, 403 team_mismatch |
| 3c Rate limit | pass | 2026-10-28 | `403 403 403 429`, `Retry-After: 60`, log `reason=rate_limited rpm=3` |
| 3d Tracing | pass | 2026-10-30 | Jaeger 2.5.0 via OTLP/HTTP on 4318; `POST /v1/chat/completions` spans carry `finops.team`, `finops.project`, `finops.use_case`, `finops.model`, `finops.cost_usd`, `finops.input_tokens`, `finops.output_tokens`, `finops.request_id`; `/healthz` not traced |
| 4 Azure OpenAI | pass (4.1 to 4.4, 4.6) | 2026-10-30 | `gpt-4.1-mini-dev` in Sweden Central; 30-call run then an 800-call run (713 sent, 662 served, 0.0576 USD) with a policy copy that allows the model for every team; ledger total for the day 674 rows, 12,214 input and 33,448 output tokens, 0.0584024 USD; cost by hand matches to the last digit; 502 `DeploymentNotFound` with 0 ledger rows; v1 endpoint via `openai_compat` ok. 4.7 reasoning params on `gpt-5-mini-dev` (Global Standard) pass 2026-10-30: 200 with translated params, 60-token limit gave empty content and `length`, 400-token limit gave "Ready" with 74 completion tokens, cost 0.00015125 USD matches by hand. 4.8 streaming pass 2026-10-30: 13 events, text intact, final usage 16 in / 10 out from Azure (not estimated), cost 0.0000224 USD, ledger row written; gpt-5-mini stream reported 300 reasoning tokens. 4.9 Entra pass 2026-10-30: keyless chat and stream through the gateway with the `az login` identity, both ledgered; the `Cognitive Services OpenAI User` role took about 35 minutes to propagate (first `PermissionDenied` naming the principal, then "Principal does not have access to API/Operation", then 200). 4.10 tool calling pass 2026-10-30: `tool_calls` round trip, 7 streamed fragments assembling `{"city":"Pecs"}`, JSON mode, content parts, 422 `extra_forbidden` for `foo`. 4.5 pass 2026-10-31 (see the reconciliation below) |
| 4b Claude Code | pass | 2026-11-16 | LiteLLM 1.108.0, Claude Code 2.1.325, Anthropic API; 50 calls, 0.990507 USD on `claude-fable-5-2`, `claude-opus-5-5`, `claude-sonnet-5-5` and `claude-haiku-5-5`. 20 scripted calls (plain, streamed, cache write and read at about 24,300 tokens per model, a 1-hour write, a tool round trip): tokens and cost equal Anthropic's usage priced by hand, 0 mismatches. Seven Claude Code sessions, one with a subagent: the ledger equals Claude Code's own `total_cost_usd` (0.232871 USD over six of them, difference 0). The ledger equals LiteLLM's spend log per model: 50 calls, 0.990507 USD on both sides. Rejections and the live budget block as expected. Claude Code asks for `max_tokens` 128,000, so the reservation of one call in flight was 0.07 USD on Haiku, 1.30 on Sonnet, 2.61 on Opus and 5.22 on Fable; `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8000` brought Opus down to 0.21. With `reserve_output_tokens_max: 16000` on the team's budget (ADR-0028, same day) the reservations were 0.01, 0.18, 0.37 and 0.74 USD. The Anthropic Console showed 0.99 USD for the same period, the ledger 0.990507 |
| 4c GitHub Copilot | pass | 2026-11-19 | LiteLLM 1.108.0, Copilot CLI 1.0.114, VS Code 1.143.1 with Copilot Chat 0.71.0, a stand-in upstream (fixed usage of 1,200 prompt tokens, 1,000 of them cached, and 7 output tokens per call), so no model cost and no check of token counts against a provider. CLI: both wire APIs served and recorded; before the change of the same day a Responses call passed the plugin without a ledger row (ADR-0029). Aliases, phase keys, use case lists and the use case budget behaved as expected; the lowered budget was in force within 6 seconds. Ledger rows 0.00741 USD, the team's counter and the report total the same. VS Code sent the three headers on both routes and 128 tool definitions per call. Clients on a rejection: Copilot CLI prints the message of a `400` and replaces a `403` with "Authentication failed with provider (HTTP 403)"; VS Code retries five times, then prints `error.message` of a `403` and the whole body of a `400`. On the chat completions route that message was the bare code when VS Code was first tested; ADR-0034 put the explanation there, and VS Code then showed `502 model_not_allowed: model 'claude-haiku-5-5' is not allowed for use case 'plan' of project 'my-repo' (allowed: claude-opus-5-5)` after its five retries. 4c.10 with the Anthropic API: five calls from the CLI, 0.1925295 USD, each about 23,300 input tokens with no cache reads; three from VS Code (Haiku reading a file through a tool, Sonnet answering), about 87,000 input tokens each with 128 tool definitions, 0.1932449 USD; all eight equal to `LiteLLM_SpendLogs` per call in tokens and cost. VS Code lists one model per id in a group, so a second entry with the same id did not appear in its picker; the Responses route needed `drop_params: true` in the LiteLLM config (the CLI sends `prompt_cache_key`), and the refused call left no reservation behind. Not covered: the JetBrains plugin, the coding agent that runs on GitHub's side |
| 4c.11 Copilot, phases by model name | pass, one finding | 2026-11-20 | 0.5.2 on Azure Container Apps (PostgreSQL ledger), LiteLLM 1.108.0, VS Code with the Custom Endpoint provider, the Anthropic API. 16 calls on `sdd-plan`, `sdd-implement` and `sdd-tasks` without `X-Use-Case`: every row under the phase of its name, with `model_alias`. Six calls without cache marks: 1.5164398 USD, equal to LiteLLM's spend log per call, no cache tokens. Ten with `cache_control_injection_points` on the system message: 85,273 prompt tokens written on a model's first call and read on the calls after it (Sonnet 0.24 USD, then 0.04 and 0.05 where an uncached call was 0.20); nine equal to the spend log. Finding: the tenth, a Haiku call with 102,646 prompt tokens, was 0.00288303 USD in the ledger and 0.01441515 in the spend log, because Haiku has a second set of prices above 100K tokens; fixed by `long_prompt` in the price book (ADR-0037, #103), which prices that call at 0.01441515. Not covered: Copilot CLI and the Responses route with the injection points, the pinned key on the deployed proxy |
| 5 Docker | pass (image) | 2026-10-30 | image builds, runs as uid 10001, healthcheck `healthy`, live call through the container ok. `docker compose config` fails on Compose 2.23 (`env_file` long syntax needs 2.24+); compose with Ollama not brought up |
| 6 Limitations | confirmed by tests | 2026-10-28 | estimate-based rejection and per-instance rate limits covered by unit tests |

## Observations from the 2026-10-28 run (fixed in this release)

- Zero costs rendered as `0.0` in headers and body while the summary said `0`; all money
  strings now go through one formatter (`pricing.money`) and the suite has a regression test.
- `keys new` did not say that `keys: []` has to become `keys:` before pasting the entry; the
  CLI now prints that hint and the follow-up `keys check` command.
- Two checkouts of the repository with one venv caused a "file not found" moment; the venv and
  the config files must be in the same checkout, because `serve` reads `config/` relative to the
  working directory. Documented in the quick start.
- Ollama on macOS 13 is not installable from Homebrew (no bottles, source build fails) and the
  desktop app requires macOS 14; macOS 26 installed fine. The first `ollama pull` stalled on a
  flaky network with a DNS failure to the Cloudflare R2 blob host; a second attempt on another
  network completed.

## Observations from the 2026-10-30 Azure run

- `gpt-4o-mini` version 2024-07-18 is deprecated for new deployments since 2026-03-31; the
  resource was created with `gpt-4.1-mini` 2025-04-14 instead, which is already in the price
  book. The setup guide and this plan now use it.
- With the deployment at 10K tokens per minute one of the demo's 30 calls got an Azure 429
  (token rate limit), which the gateway reports as HTTP 502 `upstream_error` and does not
  record. Capacity was raised to 50K; the setup guide says so. Mapping upstream 429 to 429
  with `Retry-After` instead of 502 is a candidate improvement.
- The demo's `--model` override sends the same model to every team, so `research` and
  `customer-support`, whose allow-lists name `gpt-4o-mini` only, were rejected with 403
  `model_not_allowed`. Correct behaviour, but for a cost-focused live run either widen those
  lists in a copy of the policy file or accept the rejections.
- The spend of a 30-call run (0.0007 USD) rounds to 0.00 in Azure Cost Management. For a
  reconciliation to the cent, send a few hundred calls (300 calls are about 0.01 USD) or
  longer prompts before comparing.
- `docker-compose.yml` uses the `env_file: [{path: .env, required: false}]` form, which needs
  Docker Compose 2.24 or newer; Docker Desktop 4.25 (Compose 2.23) rejects the file. The README
  states the minimum version. The image itself was tested with `docker run --env-file .env`.
- In the 800-call run 87 calls (11 %) were throttled by Azure even at 50K tokens per minute,
  because the deployment's requests-per-minute allowance is derived from the token capacity
  (6 RPM per 1K TPM) and the demo sends calls back to back. These are now returned as `429
  upstream_rate_limited` with the provider's `Retry-After` instead of 502; the gateway's own
  per-team rate limit is the right tool to stay under the deployment's allowance.
- For the Cost Management reconciliation: `azure-live.db` holds 674 rows / 0.0584024 USD for
  2026-10-30. Four further calls were made outside that ledger (v1 endpoint smoke, two tracing
  calls, one Docker call), about 0.00005 USD, so the Azure figure should read 0.06 USD.
- The `gpt-4.1-mini-dev` deployment was created as regional `Standard`; the Retail Prices API
  lists regional gpt-4.1-mini at 0.484 / 1.936 USD per 1M tokens, 21 % above the Global
  Standard list price the price book carries (0.40 / 1.60). The 2026-10-30 usage (12,214 in,
  33,448 out) will therefore be billed about 0.071 USD against the ledger's 0.0584 USD. The
  deployment was recreated as `GlobalStandard` on 2026-10-30 at about 17:00 CET, so later
  usage is priced as the book says. `pricing sync --deployment regional` would have shown it.
- Role assignments on Azure OpenAI can take far longer than the documented 5 minutes to reach
  the data plane: 35 minutes here, on a new subscription. The error message changes on the
  way (`lacks the required data action` first, `Principal does not have access to
  API/Operation` later) without meaning anything different. A fresh token does not help;
  waiting does.

## Reconciliation of 2026-10-30 against Azure Cost Management (run 2026-10-31)

Cost Management query at subscription scope filtered to the resource, daily granularity,
grouped by meter (the two aggregations the API allows per query: cost and usage quantity,
then cost in USD). Tokens are quantity times the meter unit (1K or 1M).

| Meter (Azure)                    | Azure tokens | Ledger tokens | Azure USD | Ledger USD at that SKU |
| -------------------------------- | -----------: | ------------: | --------: | ---------------------: |
| gpt 4.1 mini Inp regnl Tokens    | 12,263 | 12,214 (before 15:27Z) | 0.005935 | 0.005912 |
| gpt 4.1 mini Outp regnl Tokens   | 33,462 | 33,448 (before 15:27Z) | 0.064782 | 0.064755 |
| gpt 4.1 mini Inp glbl Tokens     | 5,634  | 5,598 (after 15:27Z)   | 0.002254 | 0.002239 |
| gpt 4.1 mini Outp glbl Tokens    | 13,070 | 13,062 (after 15:27Z)  | 0.020912 | 0.020899 |
| GPT 5 Mini Inpt Glbl 1M Tokens   | 151    | 64                     | 0.000038 | 0.000016 |
| GPT 5 Mini outpt Glbl 1M Tokens  | 1,290  | 710                    | 0.002580 | 0.001420 |
| **Total**                        | 65,870 | 65,096                 | **0.096501** | **0.095241** |

- Azure billed 0.096501 USD (0.084918 EUR, Microsoft's rate 0.8800). The ledger recorded
  0.082977 USD. The gap splits exactly in two:
  - **0.012265 USD price book SKU mismatch**: the deployment was regional `Standard` until
    15:27Z while the book carried Global Standard prices (0.40 / 1.60 against 0.484 / 1.936).
    Repricing the 674 pre-switch calls at the regional meters gives 0.070667 USD, which is what
    Azure charged for those tokens. `pricing sync --deployment regional` reports exactly this.
  - **0.001260 USD calls made outside the gateway**: 172 input and 602 output tokens from
    direct API tests during the day (gpt-5-mini curl tests, one keyless Entra stream, the v1
    endpoint and tracing smokes). The ledger cannot see what did not pass through it; this is
    the "block direct access" step of the rollout guide.
- With those two explained, the per-meter token counts match to within the off-ledger calls
  and the per-meter charges match to the sixth decimal. The ledger reproduces the invoice
  when the book carries the deployment's SKU price, which is the point of ADR-0002 and
  ADR-0013.
- Time split: the ledger's timestamps are UTC; Cost Management days are UTC; the deployment
  switch time comes from the deployment's `systemData.createdAt`.
