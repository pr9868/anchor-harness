---
workflow: reconcile
version: 1
harness_version: 0.1
intent: reconcile a metric across two sources, defer to credibility, flag conflicts
tags: [reconcile, etl, data, sources]
triggers: ["reconcile the numbers", "reconcile sources", "run reconciliation"]
params:
  period: {type: string, required: true}
guardrails:
  - {text: "never overwrite raw source data", enforce: hard}
nodes:
  - id: pull_a
    autonomy: locked
    depends_on: []
    produces: pull_a.rows
    uses_sources: [snowflake_prod]
    verify: "row_count > 0"
  - id: pull_b
    autonomy: locked
    depends_on: []
    produces: pull_b.rows
    uses_sources: [finance_export]
    verify: "row_count > 0"
  - id: reconcile
    autonomy: guided
    consumes: [pull_a.rows, pull_b.rows]
    produces: reconcile.table
    uses_sources: [snowflake_prod, finance_export]
    merge: {key: [date, metric], strategy: precedence}
    verify: "row_count > 0"
    gate: {when: after, mode: approve, scheduled_ok: true}
---

## Node: pull_a
Pull the metric for `${params.period}` from the authoritative source.

## Node: pull_b
Pull the same metric for `${params.period}` from the secondary source.

## Node: reconcile
The helper keyed-joins both sources and applies precedence; review the flagged
conflicts before accepting the reconciled table.
