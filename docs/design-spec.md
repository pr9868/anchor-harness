# Agent Workflow Harness — Design Spec

**Working codename:** Anchor · **Version:** v1.3 · **Date:** 2026-07-15 · **Status:** For build

> **Shipped status (plugin v0.3.1):** the built helper implements 14 commands — init, validate, reindex, match, plan, start, ready, verify, record, gate, status, list, restore, end. It enforces frontier-only recording, verify-before-done state, and blocking gates with quarantined output. The `merge`/`expand` helper commands and full durable-resume below are **design intent**; in the shipped build the **agent** performs source merge, map iteration, and `on_fail` handling as node work. This spec is the design of record; see the learning pack `05-maintenance-and-updates.md` for what shipped.

> Changelog v1.2→v1.3: added the reusability/control-flow axis — **run parameters** (§4.4, one workflow many runs), **conditional `when:` nodes** (§4.5), **map/fan-out nodes** for dynamic iteration (§4.6), **status/list inspection** (§16), and a **starter template library** (§23). Simplified: dispatcher matching drops fork-variant + confidence tiers (params absorb variants) and adds an index collision check (§0.4); source merge simplified to per-row precedence + conflict flag, per-cell provenance deferred (§9); cache key includes run params (§11.1).
> Changelog v1.1→v1.2: added §0 The Dispatcher (front door) — not everything is orchestrated; requests route into Free / Assisted / Rails lanes with a bounded ask, plus workflow intent-matching against an auto-generated index so existing YAML is reused as the library grows, and a co-author-once / promote lifecycle. Sits in front of the execution loop; downstream engine unchanged.
> Changelog v1.0→v1.1: fixed 4 contradictions; specified 7 execution mechanics (headless gates, concurrency, verify DSL, helper interface, cache, source auth, gate capture/resume); added the deterministic merge model; deferred git per-node commit and auto-migration; added the idempotency/as-of caveat.

---

## 1. Purpose

A reusable harness that makes an agent's execution of a defined task **deterministic enough to trust**: it follows a declared graph of steps, never silently skips one, proves each step happened, pauses for the human where it matters, resolves disagreeing data sources by declared credibility, and leaves a full audit trail — while allowing bounded autonomy inside specific steps. Shipped as a **drop-in, versioned plugin** so any project's agent learns the protocol and follows it as ground rules.

### Design goals
Deterministic sequencing with bounded per-node autonomy · no silent skips · traceable & auditable by default (no manual step) · simple substrate (flat files; no server, DB, or graph engine) · portable across Cowork and Claude Code with graceful enforcement degradation · OSMO-grade declarative-DAG ergonomics without the multi-tenant infra.

### Non-goals
Not a distributed/multi-tenant scheduler · not high-throughput · not a general runtime (node *work* = agent; graph *traversal* = code).

---

## 0. The Dispatcher — front door (runs before any execution)

Not every request should be orchestrated. Forcing open-ended work through a fixed graph wastes the agent; forcing repeatable work to be re-planned each time is how steps get skipped. The **dispatcher runs first** and routes each request into one of three lanes. The lane is a **transparent decision** — the agent states its call and the user can override or pin — never a silently imposed default.

### 0.1 The three lanes
- **Rails** — request matches a defined workflow → load its YAML and follow the graph. Full determinism; autonomy only *inside* nodes.
- **Assisted** — novel but multi-step / high-stakes → the **agent builds a DAG on the fly**, optionally shows it for approval, then executes it with harness discipline (verify, gates, audit) on the agent-authored plan.
- **Free** — exploratory / one-off / open-ended → the agent plans and acts normally; the harness stays out of the way (optional light audit only).

### 0.2 Classification signals
Leans **repeatable/orchestrated** when: it will recur · correctness + traceability matter · it hits the same sources/outputs · it maps to a named recurring action ("refresh," "weekly report"). Leans **free** when: exploratory, one-off, or open-ended. The agent **states its classification aloud** ("looks repeatable → I'll use rails" / "looks open-ended → I'll plan it myself") so the user always knows the active mode — transparency is a trust feature.

### 0.3 Bounded ask
Ask a **single** dispatcher question **only when misclassifying is costly** (wasted work or skipped rigor). Cheap one-offs proceed Free without asking. Reserve questions for genuine, costly forks — an over-asking harness defeats its own purpose.

