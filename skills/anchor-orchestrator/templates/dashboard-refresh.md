---
workflow: dashboard-refresh
version: 1
harness_version: 0.1
intent: refresh the exec metrics dashboard from Snowflake + Slack, reconcile, analyze, publish
tags: [dashboard, metrics, snowflake, slack, refresh]
triggers: ["refresh the dashboard", "update the dashboard", "refresh dashboard"]
params:
  quarter: {type: string, required: true}
  region:  {type: string, default: "all"}
guardrails:
  - {text: "write only under ./data and ./out", enforce: hard}
  - {text: "every metric traces to a source query", enforce: soft}
nodes:
  - id: fetch_sf
    autonomy: locked
    depends_on: []
    produces: fetch_sf.rows
    uses_sources: [snowflake_prod]
    verify: "row_count > 0"
    on_fail: retry(3) then halt

  - id: fetch_slack
    autonomy: locked
    depends_on: []
    produces: fetch_slack.msgs
    uses_sources: [slack_reports]
    verify: "exists(fetch_slack.msgs)"

  - id: reconcile
    autonomy: guided
    consumes: [fetch_sf.rows, fetch_slack.msgs]
    produces: reconcile.table
    uses_sources: [snowflake_prod, slack_reports]
    merge: {key: [date, metric], strategy: precedence}
    verify: "row_count > 0"

  - id: analyze
    autonomy: open
    consumes: [reconcile.table]
    produces: analyze.findings
    verify: "subagent: each claim cites a source row"
    gate: {when: after, mode: approve, scheduled_ok: true}

  - id: alert
    when: "analyze.metrics.anomaly_count > 0"
    autonomy: guided
    consumes: [analyze.findings]
    produces: alert.sent
    verify: "exists(alert.sent)"
    gate: {when: before, mode: notify, scheduled_ok: true}

  - id: publish
    autonomy: guided
    consumes: [analyze.findings]
    produces: publish.dashboard
    verify: "exists(publish.dashboard)"
    gate: {when: before, mode: choose, options: [slack, email, both], scheduled_ok: false}
---

## Node: fetch_sf
Pull `${params.quarter}` metrics (last 90 days relative to run `as_of`) for region
`${params.region}` from the Snowflake `metrics` table. Emit a table with `row_count`
in the metadata sidecar.

## Node: fetch_slack
Collect the manually-reported figures posted in the #metrics Slack channel for the
same period. Zero messages is a valid result.

## Node: reconcile
Join the Snowflake rows and Slack figures on (date, metric). Where they disagree,
defer to source precedence (Snowflake outranks Slack). The helper performs the
keyed merge and records the winning source per row.

## Node: analyze
Find month-over-month anomalies and likely drivers. You own the method — cluster,
rank by impact, whatever fits. Emit `anomaly_count` into the metadata `metrics{}`.

## Node: alert
Only runs when `anomaly_count > 0`. Draft an alert summarizing the anomalies for the
team. Notify (non-blocking).

## Node: publish
Render the reconciled figures and findings to the dashboard. Ask which channel to
publish to (slack / email / both).
