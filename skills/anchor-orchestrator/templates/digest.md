---
workflow: digest
version: 1
harness_version: 0.1
intent: collect activity across channels and post a summarized digest
tags: [digest, summary, channels, daily]
triggers: ["daily digest", "run the digest", "summarize the channels"]
params:
  since: {type: string, default: "24h"}
guardrails:
  - {text: "attribute quotes to their source", enforce: soft}
nodes:
  - id: collect
    autonomy: guided
    depends_on: []
    produces: collect.messages
    verify: "row_count >= 0"
  - id: summarize
    autonomy: open
    consumes: [collect.messages]
    produces: summarize.digest
    verify: "exists(summarize.digest)"
    gate: {when: after, mode: approve, scheduled_ok: true}
  - id: post
    autonomy: guided
    consumes: [summarize.digest]
    produces: post.sent
    verify: "exists(post.sent)"
    gate: {when: before, mode: notify, scheduled_ok: true}
---

## Node: collect
Gather messages from the tracked channels since `${params.since}`.

## Node: summarize
Distill into a short digest — themes, decisions, and anything needing attention.

## Node: post
Post the approved digest to the destination channel.
