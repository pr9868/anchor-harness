"""Deterministic bounded cohort allocation and exact resume identity."""

from __future__ import annotations

from collections import defaultdict

from .canonical import canonical_digest

from .work import (
    Cohort,
    CohortAttemptIdentity,
    CohortBudget,
    CohortManifest,
    CohortResponseBinding,
    CohortStatus,
    ResumePlan,
    WorkUnit,
)


def _fits(items: tuple[WorkUnit, ...], budget: CohortBudget) -> bool:
    return (
        len(items) <= budget.max_items
        and sum(item.estimate.input_characters for item in items) <= budget.max_input_characters
        and sum(item.estimate.input_tokens for item in items) <= budget.max_input_tokens
        and sum(item.estimate.expected_seconds for item in items) <= budget.max_expected_seconds
    )


def build_cohort_manifest(
    *, plan_digest: str, work_units: tuple[WorkUnit, ...], budget: CohortBudget
) -> CohortManifest:
    """Greedily allocate stable stage-local cohorts under every hard budget."""

    if len({item.work_id for item in work_units}) != len(work_units):
        raise ValueError("work unit identifiers must be unique")
    by_stage: dict[object, list[WorkUnit]] = defaultdict(list)
    for item in work_units:
        if not _fits((item,), budget):
            raise ValueError(f"work unit {item.work_id!r} exceeds the fixed cohort budget")
        by_stage[item.stage].append(item)
    cohorts: list[Cohort] = []
    ordinal = 0
    for stage in sorted(by_stage):
        pending: list[WorkUnit] = []
        for item in sorted(by_stage.get(stage, ()), key=lambda candidate: candidate.work_id):
            proposed = tuple(pending + [item])
            if pending and not _fits(proposed, budget):
                cohorts.append(_make_cohort(stage, ordinal, tuple(pending)))
                ordinal += 1
                pending = [item]
            else:
                pending.append(item)
        if pending:
            cohorts.append(_make_cohort(stage, ordinal, tuple(pending)))
            ordinal += 1
    unsigned = {
        "schema": "anchor.cohort-manifest/1.0",
        "plan_digest": plan_digest,
        "budget": {
            "max_items": budget.max_items,
            "max_input_characters": budget.max_input_characters,
            "max_input_tokens": budget.max_input_tokens,
            "max_expected_seconds": budget.max_expected_seconds,
        },
        "cohorts": [item.identity_value() for item in cohorts],
    }
    return CohortManifest(
        plan_digest=plan_digest,
        budget=budget,
        cohorts=tuple(cohorts),
        manifest_digest=canonical_digest(unsigned),
    )


def _make_cohort(stage, ordinal: int, items: tuple[WorkUnit, ...]) -> Cohort:
    unsigned = {
        "schema": "anchor.cohort/1.0",
        "stage": stage,
        "ordinal": ordinal,
        "work_units": [item.identity_value() for item in items],
    }
    digest = canonical_digest(unsigned)
    return Cohort(
        cohort_id=f"cohort.{stage}.{digest[:16]}",
        stage=stage,
        ordinal=ordinal,
        work_units=items,
        input_characters=sum(item.estimate.input_characters for item in items),
        input_tokens=sum(item.estimate.input_tokens for item in items),
        output_tokens=sum(item.estimate.output_tokens for item in items),
        expected_seconds=sum(item.estimate.expected_seconds for item in items),
        cohort_digest=digest,
    )


