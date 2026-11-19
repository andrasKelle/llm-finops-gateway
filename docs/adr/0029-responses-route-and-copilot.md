# 0029. The Responses API route and GitHub Copilot behind the LiteLLM plugin

Date: 2026-11-19
Status: Accepted

## Context

GitHub Copilot can call a custom OpenAI-compatible endpoint: the Custom Endpoint provider in
VS Code and `providers.json` in Copilot CLI. Either can speak chat completions or the OpenAI
Responses API, chosen per provider (`wireApi`) or per model (`apiType`). The plugin (ADR-0019,
ADR-0027) handled chat completions and `/v1/messages` and returned every other call type to
LiteLLM untouched, so a Responses call ran without an owner, a price or a budget.

Measured on LiteLLM 1.108.0 with a probe callback, a fake upstream and Copilot CLI 1.0.114:

- with `wireApi: completions` the CLI calls `/v1/chat/completions` with `stream: true` and
  `stream_options.include_usage`; the plugin attributed, priced and settled it as it was;
- with `wireApi: responses` it calls `/v1/responses` with `input`, `instructions`, `tools` and
  `stream: true`; the pre-call hook runs with the call type `aresponses`, LiteLLM's metadata is
  under `litellm_metadata`, and the success event carries the usage in the chat shape
  (`prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`) for streamed and
  non-streamed calls alike;
- the CLI sends neither `max_tokens` nor `max_output_tokens`, and no custom headers;
- the CLI runs without a GitHub sign-in when a custom provider is configured and
  `COPILOT_OFFLINE=true`;
- on a rejection the CLI prints the error message LiteLLM builds from `detail["error"]`.

## Decision

- **`/v1/responses` runs the same steps as the other two routes.** The call types `responses`
  and `aresponses` go through attribution, the price book, the policy and the reservation in
  the pre-call hook.
- **The estimate reads the Responses request.** `input` (a string or a list of items),
  `instructions` and `tools` are the prompt; `max_output_tokens` bounds the output, and without
  it the budget's `reserve_output_tokens` and `reserve_output_tokens_max` apply (ADR-0028).
- **Every Responses call settles from the success event,** like `/v1/messages`: the response
  object carries the Responses usage shape, the success event the one the plugin already reads.
  The response headers carry the request id and the budget state at the start of the call.
- **The plugin's marker goes under `litellm_metadata`;** `metadata` is the client's own field on
  this route and is not touched.
- **A rejection speaks.** The `error` is `code: explanation` with the bare code in `code`, as on
  `/v1/messages`, because a coding agent shows its user only that message.
- **Attribution without headers comes from the key.** Copilot CLI cannot send `X-Project`, so a
  key per tool or per phase carries the project and the use case in its LiteLLM metadata
  (`{"finops": {"project": ..., "use_case": ...}}`); VS Code can set the headers per model with
  `requestHeaders`.

## Consequences

- Copilot CLI works behind the plugin on both wire APIs; a Responses call can no longer pass
  around the price book and the budgets.
- The cost of a non-streamed Responses call is not in its response headers; it is in the
  ledger a moment later, as for `/v1/messages`.
- Call types the plugin does not know (embeddings, images, audio, the legacy completions
  route) still pass through unmetered. Embeddings are the next route to cover; until then a
  team's LiteLLM models should not include an embedding model the price book has no row for.
- The VS Code path (Custom Endpoint with `requestHeaders`) is configured the same way but was
  not measured here; `examples/copilot/` will carry both once it is.
