"""Tests for the experiment CLI's ``prompts`` and ``review`` commands.

The existing ``run`` command is covered indirectly by test_experiment.py
via the underlying loader/runner/context calls it wraps.
"""

from __future__ import annotations

import pytest

from experiment.cli import run_prompts, run_review
from experiment.conditions import Condition

TASK_ID = "expression_evaluator"
HIDDEN_SENTINEL = "VERIGATE_HIDDEN_TEST_SENTINEL_7f3c1a"


def test_run_prompts_returns_all_three_conditions_with_no_hidden_leakage():
    result = run_prompts(TASK_ID)

    assert result["task_id"] == TASK_ID
    assert set(result["conditions"]) == {c.value for c in Condition}

    serialized = str(result)
    assert HIDDEN_SENTINEL not in serialized
    assert "hidden_tests" not in serialized


def test_run_prompts_conditions_differ_only_as_expected():
    result = run_prompts(TASK_ID)
    conditions = result["conditions"]

    assert conditions["A_NO_RESULT"]["visible_result_statement"] == ""
    assert conditions["A_NO_RESULT"]["reviewer_strategy_instruction"] == ""

    assert conditions["B_VISIBLE_PASS"]["visible_result_statement"] != ""
    assert conditions["B_VISIBLE_PASS"]["reviewer_strategy_instruction"] == ""

    assert (
        conditions["C_ADVERSARIAL"]["visible_result_statement"]
        == conditions["B_VISIBLE_PASS"]["visible_result_statement"]
    )
    assert conditions["C_ADVERSARIAL"]["reviewer_strategy_instruction"] != ""


@pytest.mark.parametrize(
    "condition", [Condition.A_NO_RESULT, Condition.B_VISIBLE_PASS, Condition.C_ADVERSARIAL]
)
def test_run_review_with_mock_provider_produces_a_valid_result(condition: Condition):
    result = run_review(TASK_ID, condition, "mock")

    assert result["task_id"] == TASK_ID
    assert result["condition"] == condition.value
    assert result["provider"] == "mock"
    assert result["prompt_version"]
    assert 0 <= result["assessment"]["confidence"] <= 100
    assert "timestamp" in result and result["timestamp"]
    assert result["latency_seconds"] is None or result["latency_seconds"] >= 0
    assert HIDDEN_SENTINEL not in str(result)


def test_run_review_with_unknown_provider_raises_value_error():
    # NOTE: must be a provider name that is *not* registered (as of
    # Milestone 4, "openai" is a real, registered provider that would
    # attempt a real network call here -- see test_openai_reviewer.py for
    # OpenAI-specific tests, all of which use a fake client).
    with pytest.raises(ValueError):
        run_review(TASK_ID, Condition.A_NO_RESULT, "not-a-real-provider")
