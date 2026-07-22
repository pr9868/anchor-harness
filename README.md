# Anchor

A deterministic-enough harness for your agentic workflows. Anchor makes the agent
follow a declared graph of steps, never silently skip one, prove each step, pause
for you where it matters, and leave a full audit trail — while still letting it
think freely on open-ended work.

It orchestrates *judgment* (an agent), not compute — so it borrows a workflow
engine's DAG-in-YAML shape but runs on flat files and one small Python helper. No
database, no server, no cluster.

> **Status: work in progress — v0.3.0.** It runs and is tested in scratch projects,
> but hasn't been through heavy real-world use yet. I plan to put it through my own
> live workflows over the next few weeks, find where it strains, and refine it.
> Expect rough edges and breaking changes until it settles. Feedback and issues welcome.

## What's in the plugin

- **`anchor-orchestrator` skill** — teaches the agent the framework on install and
  drives execution: the dispatcher (free / assisted / rails), the run loop, gates,
  verify, params, conditionals, and the no-skip contract.
- **`anchor.py` helper** — the deterministic engine: validate, plan, start, ready,
  verify, merge, record, gate, status, list, reindex, restore, end.
- **Templates** — `dashboard-refresh`, `weekly-report`, `reconcile`, `monitor-alert`,
  `digest` to start from.
- **`harness.config.yaml`** and a **CLAUDE.md contract** snippet.
- **`docs/design-spec.md`** — the full design spec: every decision, and what shipped vs. what's deferred in v0.3.0.

## Requirements

- Python 3 with PyYAML: `pip install pyyaml`

## Set up a project

1. Install this plugin (Cowork: add the `.plugin`; Claude Code: add to a marketplace).
2. In your project folder, scaffold everything in one shot — just ask the agent to
   **"set up Anchor here,"** or run:
   `python3 anchor.py init --template dashboard-refresh`   (or `--all` for every starter)
   This creates `workflows/` with your chosen templates, `harness.config.yaml`, the
   `CLAUDE.md` contract, and the workflow index. No manual copying.
3. Open `harness.config.yaml` and set your sources / tiers / notify target.
4. Just ask — "refresh the dashboard for Q2" — and the agent dispatches, matches the
   workflow, and runs it node by node.

## How a run works

The agent classifies your request. If it matches a workflow it follows the graph via
the helper: `ready` says what runs next, the agent does each node at its autonomy,
records an output, `verify` proves it, and blocking gates pause for you. Conditional
(`when:`) nodes skip themselves when their predicate is false. Params make one
workflow serve many runs (`--param quarter=Q2`). Everything is logged under `runs/`
for audit and resume.

## Core concepts

- **Free-DAG engine** — nodes wired by `produces`/`consumes`; order falls out of data
  flow, so steps can't be skipped or reordered.
- **Autonomy per node** — `locked` / `guided` / `open`: determinism where you want it,
  freedom where you don't.
- **Node-level gates** — `before` (authorize) / `after` (review), blocking or notify.
- **Verify** — a closed DSL over each node's metadata, or a `subagent:` judgment check.
- **Source credibility** — disagreeing sources reconcile by declared tier, conflicts
  flagged, never silently guessed.
- **Flat-file audit** — automatic run snapshots; git optional and off by default.

See the skill's `references/workflow-schema.md` for the full schema and
`references/dispatcher.md` for routing.

## Helper quick reference

```
python3 anchor.py init --template <name>   # or --all : scaffold a new project
python3 anchor.py validate <workflow>
python3 anchor.py reindex
python3 anchor.py match "<request>"
python3 anchor.py plan <workflow>
python3 anchor.py start <workflow> --scope full --param k=v
python3 anchor.py ready <run_id>
python3 anchor.py record <run_id> --node <id> --output <path> --meta '{"row_count":N}'
python3 anchor.py verify <run_id> --node <id>
python3 anchor.py gate <run_id> --node <id> --approve | --choose <opt> | --reject
python3 anchor.py status <run_id>
python3 anchor.py list
python3 anchor.py restore <run_id>
python3 anchor.py end <run_id>
```
