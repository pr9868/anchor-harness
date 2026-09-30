"""Pure deterministic Anchor workflow compiler."""

from __future__ import annotations

from typing import Iterable

from .contracts import canonical_digest

from .models import (
    ExecutionNode,
    ExecutionPlan,
    RunMode,
    SkipExplanation,
    WorkflowManifest,
    _kind_value,
)
from .validation import deterministic_topological_order, validate_manifest


def compile_workflow(
    manifest: WorkflowManifest,
    *,
    mode: RunMode | str = RunMode.FULL,
) -> ExecutionPlan:
    """Compile a valid manifest with dependency and verifier closure."""

    validate_manifest(manifest)
    selected_mode = mode if isinstance(mode, RunMode) else RunMode(mode)
    nodes = {node.node_id: node for node in manifest.nodes}
    direct = {node.node_id for node in manifest.nodes if selected_mode in node.modes}
    selected = set(direct)
    selection_reason = {node_id: f"mode:{selected_mode.value}" for node_id in direct}

    def add_dependencies(seed_ids: Iterable[str]) -> None:
        pending = list(sorted(seed_ids))
        while pending:
            node_id = pending.pop()
            for dependency in sorted(nodes[node_id].depends_on):
                if dependency not in selected:
                    selected.add(dependency)
                    selection_reason[dependency] = f"dependency_of:{node_id}"
                    pending.append(dependency)

    add_dependencies(selected)

    while True:
        added: set[str] = set()
        for node_id in sorted(selected):
            node = nodes[node_id]
            if _kind_value(node.kind) != "external_effect":
                continue
            verifier = node.verification_node_id
            if verifier and verifier not in selected:
                selected.add(verifier)
                selection_reason[verifier] = f"required_verification_of:{node_id}"
                added.add(verifier)
        if not added:
            break
        add_dependencies(added)

    global_order = deterministic_topological_order(manifest)
    selected_order = tuple(node_id for node_id in global_order if node_id in selected)
    execution_nodes = tuple(
        ExecutionNode(
            ordinal=index,
            node_id=node_id,
            kind=nodes[node_id].kind,
            depends_on=tuple(
                sorted(
                    dependency
                for dependency in nodes[node_id].depends_on
                if dependency in selected
                )
            ),
            consumes=tuple(sorted(nodes[node_id].consumes)),
            produces=tuple(sorted(nodes[node_id].produces)),
            selected_by=selection_reason[node_id],
            verification_node_id=nodes[node_id].verification_node_id,
        )
        for index, node_id in enumerate(selected_order)
    )
    skipped = tuple(
        SkipExplanation(
            node_id=node.node_id,
            code="mode_not_selected",
            detail=(
                f"node is enabled for {', '.join(sorted(item.value for item in node.modes))}; "
                f"requested mode is {selected_mode.value}"
            ),
        )
        for node in sorted(manifest.nodes, key=lambda item: item.node_id)
        if node.node_id not in selected
    )

    dependents = {node_id: set() for node_id in selected}
    for node_id in selected:
        for dependency in nodes[node_id].depends_on:
            if dependency in selected:
                dependents[dependency].add(node_id)
    selected_terminals = tuple(
        sorted(node_id for node_id, children in dependents.items() if not children)
    )
    unsigned = {
        "schema": "anchor.execution-plan.v1",
        "workflow_id": manifest.workflow_id,
        "workflow_version": manifest.version,
        "mode": selected_mode.value,
        "nodes": [node.to_dict() for node in execution_nodes],
        "skipped": [skip.to_dict() for skip in skipped],
        "terminal_node_ids": list(selected_terminals),
    }
    return ExecutionPlan(
        workflow_id=manifest.workflow_id,
        workflow_version=manifest.version,
        mode=selected_mode,
        nodes=execution_nodes,
        skipped=skipped,
        terminal_node_ids=selected_terminals,
        digest=str(canonical_digest(unsigned)),
    )


__all__ = ["compile_workflow"]
