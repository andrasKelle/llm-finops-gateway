# 0033. A price book alias is a name that is priced and governed as its target

Date: 2026-11-19
Status: Accepted

## Context

A platform team may want clients to call a role instead of a model: `sdd-plan` today routed
to one model, next quarter to another, without touching the clients. In the LiteLLM plugin
the name the client sends is the price book row, so every such name needed a copy of the
target's price row, kept in step by hand, and the model budgets and allow-lists saw the alias
as a model of its own (#85).

## Decision

- **A price book row may be `{ alias_of: <row> }` and nothing else.** The target must be a
  priced row of the same file; an alias of an alias and an alias with a price are errors at
  load time.
- **An alias is the target for every rule.** The call is priced at the target's prices,
  checked against the allow-lists as the target, and reserved and settled on the target's
  model budget. Naming an alias in an allow-list or in `model_budgets` has no effect.
- **The ledger row keeps both names:** `model` is the target, `cost_records.model_alias` the
  name the client called (nullable; revision `0005`). Reports and the FOCUS export group by
  the target, which is what the provider bills.
- **Who routes.** In the gateway the alias is resolved before the upstream call: the provider
  is called with the target and the response names it. In the LiteLLM plugin LiteLLM keeps
  routing the name the client sent, and the proxy's `model_name` must point at the same model
  the alias names in the price book.
- **`/v1/models` lists aliases** next to the priced rows.

## Consequences

- Changing what a role points at is one line in the price book (and, behind LiteLLM, one in
  its config), reviewed like any price change; clients do not change.
- An alias cannot be used to get around a model allow-list or a model budget.
- Behind LiteLLM the price book and the proxy config can disagree about an alias; the ledger
  then prices the call as the price book says. Keep the two in one pull request.
- The use case of a call still comes from the header or the key; an alias does not set it.