### 0.4 Workflow discovery & intent matching (scales as the library grows)
As repeatable tasks accumulate, the dispatcher checks whether a **new request already has a matching workflow** before treating it as new — so more workflows means better upfront clues, not more confusion.

- **Index.** The helper maintains `workflows/index.yaml`, auto-regenerated from each workflow's front-matter whenever a workflow is added or changed. Each entry: `{name, intent, triggers, tags, uses_sources, produces}`. Cheap to scan — front-matter only, never full bodies.
- **New front-matter fields** to power matching: `intent:` (one-line purpose) and `tags: [...]`.
- **Two-stage match (cheap → smart):** (1) *Deterministic filter (helper):* match the request against each workflow's `triggers` phrases + `tags`/keywords → candidate set, no model needed. (2) *Semantic rank (agent):* read the candidates' `intent` lines and pick the best fit.
- **Match → action:**
  - *One clear match* → propose + echo-confirm ("this matches `dashboard-refresh` — follow it?"). Never auto-runs silently unless the user pinned auto-run (e.g. a scheduled task).
  - *Several plausible / ambiguous* → a simple **"did you mean X or Y?"** disambiguation.
  - *Same workflow, different inputs* (Q2 vs Q3, client A vs B) → **not a new workflow**: run the matched workflow with different **run parameters** (§4.4). Parameters, not forked files, absorb variants — the library stays small.
  - *No match* → treat as new (Free, or co-author if repeatable).
- **Collision check:** when the index is (re)built, the helper **warns on duplicate workflow names or overlapping triggers**, so ambiguity is caught at authoring time, not mid-request.
- **Guardrail:** matching **proposes, never executes silently**; the user always sees which workflow matched and can decline.

### 0.5 Lifecycle — co-author once, follow forever, promote after the fact
- **First** encounter of a repeatable task with no match → the agent **co-authors** the YAML with the user (nodes, gates, verify, sources) using the schema, saves it to `workflows/`, and indexes it.
- **Every** later run → load and follow (Rails).
- **Promotion:** any Free or Assisted run can be captured afterward — "that worked; save it as a workflow so it's repeatable?" — graduating into Rails next time. Structure is created only when it's proven useful, never preemptively.

```
request
  → classify (§0.2) + match against workflows/index.yaml (§0.4)
       ├─ clearly free            → Free: agent builds its own plan, executes
       ├─ matches a workflow      → Rails: load YAML, echo-confirm, follow
       ├─ repeatable, no match    → co-author YAML with user, save+index, run
       ├─ novel + high-stakes     → Assisted: agent drafts DAG, run with discipline
       └─ in doubt AND costly     → ask one question → branch above
```

---

## 2. Mental model & layers

A **workflow** is a **free DAG** of **nodes**; the node is the atom. The **engine** is the **orchestrator skill** (agent protocol) + a small **deterministic helper script** (graph math). State is a per-run checkpoint; runs are resumable, previewable, restorable.

| Layer | What | Backed by |
|---|---|---|
| Definition | workflow (nodes + edges) | hybrid YAML + Markdown file |
| State | live run checkpoint | `runs/<id>/state.json` |
| Engine | traversal + execution | helper script (code) + orchestrator skill (agent) |
| Config | per-project settings | `harness.config.yaml` |
| Cache | reusable node outputs | `.cache/<workflow>/<node>/<hash>/` |
| Outputs | current working outputs | `out/<workflow>/` |
| Package | the reusable harness | versioned plugin |

---

## 3. Determinism model

Split deterministic work from judgment work:
- **Code (helper), deterministic:** DAG validation, topological readiness, dependency/freshness evaluation, mechanical `verify`, keyed source merge + precedence, state/record I/O, cache keys, retention, locking.
- **Agent (orchestrator), judgment:** node work at declared autonomy; `subagent:` verify; gate summaries.

Protected by: **namespaced outputs** (no merge-key races), **idempotent nodes**, **externalized state** (re-anchor from `state.json`).

**Reproducibility caveat (as-of stamping).** Nodes with relative time windows ("last 90 days") are not naturally idempotent. At run start the engine captures a single `as_of` timestamp into `state.json`; all nodes resolve relative windows against `as_of`, so a run (and any `resume`/re-run *within* it) is reproducible. A fresh `full` run takes a new `as_of` — cross-run results legitimately differ, and `record.json` stores `as_of` so any output is interpretable.

