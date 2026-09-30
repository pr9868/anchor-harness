"""Immutable bounded work and exact resume identities."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any
from .canonical import canonical_digest

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


_ID = re.compile(r"^[a-z][a-z0-9_.:-]{0,199}$")


def _require_digest(value: str, label: str) -> None:
    if not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase sha256 digest")


def _require_id(value: str, label: str) -> None:
    if not _ID.fullmatch(value):
        raise ValueError(f"{label} must be a stable contract identifier")


def _integer(value: int, label: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")


@dataclass(frozen=True, slots=True)
class WorkEstimate:
    input_characters: int = 1_000
    input_tokens: int = 250
    output_tokens: int = 125
    expected_seconds: int = 1

    def __post_init__(self) -> None:
        for name in ("input_characters", "input_tokens", "output_tokens", "expected_seconds"):
            _integer(getattr(self, name), name)
        if min(self.input_characters, self.input_tokens, self.output_tokens, self.expected_seconds) < 0:
            raise ValueError("work estimates cannot be negative")


@dataclass(frozen=True, slots=True)
class CohortBudget:
    max_items: int = 20
    max_input_characters: int = 125_000
    max_input_tokens: int = 30_000
    max_expected_seconds: int = 900

    def __post_init__(self) -> None:
        for name in ("max_items", "max_input_characters", "max_input_tokens", "max_expected_seconds"):
            _integer(getattr(self, name), name, 1)
        if min(
            self.max_items,
            self.max_input_characters,
            self.max_input_tokens,
            self.max_expected_seconds,
        ) <= 0:
            raise ValueError("every cohort budget must be positive")


@dataclass(frozen=True, slots=True)
class WorkUnit:
    work_id: str
    stage: str
    object_id: str
    input_digest: str
    estimate: WorkEstimate

    def __post_init__(self) -> None:
        _require_id(self.stage, "stage")
        _require_id(self.work_id, "work_id")
        _require_id(self.object_id, "work object id")
        _require_digest(self.input_digest, "work input digest")

    def identity_value(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "stage": self.stage,
            "object_id": self.object_id,
            "input_digest": self.input_digest,
            "estimate": {
                "input_characters": self.estimate.input_characters,
                "input_tokens": self.estimate.input_tokens,
                "output_tokens": self.estimate.output_tokens,
                "expected_seconds": self.estimate.expected_seconds,
            },
        }


@dataclass(frozen=True, slots=True)
class Cohort:
    cohort_id: str
    stage: str
    ordinal: int
    work_units: tuple[WorkUnit, ...]
    input_characters: int
    input_tokens: int
    output_tokens: int
    expected_seconds: int
    cohort_digest: str

    def __post_init__(self) -> None:
        _integer(self.ordinal, "ordinal")
        _require_id(self.stage, "stage")
        if len({item.work_id for item in self.work_units}) != len(self.work_units):
            raise ValueError("cohort work identifiers must be unique")
        _require_id(self.cohort_id, "cohort_id")
        _require_digest(self.cohort_digest, "cohort_digest")
        if not self.work_units or any(item.stage != self.stage for item in self.work_units):
            raise ValueError("a cohort must contain work from exactly one stage")
        totals = (
            sum(item.estimate.input_characters for item in self.work_units),
            sum(item.estimate.input_tokens for item in self.work_units),
            sum(item.estimate.output_tokens for item in self.work_units),
            sum(item.estimate.expected_seconds for item in self.work_units),
        )
        if totals != (
            self.input_characters,
            self.input_tokens,
            self.output_tokens,
            self.expected_seconds,
        ):
            raise ValueError("cohort resource totals do not match its immutable work units")
        expected = canonical_digest(
            {
                "schema": "anchor.cohort/1.0",
                "stage": self.stage,
                "ordinal": self.ordinal,
                "work_units": [item.identity_value() for item in self.work_units],
            }
        )
        if self.cohort_digest != expected or self.cohort_id != f"cohort.{self.stage}.{expected[:16]}":
            raise ValueError("cohort identity does not match its canonical allocation")

    def identity_value(self, *, include_digest: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "cohort_id": self.cohort_id,
            "stage": self.stage,
            "ordinal": self.ordinal,
            "work_units": [item.identity_value() for item in self.work_units],
            "input_characters": self.input_characters,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "expected_seconds": self.expected_seconds,
        }
        if include_digest:
            value["cohort_digest"] = self.cohort_digest
        return value


@dataclass(frozen=True, slots=True)
class CohortManifest:
    plan_digest: str
    budget: CohortBudget
    cohorts: tuple[Cohort, ...]
    manifest_digest: str

    def __post_init__(self) -> None:
        _require_digest(self.plan_digest, "plan_digest")
        _require_digest(self.manifest_digest, "manifest_digest")
        ids = tuple(item.cohort_id for item in self.cohorts)
        if len(ids) != len(set(ids)):
            raise ValueError("cohort identifiers must be unique")
        work_ids = [unit.work_id for cohort in self.cohorts for unit in cohort.work_units]
        if len(work_ids) != len(set(work_ids)):
            raise ValueError("manifest work identifiers must be unique")
        if tuple(item.ordinal for item in self.cohorts) != tuple(range(len(self.cohorts))):
            raise ValueError("cohort ordinals must be contiguous and ordered")
        for cohort in self.cohorts:
            if (len(cohort.work_units) > self.budget.max_items
                    or cohort.input_characters > self.budget.max_input_characters
                    or cohort.input_tokens > self.budget.max_input_tokens
                    or cohort.expected_seconds > self.budget.max_expected_seconds):
                raise ValueError("cohort exceeds manifest budget")
        unsigned = {
            "schema": "anchor.cohort-manifest/1.0",
            "plan_digest": self.plan_digest,
            "budget": {
                "max_items": self.budget.max_items,
                "max_input_characters": self.budget.max_input_characters,
                "max_input_tokens": self.budget.max_input_tokens,
                "max_expected_seconds": self.budget.max_expected_seconds,
            },
            "cohorts": [item.identity_value() for item in self.cohorts],
        }
        if self.manifest_digest != canonical_digest(unsigned):
            raise ValueError("cohort manifest digest does not match its canonical allocation")


class CohortStatus(StrEnum):
    SEALED_VALID = "sealed-valid"
    FAILED = "failed"
    TIMED_OUT = "timed-out"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class CohortAttemptIdentity:
    attempt_id: str
    plan_digest: str
    manifest_digest: str
    cohort_id: str
    cohort_digest: str
    attempt_number: int
    provider_adapter_digest: str
    runtime_capability_digest: str

    def __post_init__(self) -> None:
        _integer(self.attempt_number, "attempt_number", 1)
        _require_id(self.attempt_id, "attempt_id")
        for label, value in (
            ("plan_digest", self.plan_digest),
            ("manifest_digest", self.manifest_digest),
            ("cohort_digest", self.cohort_digest),
            ("provider_adapter_digest", self.provider_adapter_digest),
            ("runtime_capability_digest", self.runtime_capability_digest),
        ):
            _require_digest(value, label)
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        value = {
            "schema": "anchor.cohort-attempt/1.0",
            "plan_digest": self.plan_digest,
            "manifest_digest": self.manifest_digest,
            "cohort_id": self.cohort_id,
            "cohort_digest": self.cohort_digest,
            "attempt_number": self.attempt_number,
            "provider_adapter_digest": self.provider_adapter_digest,
            "runtime_capability_digest": self.runtime_capability_digest,
        }
        if self.attempt_id != f"attempt.{canonical_digest(value)}":
            raise ValueError("attempt identity does not match its canonical binding")


@dataclass(frozen=True, slots=True)
class CohortResponseBinding:
    attempt: CohortAttemptIdentity
    status: CohortStatus
    response_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CohortStatus):
            raise ValueError("status must be a CohortStatus")
        if self.status is CohortStatus.SEALED_VALID:
            if self.response_digest is None:
                raise ValueError("a sealed valid cohort requires a response digest")
            _require_digest(self.response_digest, "response_digest")
        elif self.response_digest is not None:
            _require_digest(self.response_digest, "response_digest")


@dataclass(frozen=True, slots=True)
class ResumePlan:
    manifest_digest: str
    reusable: tuple[CohortResponseBinding, ...]
    pending_cohort_ids: tuple[str, ...]
    next_attempt_numbers: tuple[tuple[str, int], ...]
    resume_digest: str
