# 0025. Budgets per model, on a team or a registered project

Date: 2026-11-15
Status: Accepted

## Context

A budget is set per team (ADR-0005) and per registered project (ADR-0021). A model can only be
allowed or refused through `allowed_models`. When a team is allowed an expensive model for a
narrow purpose, there is no way to say "at most 50 USD a month on this model" short of locking
the model into a dedicated project, which bends the project dimension to a pricing concern.
Finance asks the question per model (the invoice is per meter, ADR-0018), and LiteLLM answers
it only per key (`model_max_budget`), not per team.

## Decision

- A team and a registered project may carry `model_budgets`: a map from model name to a
  budget block with the same keys as `budget` (`monthly_usd`, `warn_at`, `block_at`,
  `enforcement`, `reserve_output_tokens`). The team's map is inherited from `defaults` and
  replaced, not merged, by a team entry, like `allowed_models`.
- One model budget applies to a call: the project's entry for the model when the project
  declares one, else the team's. A project entry therefore replaces the team's entry for that
  project's calls, the way a project `allowed_models` replaces the team's list.
- The model budget is reserved after the team's and the project's, through the same atomic
  reservation (ADR-0007), settled with the ledger row and released on failure. When it does
  not fit, the earlier reservations are released and the call is rejected with
  `403 budget_exceeded` naming the model and its owner.
- Counter scopes: `team/model/<model>` for a team entry, `team/<project>/model/<model>` for a
  project entry, in the existing `budget_counters` table. The `team` column of that table is
  widened to 255 characters because model names are not length-limited and may contain `/`;
  project names cannot contain `/`, so the two forms cannot collide.
- The response carries the model budget's state as `x_finops.policy.model_budget` and the
  `X-FinOps-Model-Budget-*` headers, the rejection body as `model_budget`;
  `/v1/finops/budgets` lists `model_budgets` per team and per project. The HTML report page
  does not show them yet.
- The LiteLLM plugin (ADR-0019) inherits the rule through the shared policy engine.

## Consequences

- A per-model cap is a line in the policy file next to the model's allow-list entry, reviewed
  in the same pull request.
- Up to three reservations per call; each is one conditional row update in the same
  transaction scope as before.
- Existing PostgreSQL databases need `ALTER TABLE budget_counters ALTER COLUMN team TYPE
  VARCHAR(255)` before a scope key longer than 64 characters is written; `create_all` does
  not alter columns (see `docs/database.md`). SQLite does not enforce the width.
- Model budgets do not feed the webhook alerts or the `budget_used` metric, which stay per
  team.
