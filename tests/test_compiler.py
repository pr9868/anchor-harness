from __future__ import annotations

import pytest

from anchor_harness import (
    CohortValidationError,
    DependencyDeclaration,
    ManifestValidationError,
    WorkflowManifest,
    compile_commit_cohorts,
    compile_workflow,
)


def manifest(nodes, **overrides):
    value = {
        "workflow_id": "test.workflow",
        "version": "1",
        "inputs": ["input.seed"],
        "nodes": nodes,
    }
    value.update(overrides)
    return WorkflowManifest.from_dict(value)


def node(node_id, *, kind="pure", depends_on=(), consumes=(), produces=(), **extra):
    value = {
        "node_id": node_id,
        "kind": kind,
        "depends_on": list(depends_on),
        "consumes": list(consumes),
        "produces": list(produces),
    }
    value.update(extra)
    return value


def issue_codes(error: ManifestValidationError) -> set[str]:
    return {issue.code for issue in error.issues}


def test_plan_is_topological_and_independent_of_manifest_order():
    first = node("a.collect", consumes=("input.seed",), produces=("evidence.a",))
    second = node(
        "b.synthesize",
        depends_on=("a.collect",),
        consumes=("evidence.a",),
        produces=("knowledge.a",),
    )
    plan_one = compile_workflow(manifest([second, first]), mode="full")
    plan_two = compile_workflow(manifest([first, second]), mode="full")
    assert [item.node_id for item in plan_one.nodes] == ["a.collect", "b.synthesize"]
    assert plan_one.digest == plan_two.digest
    assert plan_one.canonical_json() == plan_two.canonical_json()


def test_plan_digest_canonicalizes_unordered_node_collections():
    plan_one = compile_workflow(
        manifest(
            [
                node("a", produces=("artifact.a", "artifact.b")),
                node(
                    "b",
                    depends_on=("a",),
                    consumes=("artifact.a", "artifact.b"),
                    modes=["quick", "full"],
                ),
            ]
        ),
        mode="full",
    )
    plan_two = compile_workflow(
        manifest(
            [
                node("a", produces=("artifact.b", "artifact.a")),
                node(
                    "b",
                    depends_on=("a",),
                    consumes=("artifact.b", "artifact.a"),
                    modes=["full", "quick"],
                ),
            ]
        ),
        mode="full",
    )
    assert plan_one.digest == plan_two.digest


def test_stable_ids_are_explicit_and_schema_is_strict():
    with pytest.raises(ManifestValidationError) as caught:
        manifest([node("Bad Display Name")])
    assert "identity.node_id" in issue_codes(caught.value)
    with pytest.raises(ManifestValidationError) as caught:
        WorkflowManifest.from_dict(
            {
                "workflow_id": "test.workflow",
                "version": "1",
                "nodes": [],
                "surprise": True,
            }
        )
    assert "schema.unknown_field" in issue_codes(caught.value)


def test_cycle_and_missing_dependency_are_rejected():
    with pytest.raises(ManifestValidationError) as caught:
        manifest([node("a", depends_on=("b",)), node("b", depends_on=("a",))])
    assert "graph.cycle" in issue_codes(caught.value)
    with pytest.raises(ManifestValidationError) as caught:
        manifest([node("a", depends_on=("missing",))])
    assert "graph.missing_dependency" in issue_codes(caught.value)


def test_independent_manifest_errors_are_reported_even_when_graph_is_invalid():
    with pytest.raises(ManifestValidationError) as caught:
        manifest(
            [
                node("a", depends_on=("b",)),
                node("b", depends_on=("a",)),
                node(
                    "publish",
                    kind="external_effect",
                    consumes=("undeclared",),
                    verification_node_id="missing.verify",
                ),
            ],
            terminal_node_ids=["missing.terminal"],
        )
    assert {
        "graph.cycle",
        "artifact.undeclared_consume",
        "safety.missing_verifier",
        "graph.missing_terminal",
    } <= issue_codes(caught.value)


def test_artifact_producers_and_consumers_are_closed():
    with pytest.raises(ManifestValidationError) as caught:
        manifest(
            [
                node("a", produces=("shared",)),
                node("b", produces=("shared",)),
                node("c", consumes=("missing",)),
            ]
        )
    codes = issue_codes(caught.value)
    assert "artifact.multiple_producers" in codes
    assert "artifact.undeclared_consume" in codes


def test_consumed_output_requires_producer_in_dependency_closure():
    with pytest.raises(ManifestValidationError) as caught:
        manifest(
            [node("produce", produces=("artifact",)), node("consume", consumes=("artifact",))]
        )
    assert "artifact.producer_not_dependency" in issue_codes(caught.value)


