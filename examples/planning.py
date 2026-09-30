"""Compile, select affected work, allocate bounded batches and resume exact results."""
import json
from pathlib import Path
from anchor_harness import (
    WorkflowManifest, compile_workflow, ReusableOutput, plan_incremental,
    CohortBudget, WorkEstimate, WorkUnit, build_cohort_manifest, allocate_attempt,
    CohortResponseBinding, CohortStatus, plan_resume,
)
from anchor_harness.canonical import canonical_digest


def main():
    manifest = WorkflowManifest.from_dict(json.loads(Path(__file__).with_name("workflow.json").read_text()))
    plan = compile_workflow(manifest)
    assert tuple(node.node_id for node in plan.nodes) == ("read", "render", "publish", "verify")
    # Synthetic cached identities; a real host must verify its cached bytes first.
    cached = tuple(ReusableOutput(n.node_id, plan.digest, canonical_digest(n.node_id)) for n in plan.nodes)
    impact = plan_incremental(plan, changed_node_ids=("render",), reusable=cached, dependencies_proven=True)
    assert impact.scheduled_node_ids == ("render", "publish", "verify")
    units = tuple(WorkUnit(f"work-{i}", "transform", f"item-{i}", canonical_digest(i), WorkEstimate()) for i in range(5))
    batches = build_cohort_manifest(plan_digest=plan.digest, work_units=units, budget=CohortBudget(max_items=2))
    attempt = allocate_attempt(batches, cohort_id=batches.cohorts[0].cohort_id,
        attempt_number=1, provider_adapter_digest="a" * 64, runtime_capability_digest="b" * 64)
    response = CohortResponseBinding(attempt, CohortStatus.SEALED_VALID, canonical_digest("synthetic-response"))
    resume = plan_resume(batches, (response,), provider_adapter_digest="a" * 64, runtime_capability_digest="b" * 64)
    assert len(resume.reusable) == 1 and len(resume.pending_cohort_ids) == 2
    print("Compilation, incremental closure, bounded allocation and selective resume passed.")


if __name__ == "__main__":
    main()
