# 0028. A budget may cap the output tokens its reservation assumes

Date: 2026-11-16
Status: Accepted

## Context

The pre-call reservation prices the prompt at the input rate and the request's `max_tokens` at
the output rate (ADR-0007). That is an upper bound, and for most clients a reasonable one.
Coding agents break it: Claude Code asks for `max_tokens` 128,000 on every call and produces
a few hundred to a few thousand tokens. In the live test of 2026-11-16 one call in flight
reserved 1.30 USD on Claude Sonnet 5.5, 2.61 on Opus 5.5 and 5.22 on Fable 5.2, while the calls
cost cents. A budget needs that much free room per parallel call, so a 20 USD project budget
refused the fourth parallel Fable call with almost nothing spent. The only remedy was on the
client (`CLAUDE_CODE_MAX_OUTPUT_TOKENS`), which the platform team does not control.

## Decision

- A budget block (team, project or model budget) may set `reserve_output_tokens_max`: the most
  output tokens a reservation assumes, whatever `max_tokens` asks for. Without the key the
  reservation is what it was.
- One estimate is made per call and reserved on every budget that applies to it, as before.
  The cap of the call is the smallest `reserve_output_tokens_max` among those budgets. A cap on
  a model budget therefore bounds the calls of that model, a cap on the team's budget bounds
  every call of the team.
- The request is not changed: the client's `max_tokens` goes upstream as sent. The cap bounds
  the reservation only, and the call is settled at its real cost.
- A streamed call whose provider reports no usage is still billed at the uncapped estimate
  (ADR-0012); the cap is about admission, not about billing.
- The same rule runs in the gateway and in the LiteLLM plugin through the shared estimate.

## Consequences

- A budget can be sized to what the team is meant to spend, not to the worst case of its
  client's `max_tokens`.
- The guarantee of ADR-0007 weakens where a cap is set, and only there: a call that produces
  more output than the cap settles above its reservation, so calls in flight together can
  overshoot the budget by the difference. The overshoot of one call is at most
  `(max_tokens - cap)` output tokens at the output price; the next call sees the real spend.
  Pick a cap above what the workload produces in a normal call.
- Budgets without the key keep the strict bound.
- Measured with Claude Code and a cap of 16,000 on the team's budget: one call in flight
  reserved 0.18 USD on Sonnet, 0.37 on Opus and 0.74 on Fable, down from 1.30, 2.61 and 5.22.
- The report page's policy table does not show the cap yet.
