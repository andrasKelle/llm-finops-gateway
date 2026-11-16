# Claude Code behind the FinOps plugin

Claude Code speaks the Anthropic Messages API. Point it at a LiteLLM proxy that runs the
FinOps plugin and every call it makes is attributed to a team and a project, priced from the
price book (cache reads and cache writes included), checked against the team's, the project's
and the model's budget before it is sent, and written to the ledger (ADR-0027).

Tested on 2026-11-16 with Claude Code 2.1.325 and LiteLLM 1.108.0 against the Anthropic API:
over 50 calls on four models the ledger matched Anthropic's usage priced by hand, the cost
Claude Code itself reports for a session, and LiteLLM's own spend log
([test plan, section 4b](../../docs/testing-plan.md)).

## Run it

```bash
cd examples/claude-code
ANTHROPIC_API_KEY=sk-ant-... docker compose up -d --build
```

The proxy listens on port 4002, the report page on port 8082. Create a LiteLLM team whose
alias matches a team in `policies.yaml`, and a key for a developer in it:

```bash
B=http://localhost:4002; M=sk-lfg-master-local-only
TEAM=$(curl -s -X POST $B/team/new -H "Authorization: Bearer $M" -H "Content-Type: application/json" \
  -d '{"team_alias":"dev"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["team_id"])')
curl -s -X POST $B/key/generate -H "Authorization: Bearer $M" -H "Content-Type: application/json" \
  -d "{\"team_id\":\"$TEAM\",\"key_alias\":\"alice-laptop\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["key"])'
```

## Point Claude Code at it

```bash
export ANTHROPIC_BASE_URL=http://localhost:4002
export ANTHROPIC_AUTH_TOKEN=<the key printed above>
export ANTHROPIC_CUSTOM_HEADERS="X-Project: my-repo"
claude
```

- `ANTHROPIC_AUTH_TOKEN` is the LiteLLM key; the team comes from it. The Anthropic API key
  stays on the proxy.
- `ANTHROPIC_CUSTOM_HEADERS` carries the attribution. One `Name: Value` per line, so a use
  case goes on a second line: `X-Use-Case: coding`. Setting the variable per repository (a
  `.envrc`, or the `env` block of the repository's `.claude/settings.json`) makes the project
  follow the directory the developer works in.
- A key that serves one repository or one pipeline can carry the project itself, and then no
  header is needed: `"metadata": {"finops": {"project": "payments-api", "use_case": "ci"}}`
  on `/key/generate`.

Open http://localhost:8082/finops to see the spend per team, project and model.

## What the developer sees

| Situation | Answer to Claude Code |
| --- | --- |
| No `X-Project` and no project on the key | `400 missing_attribution`, before the call |
| Project not in the team's registry | `403 project_not_registered` |
| Model without a row in the price book | `422`, "No price configured for model ..." |
| Model outside the team's or the project's list | `403 model_not_allowed` |
| Team, project or model budget used up | `403 budget_exceeded`, naming the budget and the amounts |
| Past the warn threshold | the call is served; `X-FinOps-Warning` says so, and the alert webhook when one is configured |

Claude Code prints the rejection as an API error with the explanation, for example:

```text
API Error: 400 missing_attribution: Set X-Project so the call can be charged back, or give
the key a default project in its metadata ({"finops": {"project": ...}}); the team comes from
the LiteLLM key.

Failed to authenticate. API Error: 403 project_not_registered: project 'pilot-7' is not
registered for team 'dev' (registered: my-repo, payments-api); add it to policies.yaml
```

"Failed to authenticate." is what Claude Code puts in front of every 403; the key is fine,
the text after it is the reason. Nothing is sent to Anthropic and nothing is charged for a
rejected call.

## What is recorded

One ledger row per call: team, project, use case, key alias, provider, model, the token counts
and the cost, with the price book version. The prompt is priced in four parts, each at its own
rate from the price book row:

| Part of the prompt | Ledger column | Price book key |
| --- | --- | --- |
| read from Anthropic's prompt cache | `cached_input_tokens` | `cached_input` |
| written to the cache, 5-minute lifetime | `cache_write_input_tokens` minus the 1-hour part | `cache_write` |
| written to the cache, 1-hour lifetime | `cache_write_1h_input_tokens` | `cache_write_1h` |
| everything else | the rest of `input_tokens` | `input` |

`input_tokens` is the whole prompt. Claude Code caches aggressively, so most of a session is
billed at the cache-read price; the ledger shows that split per call.

## Good to know

- **The reservation is capped in `policies.yaml`, because Claude Code asks for 128,000
  output tokens.** Before the call the plugin reserves the prompt at the input price plus
  the request's `max_tokens` at the output price, and replaces it with the real cost when the
  call ends. Uncapped, one call in flight reserved about 0.07 USD on Haiku, 1.30 on Sonnet,
  2.61 on Opus and 5.22 on Fable in the live test, times the number of parallel calls
  (subagents). The `dev` team's budget sets `reserve_output_tokens_max: 16000`, so a
  reservation assumes at most 16,000 output tokens: measured 0.01 USD on Haiku, 0.18 on
  Sonnet, 0.37 on Opus and 0.74 on Fable. A call that writes
  more than the cap settles above its reservation; pick a cap above a normal call's output
  (ADR-0028). `CLAUDE_CODE_MAX_OUTPUT_TOKENS` on the developer's side lowers what Claude Code
  asks for, which has the same effect on the reservation.
- **Token counting is free and unattributed.** `/v1/messages/count_tokens` passes through
  the proxy without the plugin: no price, no ledger row, no `X-Project` needed.
- **Every model id needs a row.** Claude Code picks its model from its own settings; an id
  that is not in `config.yaml` and in `config/pricing.yaml` is rejected. The shipped rows are
  Anthropic's list prices. Claude on Microsoft Foundry has the same prices; Amazon Bedrock and
  Google Vertex AI have their own, so give those deployments their own rows.
- **The Haiku row is flat.** Anthropic prices Claude Haiku 5.5 higher for prompts above 100K
  tokens; the price book has one price per model, so such calls are under-priced in the
  ledger.
- **Other clients work the same way.** Anything that calls `/v1/messages` on the proxy (the
  Anthropic SDKs with a `base_url`, the Claude Agent SDK) goes through the same rules.
