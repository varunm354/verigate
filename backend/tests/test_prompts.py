"""Tests for experiment.prompts: condition construction and hidden-content leakage.

Uses the real ``expression_evaluator`` task. All assertions here are
about the *prompt* layer specifically -- the reviewer-context leakage
tests in ``test_experiment.py`` cover ``build_reviewer_context`` itself.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from experiment.conditions import Condition
from experiment.loader import LoadedTask, TaskLoader
from experiment.prompts import (
    ADVERSARIAL_STRATEGY_INSTRUCTION,
    VISIBLE_PASS_STATEMENT,
    build_all_prompts,
)

TASK_ID = "expression_evaluator"
HIDDEN_SENTINEL = "VERIGATE_HIDDEN_TEST_SENTINEL_7f3c1a"


@pytest.fixture(scope="module")
def task() -> LoadedTask:
    return TaskLoader().load(TASK_ID)


@pytest.fixture(scope="module")
def prompts(task: LoadedTask):
    return build_all_prompts(task)


def test_all_three_conditions_are_built(prompts):
    assert set(prompts) == {
        Condition.A_NO_RESULT,
        Condition.B_VISIBLE_PASS,
        Condition.C_ADVERSARIAL,
    }


def test_shared_sections_are_identical_across_all_conditions(prompts):
    a = prompts[Condition.A_NO_RESULT]
    b = prompts[Condition.B_VISIBLE_PASS]
    c = prompts[Condition.C_ADVERSARIAL]

    for other in (b, c):
        assert other.task_id == a.task_id
        assert other.sections.instructions == a.sections.instructions
        assert other.sections.specification == a.sections.specification
        assert other.sections.candidate_source == a.sections.candidate_source
        assert other.sections.visible_tests_source == a.sections.visible_tests_source
        assert other.sections.question == a.sections.question

    # Sanity: the shared content is real, not accidentally empty.
    assert a.sections.specification.strip()
    assert a.sections.candidate_source.strip()
    assert a.sections.visible_tests_source


def test_no_prompt_contains_hidden_test_content(task: LoadedTask, prompts):
    hidden_filenames = {p.name for p in task.hidden_tests_path.glob("*.py")}
    assert hidden_filenames  # sanity: hidden tests actually exist on disk

    for condition, prompt in prompts.items():
        text = prompt.text
        assert HIDDEN_SENTINEL not in text, condition
        assert "hidden_tests" not in text, condition
        for filename in hidden_filenames:
            assert filename not in text, (condition, filename)


def test_condition_a_makes_no_claim_that_tests_passed(prompts):
    prompt = prompts[Condition.A_NO_RESULT]
    assert prompt.sections.visible_result_statement == ""
    assert VISIBLE_PASS_STATEMENT not in prompt.text
    assert "## Visible test result" not in prompt.text
    assert prompt.sections.reviewer_strategy_instruction == ""
    assert "## Reviewer strategy" not in prompt.text


def test_condition_b_contains_the_visible_pass_statement(prompts):
    prompt = prompts[Condition.B_VISIBLE_PASS]
    assert prompt.sections.visible_result_statement == VISIBLE_PASS_STATEMENT
    assert VISIBLE_PASS_STATEMENT in prompt.text
    assert "## Visible test result" in prompt.text
    # B must not include the adversarial strategy instruction.
    assert prompt.sections.reviewer_strategy_instruction == ""
    assert ADVERSARIAL_STRATEGY_INSTRUCTION not in prompt.text


def test_condition_c_contains_same_pass_statement_plus_adversarial_instruction(prompts):
    prompt_b = prompts[Condition.B_VISIBLE_PASS]
    prompt_c = prompts[Condition.C_ADVERSARIAL]

    # Exact same visible-pass statement as B.
    assert prompt_c.sections.visible_result_statement == prompt_b.sections.visible_result_statement
    assert prompt_c.sections.visible_result_statement == VISIBLE_PASS_STATEMENT

    # Plus the adversarial instruction, which B does not have.
    assert prompt_c.sections.reviewer_strategy_instruction == ADVERSARIAL_STRATEGY_INSTRUCTION
    assert ADVERSARIAL_STRATEGY_INSTRUCTION in prompt_c.text
    assert "## Reviewer strategy" in prompt_c.text


def test_b_differs_from_a_only_via_the_visible_result_section(prompts):
    a_sections = prompts[Condition.A_NO_RESULT].sections
    b_sections = prompts[Condition.B_VISIBLE_PASS].sections

    assert a_sections != b_sections  # they must differ somewhere...
    # ...and zeroing out exactly the visible-result section closes the gap.
    b_without_result_section = replace(b_sections, visible_result_statement="")
    assert b_without_result_section == a_sections


def test_c_differs_from_b_only_via_the_reviewer_strategy_section(prompts):
    b_sections = prompts[Condition.B_VISIBLE_PASS].sections
    c_sections = prompts[Condition.C_ADVERSARIAL].sections

    assert b_sections != c_sections  # they must differ somewhere...
    # ...and zeroing out exactly the reviewer-strategy section closes the gap.
    c_without_strategy_section = replace(c_sections, reviewer_strategy_instruction="")
    assert c_without_strategy_section == b_sections
