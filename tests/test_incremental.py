from dataclasses import replace
import pytest
from anchor_harness import (WorkflowManifest, compile_workflow, plan_incremental, ReusableOutput,
                            CohortBudget, WorkEstimate)
from anchor_harness.canonical import canonical_digest


def fixture():
    plan = compile_workflow(WorkflowManifest.from_dict({
        "workflow_id": "demo", "version": "1", "nodes": [
            {"node_id": "read", "kind": "pure"},
            {"node_id": "build", "kind": "pure", "depends_on": ["read"]},
            {"node_id": "publish", "kind": "external_effect", "depends_on": ["build"],
             "verification_node_id": "verify"},
            {"node_id": "verify", "kind": "verification", "depends_on": ["publish"]},
            {"node_id": "independent", "kind": "pure"},
        ]}))
    cached = tuple(ReusableOutput(n.node_id, plan.digest, canonical_digest(n.node_id)) for n in plan.nodes)
    return plan, cached


def test_changed_branch_keeps_cached_inputs_and_includes_effect_verifier():
    plan, cached = fixture()
    impact = plan_incremental(plan, changed_node_ids=("build",), reusable=cached, dependencies_proven=True)
    assert impact.scheduled_node_ids == ("build", "publish", "verify")
    assert {item.node_id for item in impact.reused} == {"read", "independent"}
    assert not impact.scope_widened


def test_missing_cache_invalidates_descendants():
    plan, cached = fixture()
    impact = plan_incremental(plan, changed_node_ids=(), reusable=tuple(x for x in cached if x.node_id != "read"), dependencies_proven=True)
    assert set(impact.scheduled_node_ids) == {"read", "build", "publish", "verify"}


@pytest.mark.parametrize("case", ["unknown", "unproven", "drift"])
def test_uncertain_baseline_widens_safely(case):
    plan, cached = fixture()
    changed = ("unknown",) if case == "unknown" else ()
    if case == "drift":
        cached = (replace(cached[0], plan_digest="f" * 64),) + cached[1:]
    impact = plan_incremental(plan, changed_node_ids=changed, reusable=cached, dependencies_proven=case != "unproven")
    assert impact.scope_widened
    assert len(impact.scheduled_node_ids) == len(plan.nodes)
    assert not impact.reused


def test_unchanged_verified_baseline_schedules_nothing_and_is_deterministic():
    plan, cached = fixture()
    first = plan_incremental(plan, changed_node_ids=(), reusable=cached, dependencies_proven=True)
    second = plan_incremental(plan, changed_node_ids=(), reusable=tuple(reversed(cached)), dependencies_proven=True)
    assert not first.scheduled_node_ids
    assert first.digest == second.digest


def test_tampered_plan_and_duplicate_baseline_are_rejected():
    plan, cached = fixture()
    with pytest.raises(ValueError, match="digest"):
        plan_incremental(replace(plan, digest="f" * 64), changed_node_ids=())
    with pytest.raises(ValueError, match="duplicate"):
        plan_incremental(plan, changed_node_ids=(), reusable=cached + cached[:1])


@pytest.mark.parametrize("value", [True, 1.5, float("inf"), float("nan"), -1])
def test_resource_accounting_rejects_non_integral_or_negative_values(value):
    with pytest.raises(ValueError):
        CohortBudget(max_items=value)
    with pytest.raises(ValueError):
        WorkEstimate(input_tokens=value)


def test_canonical_keys_cannot_collide_after_stringification():
    with pytest.raises(TypeError):
        canonical_digest({1: "first", "1": "second"})
