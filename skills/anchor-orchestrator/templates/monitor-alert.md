---
workflow: monitor-alert
version: 1
harness_version: 0.1
intent: check a metric on a schedule and alert only if it breaches a threshold
tags: [monitor, alert, threshold, schedule]
triggers: ["check the metric", "run the monitor", "monitor and alert"]
params:
  threshold: {type: string, default: "0"}
guardrails:
  - {text: "do not page unless the breach is confirmed", enforce: soft}
nodes:
  - id: check
    autonomy: locked
    depends_on: []
    produces: check.reading
    uses_sources: [snowflake_prod]
    verify: "exists(check.reading)"
  - id: alert
    when: "check.metrics.breaches > 0"
    autonomy: guided
    consumes: [check.reading]
    produces: alert.sent
    verify: "exists(alert.sent)"
    gate: {when: before, mode: notify, scheduled_ok: true}
---

## Node: check
Read the monitored metric. Emit `breaches` into metadata `metrics{}` (count of
values past the threshold).

## Node: alert
Only runs when `breaches > 0`. Send a concise alert to the on-call channel.