---

## 4. Node schema

```yaml
- id: analyze                     # unique within workflow
  goal: find MoM anomalies and likely drivers
  autonomy: guided                # locked | guided | open   (default: guided)
  depends_on: [fetch_sf]          # ordering-only deps (no data). See §4.3
  consumes: [fetch_sf.rows, fetch_slack.msgs]   # data inputs → imply deps
  produces: analyze.findings      # namespaced output(s)
  uses_sources: [snowflake_prod, slack_reports]
  trust_override: {slack_reports: 1}            # optional per-node tier override
  merge: {key: [date], strategy: precedence}    # only if combining sources; §9
  guardrails:                     # per-node, additive to workflow guardrails; §7
    - {text: "never write outside out/", enforce: hard}
  verify: "row_count > 0 AND non_empty"          # DSL (§8) or 'subagent: <criterion>'
  freshness: {max_age: 24h, recompute_if_stale: true}
  on_fail: retry(2) then halt     # retry(n) then halt | halt | skip flagged | fallback:<id>
  gate:
    when: after                   # before = authorize · after = review
    mode: approve                 # notify | approve | choose
    options: [slack, email, both] # required iff mode == choose
    scheduled_ok: false           # true → auto-approve under scheduled runs (§14)
```

### 4.1 Autonomy (freedom *inside* a node)
`locked` (verbatim) · `guided` (this approach, minor judgment — **default**) · `open` (agent designs method; guardrails + verify still bind). No resource/step cap in v1; gates + verify backstop runaway (revisit if observed).

### 4.2 Gates (node-level human-in-the-loop)
- **`when: before`** = authorize: node won't start until approved.
- **`when: after`** = review: node runs; output is **quarantined** and consuming branches halt until approved.
- **`mode` governs blocking:** `notify` is **non-blocking** (inform only — no quarantine, flow continues) · `approve` **blocks** (stop, wait) · `choose` **blocks** (stop, pick from `options`). So `when: after` + `mode: notify` = the node runs, you're informed of the result, and dependents proceed without quarantine; only `approve`/`choose` quarantine and halt.
- **`options`**: required when `mode: choose`; the closed choice set (so the helper can present and record it — not buried in prose).
- **`scheduled_ok`**: under a scheduled/headless run, gates with `scheduled_ok: true` **auto-approve** (recorded as `auto_approved: scheduled` in `record.json`); all others **halt + notify**, leaving a resumable checkpoint.
- **Halt propagation:** an unapproved gate blocks only branches consuming its output; independent branches continue.
- **Default** when `gate` omitted: `auto`.
- **Quarantine location:** `runs/<id>/quarantine/<node>/`; on approval the helper promotes it to the node's normal output location and writes the decision to `record.json`.
- **Approval capture / resume:** approval is an explicit action — `helper gate --run <id> --node <id> --approve` (or `--choose <option>` / `--reject`). The helper records approver + timestamp + choice, promotes quarantine, and returns the new frontier. A halted run resumes via a fresh invocation with `--scope resume`.

### 4.3 Edge authority (readiness rule)
**Effective dependencies of a node = `depends_on` ∪ { producer(x) for each x in `consumes` }.** A node is *ready* when all effective deps are `done`. `consumes` is the data truth; `depends_on` adds ordering-only edges that carry no data. If **both** are omitted, the node implicitly depends on the previously listed node (implicit-sequential default). `depends_on: []` explicitly means "no ordering dep" → eligible to run in parallel. Validation (§12) rejects any `consumes` whose producer is missing and any redundant/conflicting edge.

### 4.4 Run parameters (one workflow, many runs)
A workflow declares typed `params` in its front-matter; a run supplies values, and nodes reference them as `${params.<name>}`. This is what makes a workflow **reusable rather than copyable** — "refresh for Q2 vs Q3" or "client A vs B" is the *same file, different params*, not a forked variant.

```yaml
params:
  quarter: {type: string, required: true}
  region:  {type: string, default: "all"}
```