def test_explicit_entries_and_terminals_cover_the_graph():
    with pytest.raises(ManifestValidationError) as caught:
        manifest(
            [node("root.one"), node("root.two")],
            entry_node_ids=["root.one"],
            terminal_node_ids=["root.one"],
        )
    codes = issue_codes(caught.value)
    assert "graph.unreachable" in codes
    assert "graph.uncovered_terminal" in codes


def test_every_node_outside_the_declared_entry_reachability_is_reported():
    with pytest.raises(ManifestValidationError) as caught:
        manifest(
            [
                node("root.one"),
                node("root.two"),
                node("child.two", depends_on=("root.two",)),
            ],
            entry_node_ids=["root.one"],
        )
    unreachable = {
        issue.location
        for issue in caught.value.issues
        if issue.code == "graph.unreachable"
    }
    assert unreachable == {"nodes.root.two", "nodes.child.two"}


def test_external_effect_requires_downstream_verification():
    with pytest.raises(ManifestValidationError) as caught:
        manifest([node("publish", kind="external_effect")])
    assert "safety.effect_without_verification" in issue_codes(caught.value)
    valid = manifest(
        [
            node(
                "publish",
                kind="external_effect",
                produces=("effect.receipt",),
                verification_node_id="verify.publish",
            ),
            node(
                "verify.publish",
                kind="verification",
                depends_on=("publish",),
                consumes=("effect.receipt",),
            ),
        ]
    )
    plan = compile_workflow(valid, mode="quick")
    assert [item.node_id for item in plan.nodes] == ["publish", "verify.publish"]


def test_anchor_rejects_every_position_commit_declaration():
    with pytest.raises(ManifestValidationError) as caught:
        manifest([node("a", declares_position_commit=True)])
    assert "safety.position_commit" in issue_codes(caught.value)


def test_mode_selection_closes_dependencies_and_explains_skips():
    workflow = manifest(
        [
            node("base", modes=["full"], produces=("base.data",)),
            node(
                "quick.page",
                depends_on=("base",),
                consumes=("base.data",),
                modes=["quick"],
            ),
            node("deep.audit", modes=["deep"]),
        ]
    )
    plan = compile_workflow(workflow, mode="quick")
    assert [item.node_id for item in plan.nodes] == ["base", "quick.page"]
    assert plan.nodes[0].selected_by == "dependency_of:quick.page"
    assert [(skip.node_id, skip.code) for skip in plan.skipped] == [
        ("deep.audit", "mode_not_selected")
    ]


def test_balanced_cohort_is_one_atomic_group():
    declarations = [
        DependencyDeclaration("slack", ("knowledge.slack",)),
        DependencyDeclaration("email", ("knowledge.email",)),
    ]
    cohorts = compile_commit_cohorts(declarations)
    assert len(cohorts) == 1
    assert tuple(cohorts[0].source_ids) == ("email", "slack")


def test_safe_split_uses_disconnected_output_components():
    declarations = [
        DependencyDeclaration("slack", ("knowledge.slack",)),
        DependencyDeclaration("email", ("knowledge.email",)),
    ]
    cohorts = compile_commit_cohorts(declarations, strategy="split")
    assert [tuple(cohort.source_ids) for cohort in cohorts] == [("email",), ("slack",)]


def test_shared_dependencies_and_overlapping_outputs_merge_split_cohorts():
    declarations = [
        DependencyDeclaration(
            "slack", ("knowledge.slack",), shared_dependency_ids=("customer.identity",)
        ),
        DependencyDeclaration(
            "email", ("knowledge.email",), shared_dependency_ids=("customer.identity",)
        ),
        DependencyDeclaration("teams", ("knowledge.email",)),
    ]
    cohorts = compile_commit_cohorts(declarations, strategy="split")
    assert len(cohorts) == 1
    assert tuple(cohorts[0].source_ids) == ("email", "slack", "teams")


def test_sources_sharing_an_effect_cannot_split_into_different_cohorts():
    declarations = [
        DependencyDeclaration(
            "slack",
            ("knowledge.slack",),
            effect_ids=("publish.summary",),
        ),
        DependencyDeclaration(
            "email",
            ("knowledge.email",),
            effect_ids=("publish.summary",),
        ),
    ]
    cohorts = compile_commit_cohorts(declarations, strategy="split")
    assert len(cohorts) == 1
    assert tuple(cohorts[0].source_ids) == ("email", "slack")
    assert tuple(cohorts[0].effect_ids) == ("publish.summary",)


def test_unknown_cohort_strategy_fails_closed():
    declaration = DependencyDeclaration("slack", ("knowledge.slack",))
    with pytest.raises(CohortValidationError):
        compile_commit_cohorts([declaration], strategy="optimistic")
