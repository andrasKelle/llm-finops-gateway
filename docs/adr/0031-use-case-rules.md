# 0031. A use case can carry a model list and a budget

Date: 2026-11-19
Status: Accepted

## Context

A project lists its use cases (ADR-0021) and a call outside the list is rejected, but a use
case had no rules of its own. Teams that run a development cycle in phases (specify, plan,
tasks, implement) want two things per phase: which model it may use, and what it may spend.
The phase reaches the gateway as the use case, from `X-Use-Case` or from the key metadata in
the LiteLLM plugin (#83).

## Decision

- **`use_cases` is a list of names or a mapping of names to rules.** The list keeps its
  meaning. In the mapping an entry may set `allowed_models` and `budget`; an empty entry only
  registers the name.
- **The model list narrows.** A call must pass the team's list, the project's and the use
  case's. A use case cannot allow a model its team or project does not. The rejection is
  `403 model_not_allowed`, the same code as for the other lists, with the use case named in
  the message.
- **The budget is a fourth reservation.** It is reserved after the team's, the project's and
  the model's under the counter scope `team/project/use_case/<use_case>`, settled with the
  ledger row in the same transaction and released on failure. All budgets of a call must fit;
  a block on one releases the others. `warn_at`, `block_at`, `enforcement` and
  `reserve_output_tokens_max` work as on every budget (ADR-0028: the smallest cap applies).
- **Its state is reported like the others:** `x_finops.policy.use_case_budget`, the
  `X-FinOps-Use-Case-Budget-*` headers, `use_case_budget` in the rejection body and
  `use_case_budgets` on a project in `/v1/finops/budgets`.

## Consequences

- "Plan with the large model, implement with the mid one" is a reviewable line in the policy
  file, enforced before the call.
- Rules per use case need a registered project; a team with free-text projects has none.
- A call can now touch four counters; the reservation stays one conditional update per
  counter, in a fixed order.
- The use case is caller-declared. A client that mislabels its phase gets that phase's rules;
  where this matters, bind the use case to the key (LiteLLM key metadata) instead of a header.
