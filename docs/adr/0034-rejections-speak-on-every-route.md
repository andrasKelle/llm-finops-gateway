# 0034. A plugin rejection carries its explanation in the message on every route

Date: 2026-11-19
Status: Accepted

## Context

LiteLLM turns a rejection of the plugin into `{"error": {"message": ..., ...}}` and takes the
message from `detail["error"]` alone. On `/v1/messages` and `/v1/responses` the plugin puts
`code: explanation` there (ADR-0027, ADR-0029); on chat completions it kept the bare code, so
that a client could compare the message with a code, and the explanation sat one level down
in `provider_specific_fields.message`.

Measured with VS Code 1.143.1 (Copilot Chat 0.71.0) against the plugin: for a `403` on the chat
completions route the editor shows `error.message` and nothing else, so a developer whose model
is outside the allow-list or whose budget is used up reads `model_not_allowed` or
`budget_exceeded` without the reason, the amounts or the name of the budget (#87).

## Decision

- **The chat completions route speaks like the other two.** Every rejection of the plugin puts
  `code: explanation` into `error` and the bare code into `code`. The status codes, the other
  fields of the body and the headers are unchanged.
- **The gateway is not touched.** Its rejections are FastAPI `detail` objects with `error` and
  `message` side by side, which a client reads in full.

This replaces the last sentence of the rejection bullet in ADR-0027.

## Consequences

- VS Code and every client that prints the error message show the reason of a block.
- A client of the LiteLLM proxy that compares `error.message` with a bare code must read
  `provider_specific_fields.code` instead, or match the prefix up to the colon. This is a
  behaviour change of the plugin on chat completions, noted in the changelog.
- Copilot CLI still hides the body of a `403`; that case stays open in #87, together with the
  retries VS Code makes before it shows a rejection.
