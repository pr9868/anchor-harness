"""Pure affected-closure planning over a validated, immutable workflow plan."""
from __future__ import annotations

from dataclasses import dataclass
from .canonical import canonical_digest
from .models import ExecutionPlan
from .validation_values import require_digest


@dataclass(frozen=True, slots=True)
class ReusableOutput:
    node_id: str
    plan_digest: str
    output_digest: str

    def __post_init__(self):
        require_digest(self.plan_digest, "plan_digest")
        require_digest(self.output_digest, "output_digest")


@dataclass(frozen=True, slots=True)
class IncrementalPlan:
    baseline_plan_digest: str
    scheduled_node_ids: tuple[str, ...]
    reused: tuple[ReusableOutput, ...]
    scope_widened: bool
    reason: str
    digest: str


def plan_incremental(plan: ExecutionPlan, *, changed_node_ids: tuple[str, ...],
                     reusable: tuple[ReusableOutput, ...] = (),
                     dependencies_proven: bool = False) -> IncrementalPlan:
    """Schedule affected descendants, missing inputs, and mandatory verifiers.

    The caller must verify cached output bytes and dependency completeness before
    supplying reusable bindings. These are planning declarations, not execution
    receipts or a grant to perform effects. Unknown dependency proof, an unknown
    changed node, or a changed baseline plan widens to the complete selected plan.
    """
    if canonical_digest(plan.unsigned_dict()) != plan.digest:
        raise ValueError("execution plan digest does not match its contents")
    nodes = {node.node_id: node for node in plan.nodes}
    if len({item.node_id for item in reusable}) != len(reusable):
        raise ValueError("duplicate reusable node binding")
    if any(item.node_id not in nodes for item in reusable):
        raise ValueError("reusable binding names an unknown node")
    drift = any(item.plan_digest != plan.digest for item in reusable)
    widened = not dependencies_proven or drift or not set(changed_node_ids) <= nodes.keys()
    cache = {item.node_id: item for item in reusable}
    selected = set(nodes) if widened else set(changed_node_ids) | (nodes.keys() - cache.keys())
    # Invalidated results invalidate descendants. Dependencies can be supplied by
    # an exact verified baseline; otherwise they are already selected above.
    while True:
        before = set(selected)
        for node in plan.nodes:
            if set(node.depends_on) & selected:
                selected.add(node.node_id)
            if node.node_id in selected and node.verification_node_id:
                selected.add(node.verification_node_id)
        if before == selected:
            break
    scheduled = tuple(node.node_id for node in plan.nodes if node.node_id in selected)
    reused = tuple(cache[key] for key in sorted(nodes.keys() - selected))
    reason = "unknown_dependency_or_baseline_drift" if widened else "verified_affected_closure"
    unsigned = dict(baseline_plan_digest=plan.digest, scheduled_node_ids=scheduled,
                    reused=reused, scope_widened=widened, reason=reason)
    return IncrementalPlan(**unsigned, digest=canonical_digest(unsigned))