def allocate_attempt(
    manifest: CohortManifest,
    *,
    cohort_id: str,
    attempt_number: int,
    provider_adapter_digest: str,
    runtime_capability_digest: str,
) -> CohortAttemptIdentity:
    cohort = next((item for item in manifest.cohorts if item.cohort_id == cohort_id), None)
    if cohort is None:
        raise ValueError("cannot allocate an attempt outside the cohort manifest")
    value = {
        "schema": "anchor.cohort-attempt/1.0",
        "plan_digest": manifest.plan_digest,
        "manifest_digest": manifest.manifest_digest,
        "cohort_id": cohort.cohort_id,
        "cohort_digest": cohort.cohort_digest,
        "attempt_number": attempt_number,
        "provider_adapter_digest": provider_adapter_digest,
        "runtime_capability_digest": runtime_capability_digest,
    }
    return CohortAttemptIdentity(
        attempt_id=f"attempt.{canonical_digest(value)}",
        plan_digest=manifest.plan_digest,
        manifest_digest=manifest.manifest_digest,
        cohort_id=cohort.cohort_id,
        cohort_digest=cohort.cohort_digest,
        attempt_number=attempt_number,
        provider_adapter_digest=provider_adapter_digest,
        runtime_capability_digest=runtime_capability_digest,
    )


def plan_resume(
    manifest: CohortManifest,
    responses: tuple[CohortResponseBinding, ...],
    *,
    provider_adapter_digest: str,
    runtime_capability_digest: str,
) -> ResumePlan:
    """Reuse exact sealed cohorts and schedule only absent/failed/timed-out cohorts.

    Any plan, allocation, provider-adaptation, or Runtime-capability drift is rejected rather than
    silently converted into cross-run reuse.
    """

    known = {item.cohort_id: item for item in manifest.cohorts}
    seen_attempt_ids: set[str] = set()
    by_cohort: dict[str, list[CohortResponseBinding]] = defaultdict(list)
    for response in responses:
        attempt = response.attempt
        if attempt.attempt_id in seen_attempt_ids:
            raise ValueError("cohort attempt bindings must be unique")
        seen_attempt_ids.add(attempt.attempt_id)
        cohort = known.get(attempt.cohort_id)
        if (
            cohort is None
            or attempt.plan_digest != manifest.plan_digest
            or attempt.manifest_digest != manifest.manifest_digest
            or attempt.cohort_digest != cohort.cohort_digest
        ):
            raise ValueError("response binding does not belong to this immutable cohort manifest")
        if (
            attempt.provider_adapter_digest != provider_adapter_digest
            or attempt.runtime_capability_digest != runtime_capability_digest
        ):
            raise ValueError("provider or Runtime drift requires a new plan; response reuse is forbidden")
        by_cohort[attempt.cohort_id].append(response)

    reusable: list[CohortResponseBinding] = []
    pending: list[str] = []
    next_numbers: list[tuple[str, int]] = []
    for cohort in manifest.cohorts:
        attempts = by_cohort.get(cohort.cohort_id, [])
        sealed = [item for item in attempts if item.status is CohortStatus.SEALED_VALID]
        if len(sealed) > 1:
            identities = {(item.attempt.attempt_id, item.response_digest) for item in sealed}
            if len(identities) > 1:
                raise ValueError("multiple sealed responses make cohort selection ambiguous")
        if sealed:
            sealed_number = sealed[0].attempt.attempt_number
            if any(item.attempt.attempt_number > sealed_number for item in attempts):
                raise ValueError("a cohort has an attempt allocated after a sealed valid response")
            reusable.append(sealed[0])
            continue
        pending.append(cohort.cohort_id)
        next_numbers.append(
            (cohort.cohort_id, max((item.attempt.attempt_number for item in attempts), default=0) + 1)
        )
    value = {
        "schema": "anchor.resume-plan/1.0",
        "manifest_digest": manifest.manifest_digest,
        "reusable": [
            {
                "attempt_id": item.attempt.attempt_id,
                "cohort_id": item.attempt.cohort_id,
                "response_digest": item.response_digest,
            }
            for item in reusable
        ],
        "pending_cohort_ids": pending,
        "next_attempt_numbers": [[key, number] for key, number in next_numbers],
    }
    return ResumePlan(
        manifest_digest=manifest.manifest_digest,
        reusable=tuple(reusable),
        pending_cohort_ids=tuple(pending),
        next_attempt_numbers=tuple(next_numbers),
        resume_digest=canonical_digest(value),
    )


__all__ = ["allocate_attempt", "build_cohort_manifest", "plan_resume"]
