# Anchor workflow schema

A workflow is one file: `workflows/<name>.md` — YAML front-matter (the graph the helper reads) + a Markdown body (per-node instructions you read).

## Front-matter

```yaml
workflow: dashboard-refresh        # unique name; used for locks, cache, outputs
version: 1                         # bump when you change the graph
harness_version: 0.1
intent: one-line purpose           # powers intent-matching
tags: [dashboard, metrics]         # powers matching
triggers: ["refresh the dashboard"]# natural-language phrases that select this workflow
params:                            # run inputs — one workflow, many runs
  quarter: {type: string, required: true}
  region:  {type: string, default: "all"}
guardrails:                        # apply to every node
  - {text: "write only under ./out", enforce: hard}   # hard = never break
  - {text: "cite every metric", enforce: soft}         # soft = strong guideline
nodes: [ ... ]                     # the DAG (see below)
```

## Node fields

```yaml
- id: analyze                      # unique within the workflow
  autonomy: open                   # locked | guided | open   (default guided)
  depends_on: [fetch_sf]           # ordering-only deps (no data). Omit → implicit prev.
  consumes: [reconcile.table]      # data inputs — these IMPLY dependencies
  produces: analyze.findings       # namespaced output(s)
  uses_sources: [snowflake_prod]   # for credibility/merge
  trust_override: {slack_reports: 1}
  merge: {key: [date, metric], strategy: precedence}   # combine sources; see Merge
  when: "reconcile.metrics.rows > 0"                    # conditional; see Conditionals
  map: {over: fetch.regions, as: region, max_parallel: 4}  # fan-out; see Map
  verify: "row_count > 0"          # required; DSL or 'subagent: <criterion>'
  freshness: {max_age: 24h, recompute_if_stale: true}
  on_fail: retry(2) then halt      # retry(n) then halt | halt | skip flagged | fallback:<id>
  guardrails: [{text: "...", enforce: hard}]
  gate:
    when: after                    # before = authorize · after = review
    mode: approve                  # notify (non-blocking) | approve | choose
    options: [slack, email, both]  # required iff mode == choose
    scheduled_ok: false            # true → auto-approve under scheduled runs
```

**Readiness rule:** a node's dependencies = `depends_on` ∪ producers of everything it `consumes`. It runs when all are done. `depends_on: []` = no dep (parallel-eligible). The graph must be acyclic (validation rejects cycles).

## Autonomy
- `locked` — follow the node body verbatim (data pulls, exact procedures).
- `guided` — that approach, minor judgment (default).
- `open` — design your own method; guardrails + verify still bind; verify failures escalate to the human.

## Gates (human-in-the-loop)
- `when: before` = authorize before the node runs. `when: after` = review its output before dependents use it.
- `mode`: `notify` never blocks (just informs); `approve`/`choose` block and quarantine until you approve via `helper gate`.
- `scheduled_ok: true` auto-approves under scheduled/headless runs; otherwise the run pauses and notifies.

## Verify DSL (mechanical, checked by the helper)
Operates over the node's metadata sidecar `{row_count, col_count, non_empty, schema_name, metrics{}}`.

```
expr  := term ((AND|OR) term)*
term  := field OP number | non_empty | exists(<output>) | schema(<name>)
field := row_count | col_count | metrics.<name> | <node>.metrics.<name> | params.<name>
OP    := > >= < <= == !=
```
Use `subagent: <criterion>` for judgment checks — a separate reviewer decides pass/fail.

## Run parameters
Declare typed `params`; supply at run (`--param quarter=Q2`) or let the dispatcher extract them from the request. Reference anywhere as `${params.quarter}`. Params are part of the cache key, so Q2 and Q3 cache independently. One workflow serves many runs — never fork a file to change an input.

## Conditionals (`when:`)
A node with a `when:` predicate (same DSL) runs only if it's true; otherwise it's skipped (`skipped:condition`). Nodes that consume a skipped node's output skip too (`skipped:upstream`) unless they declare a `fallback`. Use for "only alert if anomalies > 0".

## Map / fan-out
`map: {over: <list output>, as: item, max_parallel: N}` unrolls the node into one instance per item at run time — each verified/gated/cached independently — then collects results. `over` must resolve to a list. Keeps the graph acyclic (unroll-then-collect).

## Source merge
A node with `merge:` keyed-joins its consumed sources on `merge.key`. On disagreement, the higher-tier source wins (tiers from `harness.config.yaml`, overridable per node); each row records the winning source and flags conflicts. Same-tier disagreement raises a review flag rather than guessing.

## Run scope
`--scope full` (rerun all) · `resume` (continue from checkpoint) · `subset` (named nodes; upstream reused from cache unless stale).

## Audit level (token / ceremony control)
Set in `harness.config.yaml` (`run.audit`), or override per workflow (front-matter `audit:`) or per run (`--audit`). Default **full**.
- `full` — state + append-only record + output snapshots + provenance; subagent verifies allowed. Full trust, rollback, resume.
- `light` — keep `state.json` (resume works) + minimal record; mechanical verifies only, no snapshots. Moderate savings.
- `none` — no trail, no rollback, no resume; the run is purged at `end`. Fastest/cheapest for throwaway, low-stakes runs. Gates and verify still work.

Independent of gates/verify — you can run audit-off but still stop at gates. Open-ended (Free-lane) work already runs with no audit.

## Per-node model hint (cost control)
Optional `model: light | normal | deep` per node. `ready`/`plan` surface it; the agent runs that node on a cheaper or stronger model accordingly (e.g. `light` for a mechanical `locked` fetch, `deep` for an `open` analysis). Default `normal`.

## Run budget (runaway backstop)
Optional `limits: {max_nodes, max_minutes}` in config (or workflow front-matter). `ready` halts the run when either is exceeded and reports the reason — a safety net for unattended/scheduled runs.
