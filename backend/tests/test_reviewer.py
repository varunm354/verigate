"""Tests for experiment.models (reviewer schemas) and experiment.reviewer (MockReviewer)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from experiment.conditions import Condition
from experiment.loader import TaskLoader
from experiment.models import ReviewerAssessment, ReviewerResult
from experiment.prompts import build_prompt
from experiment.reviewer import MockReviewer, Reviewer

TASK_ID = "expression_evaluator"

ALL_CONDITIONS = [Condition.A_NO_RESULT, Condition.B_VISIBLE_PASS, Condition.C_ADVERSARIAL]


def _assessment(**overrides: object) -> ReviewerAssessment:
    # confidence=50 / predicted_pass=True is internally consistent (50 >= 50),
    # so this remains a valid default even after the consistency validator.
    defaults: dict[str, object] = dict(
        condition=Condition.A_NO_RESULT,
        predicted_pass=True,
        confidence=50,
        suspected_issues=[],
        rationale="test rationale",
    )
    defaults.update(overrides)
    return ReviewerAssessment(**defaults)  # type: ignore[arg-type]


def _result(**overrides: object) -> ReviewerResult:
    defaults: dict[str, object] = dict(
        task_id=TASK_ID,
        condition=Condition.A_NO_RESULT,
        assessment=_assessment(),
        prompt_version="v1",
        provider="mock",
        model="mock-deterministic-v1",
        timestamp=datetime.now(timezone.utc),
        latency_seconds=0.01,
    )
    defaults.update(overrides)
    return ReviewerResult(**defaults)  # type: ignore[arg-type]


def test_confidence_at_the_boundaries_is_valid_with_consistent_predicted_pass():
    assert _assessment(confidence=0, predicted_pass=False).confidence == 0
    assert _assessment(confidence=100, predicted_pass=True).confidence == 100


@pytest.mark.parametrize("bad_confidence", [-1, 101, -50, 1000])
def test_confidence_out_of_bounds_fails_validation(bad_confidence: int):
    with pytest.raises(ValidationError):
        _assessment(confidence=bad_confidence)


@pytest.mark.parametrize(
    "confidence,predicted_pass",
    [
        (0, False),
        (1, False),
        (49, False),
        (50, True),  # exact boundary: >= 50 must be True
        (51, True),
        (99, True),
        (100, True),
    ],
)
def test_consistent_predicted_pass_confidence_combinations_are_valid(
    confidence: int, predicted_pass: bool
):
    assessment = _assessment(confidence=confidence, predicted_pass=predicted_pass)
    assert assessment.confidence == confidence
    assert assessment.predicted_pass is predicted_pass


@pytest.mark.parametrize(
    "confidence,predicted_pass",
    [
        (0, True),
        (1, True),
        (49, True),  # exact boundary just below 50: True is inconsistent
        (50, False),  # exact boundary at 50: False is inconsistent
        (51, False),
        (100, False),
    ],
)
def test_inconsistent_predicted_pass_confidence_combinations_fail_validation(
    confidence: int, predicted_pass: bool
):
    with pytest.raises(ValidationError):
        _assessment(confidence=confidence, predicted_pass=predicted_pass)


def test_mock_reviewer_satisfies_the_reviewer_protocol():
    reviewer = MockReviewer()
    assert isinstance(reviewer, Reviewer)
    assert isinstance(reviewer.provider, str) and reviewer.provider
    assert isinstance(reviewer.model, str) and reviewer.model


@pytest.mark.parametrize("condition", ALL_CONDITIONS)
def test_mock_reviewer_returns_a_valid_assessment_for_every_condition(condition: Condition):
    task = TaskLoader().load(TASK_ID)
    prompt = build_prompt(task, condition)
    reviewer = MockReviewer()

    assessment = reviewer.review(condition, prompt)

    assert isinstance(assessment, ReviewerAssessment)
    assert assessment.condition == condition
    assert 0 <= assessment.confidence <= 100
    assert isinstance(assessment.predicted_pass, bool)
    assert isinstance(assessment.suspected_issues, list)
    assert isinstance(assessment.rationale, str) and assessment.rationale.strip()


def test_mock_reviewer_rejects_a_prompt_condition_mismatch():
    task = TaskLoader().load(TASK_ID)
    prompt_for_a = build_prompt(task, Condition.A_NO_RESULT)
    reviewer = MockReviewer()

    with pytest.raises(ValueError):
        reviewer.review(Condition.B_VISIBLE_PASS, prompt_for_a)


def test_reviewer_result_timestamp_is_timezone_aware_utc():
    result = _result(timestamp=datetime.now(timezone.utc))

    assert result.timestamp.tzinfo is not None
    assert result.timestamp.utcoffset() == timedelta(0)


def test_reviewer_result_rejects_naive_timestamp():
    naive_now = datetime.now()  # no tzinfo
    assert naive_now.tzinfo is None  # sanity

    with pytest.raises(ValidationError):
        _result(timestamp=naive_now)


def test_reviewer_result_normalizes_non_utc_timezone_to_utc():
    eastern = timezone(timedelta(hours=-5))
    aware_but_not_utc = datetime(2026, 1, 1, 12, 0, 0, tzinfo=eastern)

    result = _result(timestamp=aware_but_not_utc)

    assert result.timestamp.tzinfo == timezone.utc
    assert result.timestamp == aware_but_not_utc  # same instant, normalized representation
