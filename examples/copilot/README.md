# GitHub Copilot behind the FinOps plugin

GitHub Copilot can call a custom OpenAI-compatible endpoint instead of GitHub's models: the
Custom Endpoint provider in VS Code and `providers.json` in Copilot CLI. Point either at a
LiteLLM proxy that runs the FinOps plugin and every call is attributed to a team, a project
and a use case, priced from the price book, checked against the budgets before it is sent,
and written to the ledger (ADR-0029).

The use case is where a development cycle in phases fits: `plan`, `implement` and `tasks` are
use cases of the project, each with the model it may use and, if you like, a budget of its own
(ADR-0031). An optional run id ties the calls of one feature together (ADR-0030).

Measured on 2026-11-19 with Copilot CLI 1.0.114 and VS Code 1.143.1 (Copilot Chat 0.71.0)
against LiteLLM 1.108.0 ([test plan, section 4c](../../docs/testing-plan.md)). The routes, the
headers, the rejections and what each client shows were measured with a stand-in upstream that
answers every call the same way. Copilot CLI was then run against the Anthropic API through
this stack as shipped: a plain call on Opus, a task that reads a file on Sonnet, the same task
on Haiku over the Responses API; VS Code the same way on Haiku (a file read through a tool)
and on Sonnet. Every call was recorded with its phase, and the ledger matched LiteLLM's own
spend log call by call.

## Run it

```bash
cd examples/copilot
ANTHROPIC_API_KEY=sk-ant-... docker compose up -d --build
```

The proxy listens on port 4003, the report page on port 8083. Create a LiteLLM team whose
alias matches a team in `policies.yaml`, then the keys:

```bash
B=http://localhost:4003; M=sk-lfg-master-local-only; CT="Content-Type: application/json"
TEAM=$(curl -s -X POST $B/team/new -H "Authorization: Bearer $M" -H "$CT" \
  -d '{"team_alias":"dev"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["team_id"])')
key() {  # alias, metadata JSON
  curl -s -X POST $B/key/generate -H "Authorization: Bearer $M" -H "$CT" \
    -d "{\"team_id\":\"$TEAM\",\"key_alias\":\"$1\",\"metadata\":$2}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["key"])'
}
key alice-vscode    '{}'
key alice-plan      '{"finops":{"project":"my-repo","use_case":"plan","pin":true}}'
key alice-implement '{"finops":{"project":"my-repo","use_case":"implement","pin":true}}'
```

The first key is for VS Code, which sends the project and the phase as headers. The other two
are for Copilot CLI, which cannot send headers: there the key carries them. `"pin": true` makes
what the key carries binding (ADR-0035): a call with `alice-plan` that claims another phase or
project is refused with 403.

## VS Code

Run `Chat: Manage Language Models`, choose `Add Models`, then `Custom Endpoint`. Give the group
a name, paste the `alice-vscode` key and pick `Chat Completions`. VS Code opens
`chatLanguageModels.json` with a new entry; replace its `"models": [...]` with the three
models of [`chatLanguageModels.json`](chatLanguageModels.json) in this folder and save.

Each model entry is one phase: it names the model and sends the phase with `requestHeaders`.
VS Code lists one entry per model id in a group, so every phase needs an id of its own; two
phases on the same model need two names for it (see the last point of "Good to know").

```json
{
  "id": "claude-opus-5-5",
  "name": "Plan (Opus)",
  "url": "http://localhost:4003/v1/chat/completions",
  "toolCalling": true,
  "requestHeaders": { "X-Project": "my-repo", "X-Use-Case": "plan" }
}
```

Pick `Plan (Opus)` in the model picker of the chat and the call is recorded as team `dev`,
project `my-repo`, use case `plan`. Add `"X-Run-Id": "feature/142"` to the headers while you
work on a feature and the report page shows that feature's cost per phase and model.
With `"apiType": "responses"` and a URL ending in `/v1/responses` the same entry uses the
Responses API; add `"statefulResponses": false`.

## Copilot CLI

Copy [`providers.json`](providers.json) to `~/.copilot/providers.json` (or point
`COPILOT_PROVIDERS_CONFIG` at it) and paste the two keys. A provider is one phase here: its key
carries the project and the use case.

