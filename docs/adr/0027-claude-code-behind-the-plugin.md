# 0027. Claude Code and other Anthropic Messages clients run behind the LiteLLM plugin

Date: 2026-11-16
Status: Accepted (the chat completions route was aligned with this one by ADR-0034)

## Context

Coding agents are where LLM spend grows fastest and where it is least attributed: every
developer runs one, each session makes hundreds of calls, and the invoice shows one line.
Claude Code speaks the Anthropic Messages API, not the OpenAI one, so neither the gateway nor
the plugin (ADR-0019), which handled chat completions only, could see its calls. LiteLLM
already serves `/v1/messages` and routes it to Anthropic, Bedrock, Vertex AI or Foundry.

Measured on LiteLLM 1.108.0 with a probe callback and with Claude Code 2.1.325:

- the pre-call hook runs with the call type `anthropic_messages`; the request headers are
  where they are for chat completions;
- on this route `metadata` is the Anthropic request field (`user_id`), and LiteLLM keeps its
  own metadata under `litellm_metadata`;
- a non-streaming response reaches the response-headers hook as a plain dictionary in the
  Anthropic shape, without the provider; the success event carries LiteLLM's usage for
  streamed and non-streamed calls alike: the whole prompt as `prompt_tokens`, with
  `cached_tokens`, `cache_creation_tokens` and the split of the written tokens by lifetime;
- `/v1/messages/count_tokens` does not run the pre-call hook.

Anthropic bills a prompt in four parts: fresh input, cache reads, and cache writes with a
5-minute or a 1-hour lifetime (1.25 and 2 times the input price). An agent session is mostly
cache reads, so a ledger that prices the whole prompt at the input rate is wrong by a large
factor, in the expensive direction.

## Decision

- The plugin handles the `anthropic_messages` call type with the same five steps as a chat
  completion. Team from the LiteLLM key, project and use case from `X-Project` and
  `X-Use-Case` (Claude Code sends them through `ANTHROPIC_CUSTOM_HEADERS`) or from the key's
  metadata. Its marker goes under `litellm_metadata` on this route; the Anthropic `metadata`
  field is not touched.
- The pre-call estimate counts the Anthropic content blocks (`text`, the `input` of
  `tool_use`, the content of `tool_result`), the `system` prompt and the tool definitions,
  and takes the output from `max_tokens`.
- A rejection on this route puts its explanation next to the code in `error`
  (`budget_exceeded: team 'dev' has used ...`), with the bare code in `code`. LiteLLM passes
  only `error` on as the message and Claude Code shows the developer nothing else. The chat
  route keeps the bare code in `error`.
- Every `/v1/messages` call is settled from the success event. The response headers carry the
  request id and the budget state, not the cost.
- A price book row may carry `cache_write` and `cache_write_1h`. Without them written tokens
  cost the input price, and the 1-hour price falls back to the 5-minute one. `cost()` takes
  the whole prompt and its three priced parts.
- The ledger row stores `cache_write_input_tokens` and, of those, the
  `cache_write_1h_input_tokens` (revision `0003`), so a past month can be re-priced from the
  row alone.
- This is the first revision the code cannot work without. As ADR-0026 announced, the ledger
  now refuses to open a database whose revision is not the one the code expects, with a
  message that names `ledger migrate`. The gateway does not start and the plugin answers
  every call with an error, instead of serving calls it cannot record.
- The repository's price book gets rows for the current Claude models at Anthropic's list
  prices, marked `provider: anthropic`. They are served through the plugin; the standalone
  gateway has no Anthropic adapter.
- `examples/claude-code/` is a compose stack with the proxy, the plugin, a policy file with
  per-model budgets and the report page.

## Consequences

- A team's coding-agent spend is attributed, capped per team, project and model, and
  reconcilable per meter like any other call.
- The token-counting route stays outside the rules: free at the provider, not attributed.
- One price per model: Anthropic's higher rate for Claude Haiku 5.5 prompts above 100K tokens
  is not modelled, and Bedrock or Vertex AI prices need their own rows.
- The reservation is sized by the request's `max_tokens`, and Claude Code asks for 128,000:
  one call in flight reserved 1.30 USD on Sonnet, 2.61 on Opus and 5.22 on Fable in the live
  test. A budget must have that much headroom per parallel call; it is given back when the
  call ends. Capping the reserved output is a separate decision.
- The standalone gateway warns at start-up about the `anthropic` rows, as it does for every
  provider that is not configured, and would route such a call to its default provider.
- Upgrading needs `ledger migrate` before the first start; a missed migration is now a
  refusal at start-up, not a failed insert after a paid call.