- Supplied at invocation: `helper start dashboard-refresh --param quarter=Q2 --param region=EMEA`, or captured by the dispatcher from the request ("refresh the dashboard for Q2" → `quarter=Q2`).
- Referenced in a node's body, its `verify`/`when:`, or source queries as `${params.quarter}`.
- Recorded in `state.json` + `record.json` and part of the **cache key** (§11.1), so Q2 and Q3 runs cache independently.
- Validation (§12) rejects a `${params.x}` with no declared `x`, and a run missing a `required` param.

### 4.5 Conditional execution (`when:`)
A node may carry a `when:` predicate — the same DSL as `verify` (§8), over upstream metrics and `params`. If it evaluates false, the node is **skipped** (`status: skipped:condition`, recorded), not failed.

```yaml
- id: alert
  when: "analyze.metrics.anomaly_count > 0"   # only alert if anomalies exist
  consumes: [analyze.findings]
  produces: alert.sent
```

**Skip cascade:** a node consuming a condition-skipped node's output is itself skipped (`skipped:upstream`) unless it declares a `fallback`. This makes "only do X if Y" first-class control flow instead of abusing failure semantics.

### 4.6 Map / fan-out node (bounded dynamic iteration)
For "do X for each item in a runtime-determined collection," a node declares `map:` over a list produced upstream (or a param). At run time the helper **unrolls** it into one instance per item — each with its own verify, gate, provenance, and cache entry — runs them with bounded parallelism, and collects results into a namespaced list output. The graph stays acyclic (unroll-then-collect, never a loop).

```yaml
- id: region_analysis
  map: {over: fetch.regions, as: region, max_parallel: 4}
  consumes: [fetch.rows]
  produces: region_analysis.results    # aggregated list
  verify: "row_count > 0"               # applied per item
```

`over` must resolve to a list (validated). Per-item failures follow the node's `on_fail`; a gate on a map node fires per item unless `gate.collapse: true` batches them into one approval.

---

## 5. Workflow definition (hybrid YAML + Markdown)

```markdown
---
workflow: dashboard-refresh
version: 3
harness_version: 1.3
intent: refresh the exec metrics dashboard from Snowflake + Slack
tags: [dashboard, metrics, snowflake, slack]
triggers: ["refresh the dashboard", "update dashboard X"]
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
    depends_on: []                 # parallel to fetch_sf
    produces: fetch_slack.msgs
    uses_sources: [slack_reports]
    verify: "exists(fetch_slack.msgs)"
  - id: reconcile
    autonomy: guided
    consumes: [fetch_sf.rows, fetch_slack.msgs]
    produces: reconcile.table
    uses_sources: [snowflake_prod, slack_reports]
    merge: {key: [date, metric], strategy: precedence}
    verify: "row_count > 0 AND schema(reconciled_v1)"
  - id: analyze
    autonomy: open
    consumes: [reconcile.table]
    produces: analyze.findings
    verify: "subagent: each claim cites a source row"
    gate: {when: after, mode: approve, scheduled_ok: true}
  - id: publish
    autonomy: guided
    consumes: [analyze.findings]
    produces: publish.dashboard
    verify: "exists(publish.dashboard)"        # every node has a verify
    gate: {when: before, mode: choose, options: [slack, email, both], scheduled_ok: false}
---

## Node: fetch_sf
Pull `${params.quarter}` data (last 90 days relative to run `as_of`) for region `${params.region}` from Snowflake table `x`...

## Node: reconcile
Join Snowflake and Slack figures on (date, metric); on disagreement defer to precedence...

## Node: analyze
Find MoM anomalies. You own the method...

## Node: publish
Render to the dashboard. Ask which channel (options above).
```

---

## 6. Run scope & freshness

Scope: **`full`** (ignore checkpoint, run all) · **`resume`** (continue from last good checkpoint) · **`subset: [ids]`** (run only these).

Out-of-scope upstream nodes reuse **cached outputs** (§11.1) unless `freshness` marks them stale (`max_age` exceeded). If stale and `recompute_if_stale: true`, the helper expands scope to the minimal upstream set needed; otherwise it halts and reports the stale dependency rather than using it silently.

---

## 7. Guardrails (two tiers, per-node + global, declare-and-degrade)

