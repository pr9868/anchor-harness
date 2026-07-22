---
name: anchor-orchestrator
description: Runs Anchor workflows deterministically and teaches the Anchor harness. Use when the user asks to run, refresh, update, or execute a defined workflow (e.g. "refresh the dashboard", "run the weekly report"), when a request is repeatable and multi-step and should follow fixed rules, when setting up or co-authoring a workflow, or whenever the user mentions Anchor, the harness, orchestration, workflow gates, or a workflow file. Classifies each request into free / assisted / rails and drives the deterministic helper CLI so no step is skipped.
---

# Anchor — orchestrator

Anchor makes an agent execute a declared workflow **deterministically enough to trust**: follow a graph of steps, never silently skip one, prove each step, pause for the human where it matters. **You do the judgment work inside each node; the helper script decides order, readiness, and verification.** Never invent the sequencing yourself — always ask the helper.

Helper (run in a shell, from the project root):

```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/anchor-orchestrator/scripts/anchor.py" <command> [args]
```

Every helper command returns one JSON object with `"status": "ok"` or `"status": "error"`. **If it returns `error`, stop, show the user the message, and do not proceed** — a helper failure must never become a skipped step.

## Setting up a project (first time)

If the project has no `workflows/` folder yet, scaffold everything in one shot rather than copying files by hand:

```
... init --template dashboard-refresh    # or: --all  (copy every starter)
```

This creates `workflows/` with the chosen templates, writes `harness.config.yaml`, adds the ground-rules contract to `CLAUDE.md`, and builds the index. It's idempotent — safe to re-run to add more templates. Afterward, remind the user to set their sources / tiers / notify target in `harness.config.yaml`. Then proceed to dispatch.

## Step 1 — Dispatch every request (do this first)

Not everything should be orchestrated. Classify the request, state your call aloud, then route:

- **Rails** — matches a defined workflow → load it and follow the graph. Run `... reindex` then `... match "<the request>"`. If one workflow clearly matches, propose it: "This matches `dashboard-refresh` — run it?" Then follow Step 2.
- **Assisted** — novel but multi-step / high-stakes → draft a small DAG yourself, show the plan, then run it with the same discipline (verify + gates + audit). Offer to save it as a workflow afterward.
- **Free** — exploratory / one-off / open-ended → just do the work normally. Do **not** orchestrate. This is the default when nothing matches and the task isn't repeatable.

Rules:
- **State the lane** ("this looks open-ended, I'll handle it directly" / "this matches a workflow, I'll follow it").
- **Ask only when misclassifying is costly.** Cheap one-offs go Free without asking.
- **Same workflow, different inputs** (Q2 vs Q3, client A vs B) is **not** a new workflow — run the matched one with different `--param` values.
- Matching only ever *proposes*; never auto-run a workflow silently unless it's an explicitly scheduled task.

Details: `references/dispatcher.md`.

## Step 2 — Run a workflow (Rails / Assisted)

Drive the loop with the helper. Do not reorder or skip; let `ready` tell you what runs next.

1. **Validate** — `... validate <workflow>`. Abort on errors and show them.
2. **Echo-confirm** — tell the user the workflow, node count, and where gates are, then wait for go (skip only for scheduled runs).
3. **Start** — `... start <workflow> --scope full --param k=v ...`. Capture the `run_id`. Extract params from the request when possible ("refresh for Q2" → `--param quarter=Q2`); if a required param is missing, ask for it.
4. **Loop** until no nodes remain:
   - `... ready <run_id>` → gives `ready`, `blocked`, and `gates`. Nodes whose `when:` is false are auto-skipped.
   - For each ready node:
     - **before-gate?** If `gates[node].when == "before"` and blocking (`approve`/`choose`): present a compact summary + the one decision, wait, then `... gate <run_id> --node <id> --approve|--choose <opt>`. (On a scheduled run, gates with `scheduled_ok: true` auto-approve.)
     - **Do the node's work** at its `autonomy` (see below). Write the output to a file.
     - **Record** — `... record <run_id> --node <id> --output <path> --meta '{"row_count":N,"metrics":{...}}'`. Always include the metrics your `verify`/downstream `when:` needs.
     - **Verify** — `... verify <run_id> --node <id>`. If `pass:false`, apply the node's `on_fail`; if the node is `open`, escalate to the user instead of silently retrying. If it returns `delegate:subagent`, run a separate check of the stated criterion and only continue if it passes.
     - **after-gate?** If the node's gate is `when: after` and blocking, present the result for review and wait for `... gate ... --approve` before dependents run.
5. **End** — `... end <run_id>` (releases the lock, prunes old runs). Notify the user of the outcome.

Autonomy per node: **locked** = follow the node body verbatim; **guided** = that approach, minor judgment; **open** = design your own method (guardrails + verify still bind; escalate verify failures).

**Cost & audit controls:**
- **Model hint** — if `ready` returns a `models` entry for a node (`light`/`normal`/`deep`), do that node's work on a correspondingly cheaper or stronger model (delegate `light` mechanical nodes to a cheap subagent; reserve `deep` for `open` analysis).
- **Audit level** — `start` reports the run's `audit`. Under `light` or `none`, be terse: skip provenance narration, skip `subagent:` verifies unless the node is gated, and don't summarize the audit trail. Under `full`, keep the full record.
- **Budget** — if `ready` returns a `halt` field (budget exceeded), stop the run and tell the user; do not continue.

Inspect or recover anytime: `... status <run_id>` (frontier, blocked, quarantined, halt reason), `... list` (all runs), `... restore <run_id>` (roll outputs back to a prior run).

## Step 3 — Co-authoring & promotion

- **Repeatable task, no workflow yet** → co-author one with the user: propose nodes, dependencies, gates, verify, sources using the schema in `references/workflow-schema.md`, save it to `workflows/<name>.md`, then `... reindex`.
- **After a good Free/Assisted run** → offer: "That worked — want me to save it as a workflow so it's repeatable?"
- Copy a starter from `templates/` (dashboard-refresh, weekly-report, reconcile, monitor-alert, digest) as a starting point.

## Guardrails & honesty

- Honor node and workflow `guardrails`. Treat `enforce: hard` rules as never-break; in Cowork, pause for explicit approval before any action that could violate one.
- Sources authenticate through the user's connected tools — never store secrets in workflow or config files.
- Never claim a node passed without a successful `verify`. Never advance past an unapproved blocking gate.

For the full schema (params, `when:`, `map:`, gates, verify DSL, merge) read `references/workflow-schema.md`. For dispatch and matching read `references/dispatcher.md`.
