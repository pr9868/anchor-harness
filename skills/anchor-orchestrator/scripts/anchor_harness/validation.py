"""Deterministic structural validation for Anchor workflow manifests."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .errors import ManifestValidationError, ValidationIssue
from .models import ManifestNode, WorkflowManifest, _STABLE_ID, _WORKFLOW_VERSION, _kind_value


def _duplicates(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


def deterministic_topological_order(manifest: WorkflowManifest) -> tuple[str, ...]:
    """Return a stable node order, raising when the graph contains a cycle."""

    node_ids = {node.node_id for node in manifest.nodes}
    indegree = {node_id: 0 for node_id in node_ids}
    children: dict[str, set[str]] = defaultdict(set)
    for node in manifest.nodes:
        for dependency in node.depends_on:
            if dependency in node_ids and node.node_id not in children[dependency]:
                children[dependency].add(node.node_id)
                indegree[node.node_id] += 1

    ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    result: list[str] = []
    while ready:
        current = ready.pop(0)
        result.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()

    if len(result) != len(node_ids):
        cyclic = sorted(node_id for node_id, degree in indegree.items() if degree > 0)
        raise ManifestValidationError(
            [
                ValidationIssue(
                    "graph.cycle",
                    "workflow.nodes",
                    f"cycle involves: {', '.join(cyclic)}",
                )
            ]
        )
    return tuple(result)


def _ancestor_map(nodes: dict[str, ManifestNode]) -> dict[str, set[str]]:
    memo: dict[str, set[str]] = {}

    def ancestors(node_id: str) -> set[str]:
        if node_id in memo:
            return memo[node_id]
        result: set[str] = set()
        for dependency in nodes[node_id].depends_on:
            if dependency in nodes:
                result.add(dependency)
                result.update(ancestors(dependency))
        memo[node_id] = result
        return result

    for node_id in nodes:
        ancestors(node_id)
    return memo


def validate_manifest(manifest: WorkflowManifest) -> None:
    """Validate all schema-level and graph-level Anchor invariants."""

    issues: list[ValidationIssue] = []
    if not _STABLE_ID.fullmatch(manifest.workflow_id):
        issues.append(
            ValidationIssue(
                "identity.workflow_id",
                "workflow.workflow_id",
                "must match the stable lowercase identifier grammar",
            )
        )
    if not _WORKFLOW_VERSION.fullmatch(manifest.version):
        issues.append(
            ValidationIssue(
                "identity.version",
                "workflow.version",
                "must be a stable non-empty version token",
            )
        )
    if not manifest.nodes:
        issues.append(
            ValidationIssue("graph.empty", "workflow.nodes", "at least one node is required")
        )

    duplicate_node_ids = _duplicates(node.node_id for node in manifest.nodes)
    for node_id in duplicate_node_ids:
        issues.append(
            ValidationIssue(
                "identity.duplicate_node",
                f"nodes.{node_id}",
                "node identifiers must be globally unique",
            )
        )
    nodes = {node.node_id: node for node in manifest.nodes}
    node_ids = set(nodes)

    if len(manifest.inputs) != len(set(manifest.inputs)):
        issues.append(
            ValidationIssue(
                "artifact.duplicate_input",
                "workflow.inputs",
                "declared input identifiers must be unique",
            )
        )

    producers: dict[str, str] = {}
    for node in manifest.nodes:
        location = f"nodes.{node.node_id}"
        if not _STABLE_ID.fullmatch(node.node_id):
            issues.append(
                ValidationIssue(
                    "identity.node_id",
                    f"{location}.node_id",
                    "must be explicit and match the stable lowercase identifier grammar",
                )
            )
        for field_name, values in (
            ("depends_on", node.depends_on),
            ("consumes", node.consumes),
            ("produces", node.produces),
            ("modes", tuple(mode.value for mode in node.modes)),
        ):
            for duplicate in _duplicates(values):
                issues.append(
                    ValidationIssue(
                        f"schema.duplicate_{field_name}",
                        f"{location}.{field_name}",
                        f"duplicate value {duplicate!r}",
                    )
                )
        if not node.modes:
            issues.append(
                ValidationIssue(
                    "selection.no_mode",
                    f"{location}.modes",
                    "a node must participate in at least one run mode",
                )
            )
        for dependency in node.depends_on:
            if dependency == node.node_id:
                issues.append(
                    ValidationIssue(
                        "graph.self_dependency",
                        f"{location}.depends_on",
                        "a node cannot depend on itself",
                    )
                )
            elif dependency not in node_ids:
                issues.append(
                    ValidationIssue(
                        "graph.missing_dependency",
                        f"{location}.depends_on",
                        f"unknown node {dependency!r}",
                    )
                )
        for artifact in node.produces:
            previous = producers.get(artifact)
            if previous is not None and previous != node.node_id:
                issues.append(
                    ValidationIssue(
                        "artifact.multiple_producers",
                        f"{location}.produces",
                        f"{artifact!r} is already produced by {previous!r}",
                    )
                )
            else:
                producers[artifact] = node.node_id
        if node.declares_position_commit:
            issues.append(
                ValidationIssue(
                    "safety.position_commit",
                    f"{location}.declares_position_commit",
                    "Anchor nodes may never declare or perform a source-position commit",
                )
            )
        if _kind_value(node.kind) == "external_effect" and not node.verification_node_id:
            issues.append(
                ValidationIssue(
                    "safety.effect_without_verification",
                    f"{location}.verification_node_id",
                    "an external effect must name a downstream verification node",
                )
            )

    graph_is_closed = not any(
        issue.code in {"identity.duplicate_node", "graph.missing_dependency"}
        for issue in issues
    )
    order: tuple[str, ...] = ()
    if graph_is_closed:
        try:
            order = deterministic_topological_order(manifest)
        except ManifestValidationError as error:
            issues.extend(error.issues)

    # Checks that do not require a valid topological order must still run when
    # another part of the graph is cyclic or names a missing dependency. A
    # manifest should report its independent defects in one compile attempt.
    declared_artifacts = set(manifest.inputs) | set(producers)
    for node in manifest.nodes:
        for artifact in node.consumes:
            if artifact not in declared_artifacts:
                issues.append(
                    ValidationIssue(
                        "artifact.undeclared_consume",
                        f"nodes.{node.node_id}.consumes",
                        f"{artifact!r} is neither a workflow input nor a produced artifact",
                    )
                )

    ancestors = _ancestor_map(nodes) if order else {}
    if order:
        for node in manifest.nodes:
            for artifact in node.consumes:
                producer = producers.get(artifact)
                if producer is not None and producer not in ancestors[node.node_id]:
                    issues.append(
                        ValidationIssue(
                            "artifact.producer_not_dependency",
                            f"nodes.{node.node_id}.consumes",
                            f"producer {producer!r} for {artifact!r} is not in dependency closure",
                        )
                    )

    for node in manifest.nodes:
        verifier_id = node.verification_node_id
        if not verifier_id:
            continue
        verifier = nodes.get(verifier_id)
        if verifier is None:
            issues.append(
                ValidationIssue(
                    "safety.missing_verifier",
                    f"nodes.{node.node_id}.verification_node_id",
                    f"unknown verification node {verifier_id!r}",
                )
            )
            continue
        if _kind_value(verifier.kind) != "verification":
            issues.append(
                ValidationIssue(
                    "safety.invalid_verifier_kind",
                    f"nodes.{node.node_id}.verification_node_id",
                    f"{verifier_id!r} is not a verification node",
                )
            )
        if order and node.node_id not in ancestors[verifier_id]:
            issues.append(
                ValidationIssue(
                    "safety.verifier_not_downstream",
                    f"nodes.{node.node_id}.verification_node_id",
                    f"{verifier_id!r} must depend on the effect node",
                )
            )

    roots = {node.node_id for node in manifest.nodes if not node.depends_on}
    entries = set(manifest.entry_node_ids) if manifest.entry_node_ids else roots
    for entry in entries:
        if entry not in node_ids:
            issues.append(
                ValidationIssue(
                    "graph.missing_entry",
                    "workflow.entry_node_ids",
                    f"unknown entry node {entry!r}",
                )
            )
        elif nodes[entry].depends_on:
            issues.append(
                ValidationIssue(
                    "graph.entry_has_dependency",
                    "workflow.entry_node_ids",
                    f"entry node {entry!r} is not a graph root",
                )
            )

    if order:
        children: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        for node in manifest.nodes:
            for dependency in node.depends_on:
                children[dependency].add(node.node_id)
        reachable: set[str] = set()
        pending = sorted(entries & node_ids, reverse=True)
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            pending.extend(sorted(children[current] - reachable, reverse=True))
        for node_id in sorted(node_ids - reachable):
            issues.append(
                ValidationIssue(
                    "graph.unreachable",
                    f"nodes.{node_id}",
                    "node is not reachable from the declared entry set",
                )
            )

    dependents: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for node in manifest.nodes:
        for dependency in node.depends_on:
            if dependency in dependents:
                dependents[dependency].add(node.node_id)
    sinks = {node_id for node_id, children in dependents.items() if not children}
    terminals = set(manifest.terminal_node_ids) if manifest.terminal_node_ids else sinks
    for terminal in terminals:
        if terminal not in node_ids:
            issues.append(
                ValidationIssue(
                    "graph.missing_terminal",
                    "workflow.terminal_node_ids",
                    f"unknown terminal node {terminal!r}",
                )
            )
        elif dependents[terminal]:
            issues.append(
                ValidationIssue(
                    "graph.terminal_has_dependents",
                    "workflow.terminal_node_ids",
                    f"terminal node {terminal!r} has downstream nodes",
                )
            )
    if manifest.terminal_node_ids:
        for sink in sorted(sinks - terminals):
            issues.append(
                ValidationIssue(
                    "graph.uncovered_terminal",
                    f"nodes.{sink}",
                    "sink is not included in the explicit terminal set",
                )
            )

    if issues:
        raise ManifestValidationError(issues)


__all__ = ["deterministic_topological_order", "validate_manifest"]