- **Global:** workflow-level `guardrails` (§5). **Per-node:** node-level `guardrails` (§4), additive.
- **Soft** (`enforce: soft`): flexible rules the agent should follow.
- **Hard** (`enforce: hard`): must not be violated; reserve for irreversible/dangerous actions.
- **Enforcement, honored per runtime, degrading loudly:**
  - **Claude Code (CLI):** hard → pre-tool **hook** blocks the action (deterministic).
  - **Cowork:** hard → strong prompt rule **+ a forced `approve` gate** before any matching action (human backstop).
- Robustness never depends on environment; worst case a hard rule becomes a mandatory checkpoint.

---

## 8. Verification & the verify DSL

Every node has a `verify`. Two forms:

**Mechanical (closed DSL, evaluated by the helper).** Operates only over the node's **output metadata** — no arbitrary code, fully deterministic.

- Output metadata sidecar, written by the agent when it records a node output (`runs/<id>/meta/<node>.json`): `{path, kind, row_count, col_count, columns[], non_empty, checksum, schema_name, as_of, metrics{}}`. `metrics{}` holds any node-emitted numbers.
- Grammar:
  ```
  expr   := term (("AND" | "OR") term)*
  term   := field OP number
          | "non_empty"
          | "exists(" outputref ")"
          | "schema(" name ")"          # matches declared schema
  field  := row_count | col_count | metrics.<name> | <node>.metrics.<name> | params.<name>
  OP     := > | >= | < | <= | == | !=
  ```
- Fields resolve against the recording node's metadata; `exists()` and `schema()` let a node assert on its produced outputs. Unresolvable field/ref → verify **error** (treated as fail, surfaced).
- **The same grammar powers node `when:` predicates (§4.5)**, which reference upstream `<node>.metrics.<name>` and `params.<name>` to gate whether a node runs at all.

**Judgment (`subagent: <criterion>`).** Spawns a separate reviewer context (not the one that did the work) returning `{pass, reason}`. Use for high-stakes/qualitative checks.

**Verify-fail policy:** `locked`/`guided` node → apply `on_fail`. **`open` node → escalate to human** (agent improvised; a human should look), never silent auto-retry.

---

## 9. Source precedence & deterministic merge

Sources are registered globally (tier = credibility) with optional per-node override; disagreements resolve by a **defined keyed-merge model** so the helper stays deterministic.

```yaml
# harness.config.yaml
sources:
  snowflake_prod: {tier: 1, key: [date, metric]}   # authoritative
  finance_export: {tier: 2, key: [date, metric]}
  slack_reports:  {tier: 3, key: [date, metric]}
conflict_policy: prefer_higher_tier    # top-level. else: flag_for_review
```

**Merge model (required inputs: tabular outputs with declared keys).** A node with a `merge:` block performs a keyed outer join of its consumed source outputs on `merge.key`. For each joined **row**:
1. Sources agree → use the value.
2. Sources disagree → the higher **effective tier** wins (global tier, overridden by node `trust_override`); the row is tagged with the winning source and a `conflict: true` flag so disagreements stay visible.
3. Disagreement **within the same tier** → unresolvable by `prefer_higher_tier` → raise `flag_for_review` (a review gate/flag), never a silent pick.

The merged output records the **winning source per row** and flags every conflict → you can see which rows disagreed and who won. (Per-*cell* winner tracking is deferred; per-row + conflict flag covers the trust need at far less complexity.) Non-tabular or keyless sources are **not** auto-merged — the helper falls back to whole-output precedence-pick by tier and records the choice. `conflict_policy: flag_for_review` turns every unresolved conflict into a gate.

**Credentials.** The registry stores only source *metadata* (tier, key, schema) — **never secrets**. Sources authenticate through the runtime's existing connectors (Cowork/MCP connectors, or CLI environment). The harness never stores or handles credentials.

---

## 10. Failure & retry

`retry(n) then halt` (exponential backoff; retry the *node*, never restart the run) · `halt` (default) · `skip flagged` (skip optional node, record a visible warning) · `fallback: <id>` (route to alternate). Halted/failed runs resume from checkpoint.

---

## 11. State, cache, history, rollback, traceability

