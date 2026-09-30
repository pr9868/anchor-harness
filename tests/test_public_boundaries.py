from dataclasses import replace
import pytest
from anchor_harness import (
    WorkflowManifest, compile_workflow, WorkUnit, WorkEstimate, CohortBudget,
    build_cohort_manifest, CohortResponseBinding, CohortStatus, allocate_attempt, plan_resume,
)


def test_dependency_array_order_cannot_change_plan_identity():
    nodes = [
        {"node_id": "root", "kind": "pure", "modes": ["deep"]},
        {"node_id": "a", "kind": "pure", "depends_on": ["root"], "modes": ["deep"]},
        {"node_id": "b", "kind": "pure", "depends_on": ["root"], "modes": ["deep"]},
        {"node_id": "c", "kind": "pure", "depends_on": ["a", "b"]},
    ]
    first = compile_workflow(WorkflowManifest.from_dict(dict(workflow_id="demo", version="1", nodes=nodes)))
    nodes[-1]["depends_on"].reverse()
    second = compile_workflow(WorkflowManifest.from_dict(dict(workflow_id="demo", version="1", nodes=nodes[::-1])))
    assert first.digest == second.digest


def test_direct_manifest_cannot_claim_a_smaller_budget():
    units = (WorkUnit("one", "transform", "item", "a" * 64, WorkEstimate()),)
    manifest = build_cohort_manifest(plan_digest="b" * 64, work_units=units, budget=CohortBudget())
    with pytest.raises(ValueError, match="budget"):
        replace(manifest, budget=CohortBudget(max_input_tokens=1))


def test_resume_rejects_invalid_status_duplicate_and_post_seal_attempts():
    units = (WorkUnit("one", "transform", "item", "a" * 64, WorkEstimate()),)
    manifest = build_cohort_manifest(plan_digest="b" * 64, work_units=units, budget=CohortBudget())
    kwargs = dict(provider_adapter_digest="c" * 64, runtime_capability_digest="d" * 64)
    first = allocate_attempt(manifest, cohort_id=manifest.cohorts[0].cohort_id, attempt_number=1, **kwargs)
    sealed = CohortResponseBinding(first, CohortStatus.SEALED_VALID, "e" * 64)
    with pytest.raises(ValueError, match="status"):
        CohortResponseBinding(first, "sealed-valid", "e" * 64)
    with pytest.raises(ValueError, match="unique"):
        plan_resume(manifest, (sealed, sealed), **kwargs)
    later = allocate_attempt(manifest, cohort_id=manifest.cohorts[0].cohort_id, attempt_number=2, **kwargs)
    with pytest.raises(ValueError, match="after"):
        plan_resume(manifest, (sealed, CohortResponseBinding(later, CohortStatus.FAILED)), **kwargs)
