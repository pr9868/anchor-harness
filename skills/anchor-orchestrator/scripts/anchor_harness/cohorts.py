"""Generic source dependency graph and deterministic cohort proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import CommitCohort, canonical_digest

from .errors import CohortValidationError
from .models import DependencyDeclaration


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """Undirected safety graph derived from generic dependency declarations."""

    declarations: tuple[DependencyDeclaration, ...]
    adjacency: tuple[tuple[str, tuple[str, ...]], ...]

    @classmethod
    def build(
        cls, declarations: Iterable[DependencyDeclaration]
    ) -> "DependencyGraph":
        ordered = tuple(sorted(declarations, key=lambda item: item.source_id))
        if not ordered:
            raise CohortValidationError("at least one source declaration is required")
        source_ids = [item.source_id for item in ordered]
        if len(source_ids) != len(set(source_ids)):
            raise CohortValidationError("source identifiers must be unique")
        known = set(source_ids)
        adjacency: dict[str, set[str]] = {source_id: set() for source_id in source_ids}

        def connect(left: str, right: str) -> None:
            if left != right:
                adjacency[left].add(right)
                adjacency[right].add(left)

        for declaration in ordered:
            for dependency in declaration.depends_on_source_ids:
                if dependency not in known:
                    raise CohortValidationError(
                        f"source {declaration.source_id!r} depends on unknown source "
                        f"{dependency!r}"
                    )
                connect(declaration.source_id, dependency)

        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if set(left.output_ids) & set(right.output_ids):
                    connect(left.source_id, right.source_id)
                if set(left.effect_ids) & set(right.effect_ids):
                    connect(left.source_id, right.source_id)
                if set(left.shared_dependency_ids) & set(right.shared_dependency_ids):
                    connect(left.source_id, right.source_id)

        return cls(
            declarations=ordered,
            adjacency=tuple(
                (source_id, tuple(sorted(neighbors)))
                for source_id, neighbors in sorted(adjacency.items())
            ),
        )

    def connected_components(self) -> tuple[tuple[str, ...], ...]:
        adjacency = {source_id: set(neighbors) for source_id, neighbors in self.adjacency}
        remaining = set(adjacency)
        components: list[tuple[str, ...]] = []
        while remaining:
            seed = min(remaining)
            pending = [seed]
            component: set[str] = set()
            while pending:
                current = pending.pop()
                if current in component:
                    continue
                component.add(current)
                pending.extend(sorted(adjacency[current] - component, reverse=True))
            remaining -= component
            components.append(tuple(sorted(component)))
        return tuple(sorted(components))


def _cohort(
    declarations: dict[str, DependencyDeclaration], source_ids: tuple[str, ...]
) -> CommitCohort:
    output_ids = tuple(
        sorted(
            {
                output_id
                for source_id in source_ids
                for output_id in declarations[source_id].output_ids
            }
        )
    )
    effect_ids = tuple(
        sorted(
            {
                effect_id
                for source_id in source_ids
                for effect_id in declarations[source_id].effect_ids
            }
        )
    )
    identity = {
        "source_ids": list(source_ids),
        "output_ids": list(output_ids),
        "effect_ids": list(effect_ids),
    }
    cohort_id = f"cohort-{str(canonical_digest(identity))[:16]}"
    return CommitCohort(
        cohort_id=cohort_id,
        source_ids=source_ids,
        output_ids=output_ids,
        effect_ids=effect_ids,
    )


def compile_commit_cohorts(
    declarations: Iterable[DependencyDeclaration], *, strategy: str = "balanced"
) -> tuple[CommitCohort, ...]:
    """Propose deterministic cohorts; never commit source positions."""

    graph = DependencyGraph.build(declarations)
    by_source = {item.source_id: item for item in graph.declarations}
    all_sources = tuple(sorted(by_source))
    if strategy == "balanced":
        return (_cohort(by_source, all_sources),)
    if strategy != "split":
        raise CohortValidationError(
            f"unknown cohort strategy {strategy!r}; expected 'balanced' or 'split'"
        )
    return tuple(
        _cohort(by_source, component) for component in graph.connected_components()
    )


__all__ = ["DependencyGraph", "compile_commit_cohorts"]