- **Checkpoint** `runs/<id>/state.json`: node statuses, output refs, frontier, scope, `as_of`, workflow+harness version. Written after each node.
- **Current outputs** live in a single **`out/<workflow>/`** tree (the one "live" location). Each run also snapshots them into `runs/<id>/out/`.
- **History (default = files, automatic, no manual step):** each run writes `runs/<id>/` = `state.json` + append-only `record.json` (per node: inputs, outputs, run params, per-row source-winner + conflict flags on merges, verify result, gate decision + approver + timestamp, autonomy, `as_of`, versions) + `out/` snapshot. Retention prunes to `retain: N` runs.
- **Rollback:** `helper restore --run <id>` copies that run's `out/` snapshot back to `out/<workflow>/`. No git needed.
- **Git (optional, OFF by default):** `history.backend: git` enables diffable history/remote backup; **per-node auto-commit is deferred** — v1.1 git mode commits once per run. Default remains `files`.
- **Traceability:** any output back-chains through `consumes`→`produces` edges and `record.json` to originating sources, the producing node/version, and `as_of`.

### 11.1 Cache identity
A node's cacheable output is keyed by `hash(workflow_id, node_id, node_version, resolved_inputs, run_params, as_of)`, stored at `.cache/<workflow>/<node>/<hash>/` with its metadata sidecar. Including `run_params` means a Q2 run and a Q3 run cache independently; keying on the run's `as_of` gives per-run caching (reuse within a run and its `resume`, recompute on a fresh `full`). `subset`/`resume` reuse the newest cache entry whose key matches **and** passes `freshness`; otherwise the node recomputes. This is the concrete definition of "cached output." (Coarser time-bucketing to share cache *across* runs is deferred — add only if reuse is observed.)

---

## 12. DAG validation (mandatory pre-run)

Auto-runs before every run; standalone via `helper validate`. Refuses to run and names the offending node/edge on: a **cycle**; a `consumes` with no matching `produces`; a `depends_on` to a missing node; an unreachable/orphan node; a `gate.mode: choose` without `options`; a `merge` whose sources lack declared `key`; an unresolved source/schema reference; a `${params.x}` referencing an undeclared param, or a run missing a `required` param; a `map.over` that doesn't resolve to a list; or a `when:`/`verify` field that can't resolve.

---

## 13. Dry-run / plan preview

`helper plan` executes nothing and reports: resolved topological order; nodes that will **run** vs **reuse cache** (given scope + freshness); gates that will fire (and which auto-approve under scheduling); outputs written/overwritten; each node's sources with precedence applied; and any validation warnings. `run.dry_run_default: true` forces it before execution.

---

## 14. Scheduling & notifications

- **Scheduling:** a scheduled task invokes a workflow with an explicit **run scope** (e.g. nightly `full`, hourly `subset`) and a **`scheduled` run-context flag**. Under that flag, gates with `scheduled_ok: true` auto-approve; all others halt + notify with a resumable checkpoint (so an unattended run never deadlocks — it pauses and pings you).
- **Notifications:** on `failure`/`completion`/`gate-halt`, push a summary to `notify.to` (`slack:#…` or email). This is the required half of scheduling — without it a paused/failed scheduled run is invisible.
- **Portability:** Cowork uses its scheduled-tasks mechanism; CLI uses cron/launchd invoking the same helper. Same workflow file, same scope semantics.

---

## 15. Execution loop

1. **Dispatch (§0)** → classify the request and match it against `workflows/index.yaml`; route to Free / Assisted / Rails. The steps below run for **Rails** (a matched workflow) and **Assisted** (an agent-authored DAG); **Free** requests skip the loop entirely. For Rails, **echo-confirm** the matched workflow (skip echo only under `scheduled`).
2. **Acquire lock** `runs/.lock/<workflow>.lock` (§16). If held → fail fast: "workflow already running (run <id>)."
3. **Validate** DAG (§12). Abort on error.
4. **Init/load** `state.json` per scope; resolve & record `params`; stamp `as_of` on new runs.
5. **Loop** while runnable nodes remain:
   a. `helper ready <run>` → ready set + blocked map (nodes whose `when:` is false are skipped, cascading to their consumers; §4.5).
   b. If a ready node has `map:` → `helper expand` unrolls it into per-item instances (§4.6).
   c. Per ready node: if `gate.when == before` unapproved → (auto-approve if `scheduled` + `scheduled_ok`, else halt+notify); else **execute** at autonomy, writing output + metadata sidecar.
   d. `helper verify` → on fail apply `on_fail` (open-node fail → escalate).
   e. If `merge:` present → `helper merge` (keyed join + precedence + per-row provenance).
   f. If `gate.when == after` → quarantine + (auto-approve if eligible, else halt+notify) before dependents run.
   g. `helper record` → writes state + record, returns next frontier.