```bash
copilot --model plan/claude-opus-5-5          # recorded as use case plan
copilot --model implement/claude-sonnet-5-5   # recorded as use case implement
```

With `COPILOT_OFFLINE=true` the CLI runs against the custom provider without a GitHub sign-in.
`"wireApi": "responses"` on a provider switches it to the Responses API.

## What the developer sees

The policy decides before the model is called. What reaches the developer depends on the
client:

| Situation | Answer of the proxy | VS Code | Copilot CLI |
| --- | --- | --- | --- |
| A model outside the phase (`plan` with Sonnet) | `403 model_not_allowed: model ... is not allowed for use case 'plan' ...` | the message, after five retries | "Authentication failed with provider (HTTP 403)" |
| A budget used up (team, project, model or use case) | `403 budget_exceeded: ... has used ...% of its monthly budget ...` | the message, after five retries | the same authentication message |
| No project (no header, none on the key) | `400 missing_attribution: Set X-Project ...` | the whole response body, after five retries | the message |
| A model without a price | `422` | not measured | not measured |

Copilot CLI replaces every `403` with its own authentication message and drops the reason;
tell CLI users to look at the report page when they see it (#87).

## What is recorded

One ledger row per call: team, project, use case, model, run id, tokens (cached ones at their
own price) and cost. The report page on port 8083 groups the chargeback by use case, lists the
runs and shows one run on request; `/v1/finops/summary?group_by=use_case&group_by=run_id`
returns the same as JSON, `/v1/finops/summary.csv` as CSV.

## Good to know

- **Client settings are not the control.** The headers in `chatLanguageModels.json` are what
  the developer's machine says, and one edited line claims another phase. That is fine for a
  walk-through and for attribution inside a team's budget. Where the phase must hold, let the
  model name carry it: an alias with a use case (next point) needs no header and one key per
  developer. Pinned keys as above are for pipelines and agents, which are few.
- **Only what goes through the endpoint is recorded.** The coding agent that runs on GitHub's
  side and inline suggestions do not use these settings; their usage is in GitHub's own Copilot
  usage metrics.
- **The prompt is large and was not cached.** Copilot CLI sent about 23,300 input tokens with
  every call (its instructions and 19 tool definitions), and none of them came back as cache
  reads on either wire format: a call cost 0.093 USD on Opus, 0.047 on Sonnet and 0.0024 on
  Haiku before it wrote a word. A task with one tool call is two such calls.
- **`drop_params: true` is needed.** Copilot sends OpenAI-only parameters (`prompt_cache_key`
  on the Responses API); without the setting in `config.yaml` LiteLLM refuses the call when the
  model is not an OpenAI one. The reservation of such a refused call is given back.
- **No `max_tokens`.** Neither client sends one, so a call in flight reserves
  `reserve_output_tokens` of the team's budget (4,000 in this example).
- **Tool definitions are part of the prompt.** VS Code sent 128 of them with every call in the
  test (its cap; the test machine had more tools enabled), about 87,000 input tokens per call
  and none of them cached: a one-word exchange cost 0.18 USD on Sonnet and 0.009 on Haiku.
  Fewer enabled tools mean a smaller prompt.
- **Retries.** VS Code repeats a rejected call five times before it shows the error, so a
  rejection reaches the proxy six times. A budget rejection uses six slots of the team's rate
  limit window.
- **One endpoint, two wire formats.** Both clients work on `/v1/chat/completions` and on
  `/v1/responses`.
- **Organisation policy.** On Copilot Business and Enterprise an administrator has to allow
  custom language model keys before VS Code offers the provider.
- **A role instead of a model.** A price book row `sdd-plan: { alias_of: claude-opus-5-5 }` and a
  LiteLLM `model_name: sdd-plan` let clients call the phase by name; changing the model behind
  it is then one reviewed line (ADR-0033). In VS Code this is also how two phases share a
  model, because a group cannot hold the same model id twice. With
  `sdd-plan: { alias_of: claude-opus-5-5, use_case: plan }` the name also says the phase
  (ADR-0036): the model entry needs no `X-Use-Case`, and a call to it that claims another phase
  is refused with 403. This variant was not part of the measurements above.
