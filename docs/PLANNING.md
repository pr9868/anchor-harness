# Pure planning API — 0.4.0

Anchor owns planning; a host owns execution and verification; an integrity runtime
such as Ratchet owns durable effects, fencing and certification. A plan is never
an execution receipt, a successful run, or permission to publish.

## Compile a workflow

Install the library with `python -m pip install .`, then run:

```bash
anchor-plan examples/workflow.json --mode full
python examples/planning.py
```

Without installing the library, the plugin bundles the same compiler:

```bash
python skills/anchor-orchestrator/scripts/anchor-plan.py examples/workflow.json
```

A JSON manifest contains `workflow_id`, `version`, `nodes`, and optional `inputs`,
`entry_node_ids`, `terminal_node_ids`. Each node declares `node_id`, `kind`, and
optional `depends_on`, `consumes`, `produces`, `modes`, `verification_node_id`.
Supported kinds are `pure`, `evidence_read`, `local_durable_write`, `external_read`,
`external_effect`, `human_gate`, and `verification`. IDs are stable lowercase
identifiers. Unknown fields, cycles, missing producers/dependencies, unsafe effects
and attempted position-commit declarations are rejected.

`compile_workflow(WorkflowManifest.from_dict(value), mode="full")` returns an
immutable plan with canonical SHA-256 identity. Modes are `quick`, `full`, `deep`.
Dependencies and mandatory downstream effect verifiers are included even if their
own modes differ. Declaration order does not affect the plan digest.

## Incremental planning

`plan_incremental(plan, changed_node_ids=(...), reusable=(...),
dependencies_proven=True)` computes the affected descendants and mandatory
verifiers. `ReusableOutput` binds a node's output digest to the exact full-plan
digest. Missing reusable inputs are scheduled and invalidate their descendants.
Unknown changed IDs, missing dependency proof or plan drift widen to the full
selected plan. A changed plan intentionally prevents reuse in this release.

The host must verify reusable bytes and dependency completeness before supplying
these bindings. Anchor does not inspect files or discover hidden dependencies.
Changing policy or runtime assumptions requires a new baseline or full recompute.
The result names scheduled nodes and reused inputs; it does not run them.

## Bounded work and resume

Create `WorkUnit` values with a stable `stage`, object/input identity and integer
`WorkEstimate`; pass them to `build_cohort_manifest` with a plan digest and
`CohortBudget`. Each batch stays within item, input-character, input-token and
expected-duration limits. Oversize individual work fails before allocation.
These are declared estimates, not measured runtime caps. Stage names are arbitrary;
lexicographic allocation order is not execution order. The host must use its DAG.

`allocate_attempt` binds an attempt to the exact plan, allocation, provider-adapter
and runtime-capability digests. `plan_resume` reuses only exact `SEALED_VALID`
responses and allocates increasing attempt numbers for absent or incomplete work.
Drift, duplicate attempts, conflicting sealed responses, or an attempt after a
sealed response are rejected. The host verifies response content before sealing;
these functions maintain no durable attempt counter or result store.

Work batches are distinct from atomic commit cohorts. `compile_commit_cohorts`
merges sources sharing outputs, effects or dependencies; split mode separates only
independent connected components. Anchor proposes membership; Ratchet certifies it.

## Ratchet boundary

There is no Ratchet installation requirement. Anchor's small planning `CommitCohort`
value and Ratchet's runtime value are separate Python types. Convert explicitly:

```python
from dataclasses import asdict
from ratchet_sqlite.contracts import CommitCohort
runtime_cohort = CommitCohort(**asdict(anchor_cohort))
```

The host maps planned output identifiers to actual verified digest/proof identities
before constructing commit cohorts. Passing a plan directly to Ratchet is not a
valid substitute for execution, read-back and verified commit-bundle construction.

## Compatibility and lineage

The original YAML-frontmatter plugin workflows and `anchor.py` run state remain
unchanged. The new JSON schema/library is opt-in; there is no automatic migration
or retroactive strengthening of legacy execution. The existing design spec remains
a historical description of the legacy helper.

The deterministic compiler and cohort analysis came from the newer V2 planner;
bounded batching and exact resume came from V3. Generic incremental closure is the
public adaptation, not a port of V3 knowledge-graph semantics. Stage names and
schemas are public and independent. No Tracker Core, provider, customer content,
state or publishing logic is included. [Source hashes](UPSTREAM-PROVENANCE.json)
record input lineage; historical requirement comments identify origin only.
