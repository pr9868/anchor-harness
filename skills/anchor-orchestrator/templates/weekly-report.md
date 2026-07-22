---
workflow: weekly-report
version: 1
harness_version: 0.1
intent: compile and deliver a weekly status report from tracked activity
tags: [report, weekly, status]
triggers: ["weekly report", "generate the weekly report", "run the weekly report"]
params:
  week_of: {type: string, required: true}
  audience: {type: string, default: "team"}
guardrails:
  - {text: "no unverified claims in the report", enforce: soft}
nodes:
  - id: gather
    autonomy: guided
    depends_on: []
    produces: gather.items
    verify: "row_count > 0"
  - id: draft
    autonomy: open
    consumes: [gather.items]
    produces: draft.report
    verify: "exists(draft.report)"
    gate: {when: after, mode: approve, scheduled_ok: false}
  - id: deliver
    autonomy: guided
    consumes: [draft.report]
    produces: deliver.sent
    verify: "exists(deliver.sent)"
    gate: {when: before, mode: choose, options: [slack, email, both], scheduled_ok: false}
---

## Node: gather
Collect the week's activity for `${params.week_of}` (commits, tickets, decisions).
Emit `row_count` in metadata.

## Node: draft
Write the report for a `${params.audience}` audience. You own structure and tone.

## Node: deliver
Send the approved report via the chosen channel.
