from __future__ import annotations

import pytest

from anchor_harness import (
    CohortBudget,
    CohortResponseBinding,
    CohortStatus,
    WorkEstimate,
    WorkUnit,
    allocate_attempt,
    build_cohort_manifest,
    plan_resume,
)


def work(index: int, *, characters: int = 100, tokens: int = 25) -> WorkUnit:
    return WorkUnit(
        work_id=f"work.source-knowledge.item-{index:03d}",
        stage="transform",
        object_id=f"evidence.item-{index:03d}",
        input_digest=f"{index % 10}" * 64,
        estimate=WorkEstimate(characters, tokens, 10, 2),
    )


def test_100_plus_items_are_bounded_and_deterministic() -> None:
    items = tuple(work(index) for index in range(107))
    budget = CohortBudget(max_items=10, max_input_characters=1_000, max_input_tokens=250, max_expected_seconds=20)
    first = build_cohort_manifest(plan_digest="a" * 64, work_units=items, budget=budget)
    second = build_cohort_manifest(
        plan_digest="a" * 64, work_units=tuple(reversed(items)), budget=budget
    )
    assert len(first.cohorts) == 11
    assert first.manifest_digest == second.manifest_digest
    assert [item.cohort_id for item in first.cohorts] == [item.cohort_id for item in second.cohorts]
    assert all(len(item.work_units) <= 10 for item in first.cohorts)
    assert all(item.input_characters <= 1_000 for item in first.cohorts)
    assert all(item.input_tokens <= 250 for item in first.cohorts)
    assert all(item.expected_seconds <= 20 for item in first.cohorts)


def test_one_oversize_unit_fails_before_allocation() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        build_cohort_manifest(
            plan_digest="a" * 64,
            work_units=(work(1, characters=1_001),),
            budget=CohortBudget(max_input_characters=1_000),
        )


def test_timeout_resume_reuses_valid_cohorts_and_only_retries_incomplete() -> None:
    manifest = build_cohort_manifest(
        plan_digest="a" * 64,
        work_units=tuple(work(index) for index in range(5)),
        budget=CohortBudget(max_items=2),
    )
    adapter = "b" * 64
    runtime = "c" * 64
    first = allocate_attempt(
        manifest,
        cohort_id=manifest.cohorts[0].cohort_id,
        attempt_number=1,
        provider_adapter_digest=adapter,
        runtime_capability_digest=runtime,
    )
    second = allocate_attempt(
        manifest,
        cohort_id=manifest.cohorts[1].cohort_id,
        attempt_number=1,
        provider_adapter_digest=adapter,
        runtime_capability_digest=runtime,
    )
    resume = plan_resume(
        manifest,
        (
            CohortResponseBinding(first, CohortStatus.SEALED_VALID, "d" * 64),
            CohortResponseBinding(second, CohortStatus.TIMED_OUT),
        ),
        provider_adapter_digest=adapter,
        runtime_capability_digest=runtime,
    )
    assert [item.attempt.cohort_id for item in resume.reusable] == [manifest.cohorts[0].cohort_id]
    assert resume.pending_cohort_ids == (
        manifest.cohorts[1].cohort_id,
        manifest.cohorts[2].cohort_id,
    )
    assert resume.next_attempt_numbers == (
        (manifest.cohorts[1].cohort_id, 2),
        (manifest.cohorts[2].cohort_id, 1),
    )
    retry = allocate_attempt(
        manifest,
        cohort_id=manifest.cohorts[1].cohort_id,
        attempt_number=2,
        provider_adapter_digest=adapter,
        runtime_capability_digest=runtime,
    )
    assert retry.cohort_digest == second.cohort_digest
    assert retry.attempt_id != second.attempt_id


def test_resume_rejects_cross_plan_or_adapter_drift() -> None:
    manifest = build_cohort_manifest(
        plan_digest="a" * 64,
        work_units=(work(1),),
        budget=CohortBudget(),
    )
    attempt = allocate_attempt(
        manifest,
        cohort_id=manifest.cohorts[0].cohort_id,
        attempt_number=1,
        provider_adapter_digest="b" * 64,
        runtime_capability_digest="c" * 64,
    )
    response = CohortResponseBinding(attempt, CohortStatus.SEALED_VALID, "d" * 64)
    with pytest.raises(ValueError, match="drift"):
        plan_resume(
            manifest,
            (response,),
            provider_adapter_digest="e" * 64,
            runtime_capability_digest="c" * 64,
        )


def test_duplicate_work_identity_is_rejected() -> None:
    item = work(1)
    with pytest.raises(ValueError, match="unique"):
        build_cohort_manifest(
            plan_digest="a" * 64,
            work_units=(item, item),
            budget=CohortBudget(),
        )


def test_attempt_identity_cannot_be_forged() -> None:
    from anchor_harness import CohortAttemptIdentity

    with pytest.raises(ValueError, match="canonical binding"):
        CohortAttemptIdentity(
            attempt_id="attempt." + "f" * 64,
            plan_digest="a" * 64,
            manifest_digest="b" * 64,
            cohort_id="cohort.source-knowledge.1234567890abcdef",
            cohort_digest="c" * 64,
            attempt_number=1,
            provider_adapter_digest="d" * 64,
            runtime_capability_digest="e" * 64,
        )
