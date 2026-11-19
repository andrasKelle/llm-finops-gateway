# 0030. An optional run id ties the calls of one run together

Date: 2026-11-19
Status: Accepted

## Context

An agent run, a pipeline job or one feature of a spec-driven development cycle makes many
calls. The ledger has one row per call with team, project and use case, and nothing that says
which rows belong together, so "what did this feature cost, and in which phase" cannot be
answered from it (#61).

## Decision

- **`X-Run-Id` is an optional request header**, in the gateway and in the LiteLLM plugin. It is
  free text (a branch name, a pull request, a CI job id), bounded only in length: more than 128
  characters is `400`. Absent or empty means no run.
- **It is stored on the ledger row** in `cost_records.run_id` (nullable, indexed; revision
  `0004`), echoed as `x_finops.run_id` and as the `X-FinOps-Run-Id` response header when the
  call sent one, and exported in the FOCUS `Tags` column. Rows of different runs are not merged
  in the FOCUS export.
- **It is a label, not a scope.** No budget, allow-list or registry reads it, and the gateway
  does not check that a run id is unique or known.

## Consequences

- The cost of one run is a filter on one column; the use case splits it by phase.
- A client that cannot send headers (Copilot CLI, ADR-0029) has no run id. A key per run is
  possible but heavy; that case stays open.
- An existing database needs `ledger migrate` before 0.5.0 starts (ADR-0026).
- Run ids are caller-chosen text and are shown in reports: they are escaped there like every
  other label, and they should not carry personal data.
