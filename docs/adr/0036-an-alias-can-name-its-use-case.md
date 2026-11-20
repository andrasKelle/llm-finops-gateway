# 0036. An alias can name the use case its calls belong to

Date: 2026-11-20
Status: Accepted

## Context

Rules per use case (ADR-0031) give each phase of a development cycle its model list and its
budget. The phase reaches the gateway as `X-Use-Case`, which the client's settings choose. A
pinned key (ADR-0035) makes the phase binding, but it needs one key per developer and phase:
3,000 keys for 1,000 developers and three phases. That fits pipelines and coding agents, which
are few and get their keys from a secret store. It does not fit people (#100).

A developer already chooses one thing in the editor on every call: the model. Aliases
(ADR-0033) let that choice be a role, `sdd-plan`, instead of a model. If the role also says
which phase it is, the developer has nothing else to set and nothing to edit.

## Decision

- **An alias row may name a use case:** `sdd-plan: { alias_of: claude-opus-5-5, use_case: plan }`.
  The name has the shape of `X-Use-Case`. `use_case` on a priced row is an error when the
  price book loads, so a rule that looks set is never silently ignored.
- **A call to such an alias belongs to that use case.** It is recorded and governed under it:
  the project's registry, the use case's model list and its budget apply as if the client had
  sent the header. The ledger row has `use_case`, `model` (the target) and `model_alias`.
- **A call that says another use case is refused** with `403 use_case_not_allowed` before the
  policy check and before any provider call: `model 'sdd-plan' is for use case 'plan', not
  'implement'`. In the gateway "says" means `X-Use-Case`; in the LiteLLM plugin it means the
  header or a pinned key. A header that agrees, or none, is served.
- **A key's unpinned default gives way.** A default is what the key says when nothing else
  does, and here the model name does.
- Gateway and plugin, every route of the plugin. No schema revision.

Considered and not chosen:

- Inferring the use case from the policy when exactly one use case of the project allows the
  model. It needs no new field, but it breaks down as soon as two phases share a model, and
  a change to one use case's model list would silently move calls of another.
- Naming the alias in the use case's entry in `policies.yaml`. It keeps prices and policy
  apart, but an alias then needs three places (LiteLLM config, price book, policy) to mean
  anything, and the same name could mean different phases in different projects.

## Consequences

- One key per developer is enough. The editor's model list is the list of phases, and what a
  developer can change there is the model name, which is what the rule is about.
- The phase is as binding as the names are complete. A project that should be callable only
  by phase lists use cases whose `allowed_models` cover exactly the targets of its aliases;
  a direct call to `claude-opus-5-5` with `X-Use-Case: plan` is then the same call as
  `sdd-plan` and no way around anything, and a call that names no use case is refused by the
  registry (ADR-0021).
- The price book now carries a label that is not a price. It already carries `provider` and
  `reasoning`, which are not prices either; all of them describe what a model name means.
- A use case named by an alias is the same in every team. Teams whose projects register use
  cases must register it; teams with free-text use cases get it as a label.
- In the LiteLLM plugin the alias still needs a `model_name` entry in LiteLLM's config, as in
  ADR-0033.
