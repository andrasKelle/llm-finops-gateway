# 0032. The cost summary splits by use case and by run on request

Date: 2026-11-19
Status: Accepted

## Context

The ledger stores the use case on every row and, since ADR-0030, the run id. The summary views
(`/v1/finops/summary`, its CSV and the chargeback table of `/finops`, ADR-0016) grouped by
team, project and model only, so "what did the plan phase cost" and "what did this feature
cost" needed SQL (#84).

## Decision

- **`group_by` adds dimensions, the default stays.** `/v1/finops/summary` and
  `/v1/finops/summary.csv` accept `group_by=use_case` and `group_by=run_id` (repeatable; any
  other value is `422`). Without it the rows and the CSV header are what they were. The JSON
  rows always carry `use_case` and `run_id`, `null` when not grouped by; the CSV gets the
  columns only when asked.
- **`run_id=<id>` limits the summary to one run,** on both endpoints and on the page.
- **The page's chargeback table is grouped by use case** and gains that column. A `Runs`
  section lists the runs in scope, most expensive first, each linking to the page filtered to
  that run. The budgets table gains a use case column and one row per use case budget
  (ADR-0031).
- **Scope is unchanged:** a team key sees its own team's rows and runs (ADR-0016).

## Consequences

- The cost of a phase and of a feature is one request, in the browser or in a BI tool.
- The chargeback table has more rows than before for projects with several use cases; the
  total and the per-team filter are unchanged.
- The run list is capped at the 50 most expensive runs; the API has no such cap.
- The summary is not limited to the current month, as before; a run that spans a month
  boundary is reported whole.
