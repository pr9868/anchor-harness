"""Immutable declarative and compiled models owned by Anchor compiler."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Mapping

from .contracts import NodeKind, canonical_json

from .errors import ManifestValidationError, ValidationIssue


_STABLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_WORKFLOW_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class RunMode(StrEnum):
    """Supported declarative run selections."""

    QUICK = "quick"
    FULL = "full"
    DEEP = "deep"


ALL_RUN_MODES: tuple[RunMode, ...] = (
    RunMode.QUICK,
    RunMode.FULL,
    RunMode.DEEP,
)


def _kind_value(kind: NodeKind) -> str:
    return str(getattr(kind, "value", kind))


def _strict_keys(
    value: Mapping[str, Any], *, allowed: set[str], location: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ManifestValidationError(
            [
                ValidationIssue(
                    "schema.unknown_field",
                    f"{location}.{name}",
                    "field is not part of the Anchor compiler manifest schema",
                )
                for name in unknown
            ]
        )


def _string(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(
            [ValidationIssue("schema.string", location, "must be a non-empty string")]
        )
    return value


def _strings(value: Any, *, location: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ManifestValidationError(
            [ValidationIssue("schema.array", location, "must be an array of strings")]
        )
    return tuple(
        _string(item, location=f"{location}[{index}]")
        for index, item in enumerate(value)
    )


def _boolean(value: Any, *, location: str) -> bool:
    if not isinstance(value, bool):
        raise ManifestValidationError(
            [ValidationIssue("schema.boolean", location, "must be a boolean")]
        )
    return value


@dataclass(frozen=True, slots=True)
class ManifestNode:
    """One declarative node with a mandatory semantic, stable identifier."""

    node_id: str
    kind: NodeKind
    depends_on: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    modes: tuple[RunMode, ...] = ALL_RUN_MODES
    verification_node_id: str | None = None
    declares_position_commit: bool = False

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ManifestNode":
        if not isinstance(value, Mapping):
            raise ManifestValidationError(
                [ValidationIssue("schema.object", "node", "must be an object")]
            )
        _strict_keys(
            value,
            allowed={
                "node_id",
                "kind",
                "depends_on",
                "consumes",
                "produces",
                "modes",
                "verification_node_id",
                "declares_position_commit",
            },
            location="node",
        )
        node_id = _string(value.get("node_id"), location="node.node_id")
        raw_kind = _string(value.get("kind"), location=f"nodes.{node_id}.kind")
        try:
            kind = NodeKind(raw_kind)
        except (TypeError, ValueError) as error:
            raise ManifestValidationError(
                [
                    ValidationIssue(
                        "schema.node_kind",
                        f"nodes.{node_id}.kind",
                        f"unsupported node kind {raw_kind!r}",
                    )
                ]
            ) from error

        raw_modes = value.get("modes", [mode.value for mode in ALL_RUN_MODES])
        modes: list[RunMode] = []
        for index, raw_mode in enumerate(
            _strings(raw_modes, location=f"nodes.{node_id}.modes")
        ):
            try:
                modes.append(RunMode(raw_mode))
            except ValueError as error:
                raise ManifestValidationError(
                    [
                        ValidationIssue(
                            "schema.run_mode",
                            f"nodes.{node_id}.modes[{index}]",
                            f"unsupported run mode {raw_mode!r}",
                        )
                    ]
                ) from error

        raw_verifier = value.get("verification_node_id")
        verifier = (
            None
            if raw_verifier is None
            else _string(raw_verifier, location=f"nodes.{node_id}.verification_node_id")
        )
        return cls(
            node_id=node_id,
            kind=kind,
            depends_on=_strings(
                value.get("depends_on", ()), location=f"nodes.{node_id}.depends_on"
            ),
            consumes=_strings(
                value.get("consumes", ()), location=f"nodes.{node_id}.consumes"
            ),
            produces=_strings(
                value.get("produces", ()), location=f"nodes.{node_id}.produces"
            ),
            modes=tuple(modes),
            verification_node_id=verifier,
            declares_position_commit=_boolean(
                value.get("declares_position_commit", False),
                location=f"nodes.{node_id}.declares_position_commit",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": _kind_value(self.kind),
            "depends_on": list(self.depends_on),
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "modes": [mode.value for mode in self.modes],
            "verification_node_id": self.verification_node_id,
            "declares_position_commit": self.declares_position_commit,
        }


@dataclass(frozen=True, slots=True)
class WorkflowManifest:
    """Strict declarative workflow definition."""

    workflow_id: str
    version: str
    nodes: tuple[ManifestNode, ...]
    inputs: tuple[str, ...] = ()
    entry_node_ids: tuple[str, ...] = ()
    terminal_node_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowManifest":
        if not isinstance(value, Mapping):
            raise ManifestValidationError(
                [ValidationIssue("schema.object", "workflow", "must be an object")]
            )
        _strict_keys(
            value,
            allowed={
                "workflow_id",
                "version",
                "nodes",
                "inputs",
                "entry_node_ids",
                "terminal_node_ids",
            },
            location="workflow",
        )
        workflow_id = _string(value.get("workflow_id"), location="workflow.workflow_id")
        version = _string(value.get("version"), location="workflow.version")
        raw_nodes = value.get("nodes")
        if not isinstance(raw_nodes, (list, tuple)) or isinstance(
            raw_nodes, (str, bytes)
        ):
            raise ManifestValidationError(
                [ValidationIssue("schema.array", "workflow.nodes", "must be an array")]
            )
        manifest = cls(
            workflow_id=workflow_id,
            version=version,
            nodes=tuple(ManifestNode.from_dict(item) for item in raw_nodes),
            inputs=_strings(value.get("inputs", ()), location="workflow.inputs"),
            entry_node_ids=_strings(
                value.get("entry_node_ids", ()), location="workflow.entry_node_ids"
            ),
            terminal_node_ids=_strings(
                value.get("terminal_node_ids", ()), location="workflow.terminal_node_ids"
            ),
        )
        from .validation import validate_manifest

        validate_manifest(manifest)
        return manifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "version": self.version,
            "inputs": list(self.inputs),
            "entry_node_ids": list(self.entry_node_ids),
            "terminal_node_ids": list(self.terminal_node_ids),
            "nodes": [node.to_dict() for node in self.nodes],
        }


@dataclass(frozen=True, slots=True)
class ExecutionNode:
    """One selected node in deterministic execution order."""

    ordinal: int
    node_id: str
    kind: NodeKind
    depends_on: tuple[str, ...]
    consumes: tuple[str, ...]
    produces: tuple[str, ...]
    selected_by: str
    verification_node_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "node_id": self.node_id,
            "kind": _kind_value(self.kind),
            "depends_on": list(self.depends_on),
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "selected_by": self.selected_by,
            "verification_node_id": self.verification_node_id,
        }


@dataclass(frozen=True, slots=True)
class SkipExplanation:
    """Why a valid manifest node is absent from a selected plan."""

    node_id: str
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"node_id": self.node_id, "code": self.code, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Immutable deterministic output of the Anchor compiler."""

    workflow_id: str
    workflow_version: str
    mode: RunMode
    nodes: tuple[ExecutionNode, ...]
    skipped: tuple[SkipExplanation, ...]
    terminal_node_ids: tuple[str, ...]
    digest: str

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema": "anchor.execution-plan.v1",
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "mode": self.mode.value,
            "nodes": [node.to_dict() for node in self.nodes],
            "skipped": [skip.to_dict() for skip in self.skipped],
            "terminal_node_ids": list(self.terminal_node_ids),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "digest": self.digest}

    def canonical_json(self) -> str | bytes:
        return canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class DependencyDeclaration:
    """Generic source/output relationship used only for cohort planning."""

    source_id: str
    output_ids: tuple[str, ...]
    depends_on_source_ids: tuple[str, ...] = ()
    shared_dependency_ids: tuple[str, ...] = ()
    effect_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        issues: list[ValidationIssue] = []
        if not _STABLE_ID.fullmatch(self.source_id):
            issues.append(
                ValidationIssue(
                    "cohort.source_id",
                    f"sources.{self.source_id}",
                    "must be an explicit stable identifier",
                )
            )
        for field_name, values in (
            ("output_ids", self.output_ids),
            ("depends_on_source_ids", self.depends_on_source_ids),
            ("shared_dependency_ids", self.shared_dependency_ids),
            ("effect_ids", self.effect_ids),
        ):
            if len(values) != len(set(values)):
                issues.append(
                    ValidationIssue(
                        "cohort.duplicate_value",
                        f"sources.{self.source_id}.{field_name}",
                        "values must be unique",
                    )
                )
        if not self.output_ids:
            issues.append(
                ValidationIssue(
                    "cohort.output_required",
                    f"sources.{self.source_id}.output_ids",
                    "at least one durable output is required",
                )
            )
        if issues:
            raise ManifestValidationError(issues)


__all__ = [
    "ALL_RUN_MODES",
    "DependencyDeclaration",
    "ExecutionNode",
    "ExecutionPlan",
    "ManifestNode",
    "RunMode",
    "SkipExplanation",
    "WorkflowManifest",
]
