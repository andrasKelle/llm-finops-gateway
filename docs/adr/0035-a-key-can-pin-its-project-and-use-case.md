# 0035. A LiteLLM key can pin its project and use case

Date: 2026-11-20
Status: Accepted

## Context

In the LiteLLM plugin the team comes from the key, so a client cannot claim another team. The
project and the use case come from `X-Project` and `X-Use-Case`. A key may carry both in its
metadata (`{"finops": {"project": ..., "use_case": ...}}`), but those are defaults: a header
that says something else wins (`docs/litellm.md`).

Rules per use case (ADR-0031) give each phase of a development cycle its own model list and
budget. With header-first attribution the phase is whatever the client's settings say. A
developer whose editor is set up for `implement` can change one line, send `X-Use-Case: plan`
and get the plan phase's model and budget. The team's allow-list and budgets still hold, so
this is no way out of the team, but inside the team the phase is a label and not a boundary
(#97). The settings of an editor on a developer's machine cannot be the control.

The gateway's own keys already know a limit of this kind: a key with `projects` may only be
used for those projects, and anything else is `403 project_not_allowed`.

## Decision

- **A key's `finops` metadata block may say `"pin": true`.** Then every value the key carries
  there is binding. A call whose `X-Project` or `X-Use-Case` names something else is refused
  with `403` before the registry, the allow-list, the rate limit and the budget are looked at,
  and before any provider call. Nothing is reserved and nothing is recorded.
- **Codes:** `project_not_allowed`, the code the gateway uses for a key limited to projects,
  and `use_case_not_allowed`. The message names the key, what it may be used for and what the
  call sent, in the `code: explanation` form of every plugin rejection (ADR-0034).
- **The pin binds only what the key carries.** A pinned key with a use case and no project
  leaves the project to the caller, and the other way round. A header that agrees with the
  key, or no header, is served as before.
- **Opt-in.** Without `pin`, or with any value other than a literal `true`, nothing changes:
  the key's values are defaults and the headers win. Deployments that rely on a default
  project with an override keep working.

Considered and not chosen: making the key's values binding whenever they are present. It needs
no new field, but it changes the documented behaviour of every key that has a default today,
and a default with an override is a legitimate use (one key for a tool that sometimes works on
another project).

## Consequences

- One key per phase, issued by the platform team, makes the phase a boundary: the client's
  settings then hold a URL and a model name, and editing them changes nothing that matters.
  A pipeline or a coding agent gets its phase from the key in its secret store.
- The pin is as strong as key issuance. Whoever may create keys for the team in LiteLLM can
  create one without the pin, so developers should receive keys, not the right to make them.
- A developer who needs several phases needs several keys. In VS Code that is one model group
  per key.
- The gateway's own keys (`keys.yaml`) can limit projects but not use cases. That is not part
  of this change; if it is wanted, the same code (`use_case_not_allowed`) fits.
- No schema revision, no change to the ledger row or the report.