6. **Release lock**, **notify** on completion/failure/halt.
7. Resumable via `--scope resume`; restorable via `helper restore`.

**Helper failure path:** every `helper` command returns JSON `{status: ok|error, ...}` and a matching exit code. On `error` (bad YAML, parse/IO exception, unresolvable verify) the agent **halts the run, surfaces the structured message to the user, and does not proceed** — a helper crash never becomes a silent skip.

---

## 16. Helper interface (CLI contract)

Stateless Python CLI; JSON out; project root inferred or passed. Core commands:

| Command | Purpose | Returns |
|---|---|---|
| `validate <wf>` | lint DAG | `{status, errors[]}` |
| `plan <wf> --scope …` | dry-run preview | `{status, order[], run[], cached[], gates[], writes[]}` |
| `start <wf> --scope … [--param k=v]` | lock + init run, resolve params, stamp as_of | `{status, run_id}` |
| `ready <run>` | compute frontier | `{status, ready[], blocked{}}` |
| `verify <run> --node <id> [--result pass\|fail]` | mechanical or delegated judgment verify | `{status, pass, state}` |
| `merge <run> --node <id>` | keyed merge + precedence | `{status, output, provenance}` |
| `record <run> --node <id> --output <path> --meta <json>` | persist node result | `{status, next_frontier[]}` |
| `gate <run> --node <id> --approve\|--reject\|--choose <opt>` | capture decision, promote quarantine | `{status, next_frontier[]}` |
| `restore --run <id>` | rollback outputs | `{status}` |
| `end <run>` | release lock, finalize record | `{status}` |
| `expand <run> --node <id>` | unroll a `map:` node into per-item instances | `{status, instances[]}` |
| `status <run>` | human view: frontier, blocked, quarantined, halt reason | `{status, view}` |
| `list` | list runs and their states | `{status, runs[]}` |
| `reindex` | rebuild `workflows/index.yaml`; warn on name/trigger collisions | `{status, warnings[]}` |

**Concurrency:** `start` acquires the per-workflow lock; a second `start` fails fast. `out/<workflow>/` is the single mutable output tree, guarded by the lock; per-run snapshots isolate history.

---

## 17. Configuration (`harness.config.yaml`)

```yaml
harness_version: 1.3
history:
  backend: files        # default. git = opt-in per project (commit-per-run only)
  retain: 20
run:
  validate_before_run: true
  dry_run_default: false
sources:
  snowflake_prod: {tier: 1, key: [date, metric]}
  slack_reports:  {tier: 3, key: [date, metric]}
conflict_policy: prefer_higher_tier
notify:
  on: [failure, completion, gate-halt]
  to: slack:#my-runs
```

---

## 18. Packaging, versioning & upgrade

- **Distribution:** one **plugin** bundling the orchestrator skill, the helper script, the CLAUDE.md contract snippet, and the `harness.config.yaml` template. Dropping it into a project's Cowork folder is the whole install; the agent then reads/extends that project's own workflow files.
- **Single source of truth:** engine + protocol + skill live **only in the plugin**, semver'd → upgrading the plugin upgrades every project (the propagation mechanism).
- **Two version axes:** harness version (plugin) vs each workflow `version`; workflow files stamp the `harness_version` they target.
- **Migration = warn-only (v1.1):** on run, the helper checks `harness_version` compatibility and **warns** on mismatch with migration notes; it does **not** auto-rewrite user DAGs. A changelog ships with the plugin.
- **Project-local vs shared:** `workflows/`, `harness.config.yaml`, `runs/`, `.cache/`, `out/` are project-local; engine/skill are shared via the plugin.

---

## 19. Directory layout (per project)

```
<project>/
  harness.config.yaml
  workflows/
    index.yaml           # auto-generated: name, intent, triggers, tags (§0.4)
    dashboard-refresh.md
  runs/
    .lock/dashboard-refresh.lock
    2026-07-14T09-00/{state.json, record.json, meta/, quarantine/, out/}
  .cache/dashboard-refresh/<node>/<hash>/
  out/dashboard-refresh/
  CLAUDE.md            # includes the harness contract snippet
```

---

