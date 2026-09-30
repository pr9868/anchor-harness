"""Public validation errors for Anchor compiler."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class ValidationIssue:
    """One deterministic, machine-readable manifest validation issue."""

    code: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} at {self.location}: {self.message}"


class ManifestValidationError(ValueError):
    """Raised when a workflow manifest violates one or more Anchor contracts."""

    def __init__(self, issues: list[ValidationIssue] | tuple[ValidationIssue, ...]):
        ordered = tuple(sorted(issues))
        self.issues = ordered
        summary = "; ".join(str(issue) for issue in ordered)
        super().__init__(summary or "workflow manifest is invalid")


class CohortValidationError(ValueError):
    """Raised when commit-cohort declarations cannot be split safely."""