## 20. Orchestrator contract (CLAUDE.md snippet, in spirit)

> When I ask to *refresh/update/run* a workflow, load `workflows/<name>.md`, run `helper validate`, echo-confirm the plan (unless scheduled), then execute node-by-node via the helper: honor effective dependencies (§4.3), run each node at its `autonomy`, write an output + metadata sidecar, run `verify` before advancing, apply `on_fail` (escalate open-node failures), perform declared merges with source precedence, and stop at every gate (auto-approving only `scheduled_ok` gates under scheduled runs). Never skip a node, never pass a failed verify or an unapproved gate. If any `helper` command returns `status:error`, halt and show me the message. Default history is file snapshots; use git only if this project enables it.

---

## 21. Requirements traceability

| Requirement | Section |
|---|---|
| Free vs orchestrated routing (not everything is fixed) | §0 |
| Reuse existing workflow by intent as library grows | §0.4 |
| Co-author-once / promote lifecycle | §0.5 |
| Deterministic no-skip sequencing | §3, §15, §20 |
| User-controlled per-project graph | §2, §5 |
| Free-DAG engine, node-level gates | §2, §4.2 |
| Bounded autonomy per step + HITL | §4.1, §4.2 |
| Guardrails global + per-node, soft + hard | §7 |
| Schedules (with headless-gate policy) | §14, §4.2 |
| Full or subset(node) refresh + freshness | §6, §11.1 |
| Outcome traceability | §8, §9, §11 |
| Auto auditability, no manual git | §11 |
| Source precedence/credibility + merge | §9 |
| Skill any project learns as ground rules | §18, §20 |
| Drop-in package that evolves | §18, §19 |
| Versioned harness, easy upgrade | §18 |
| DAG validation | §12 |
| Dry-run / plan preview | §13 |
| Git optional, default off | §11, §17 |
| Concurrency safety | §15, §16 |
| Credentials handling | §9 |
| Verify expression semantics | §8 |
| Helper↔agent interface + failure surfacing | §15, §16 |
| Cache definition | §11.1 |
| Run parameters (one workflow, many runs) | §4.4 |
| Conditional execution | §4.5 |
| Dynamic map / fan-out | §4.6 |
| Run inspection (status / list) | §16 |
| Starter workflow templates | §23 |

---

## 22. Resolved decisions & deferrals

**Resolved:** dispatcher front door → Free / Assisted / Rails with bounded ask (§0) · workflow intent-matching via auto-generated `index.yaml` with simple match + "did you mean X?" disambiguation + collision check (§0.4) · co-author-once / promote lifecycle (§0.5) · **run parameters** — one workflow, many runs (§4.4) · **conditional `when:` nodes** with skip cascade (§4.5) · **map/fan-out nodes** for bounded dynamic iteration (§4.6) · **status/list run inspection** (§16) · **starter template library** (§23) · headless gates → `scheduled_ok` auto-approve, else halt+notify · precedence → keyed deterministic merge, **per-row precedence + conflict flag** · verify → closed DSL over output metadata (also powers `when:`) · concurrency → per-workflow lock + single `out/` tree · credentials → via runtime connectors, never stored · gate `choose` → explicit `options` · open-node verify fail → escalate · `as_of` stamping for reproducibility.
**Deferred:** per-cell merge provenance (per-row + flag ships v1) · fork-variant matching (run params replace it) · confidence-tier matching (simple disambiguation ships) · sub-workflow composition / shared node library · coarse cross-run cache time-bucketing · node hang/timeout cap · git per-node auto-commit (commit-per-run only) · workflow auto-migration (warn-only).

## 23. Build plan (v1 starter kit)

1. Orchestrator **skill** + CLAUDE.md contract (incl. the §0 dispatcher protocol). 2. **Helper script** (Python) implementing §16 (validate, plan, start, ready, verify, merge, record, gate, restore, end, expand, status, list, reindex) with the §8 DSL, §9 per-row merge, §4.4 params, §4.5 `when:`, §4.6 map. 3. **Example workflow** `dashboard-refresh.md` (params, parallel fetch, merge/reconcile, a conditional node, gates). 4. **Starter template library** (report · reconcile/ETL · monitor-alert · digest) to seed the index. 5. **`harness.config.yaml`** template. 6. **Plugin** packaging + changelog. 7. **README/runbook**.
