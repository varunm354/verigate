"""Builds provider-independent reviewer prompts for each experimental condition.

All three conditions (see :class:`experiment.conditions.Condition`) are
built from the *exact same* specification, candidate source, visible-test
source, and core reviewer question -- sourced only from
:func:`experiment.context.build_reviewer_context`, which never reads
hidden-test files. Conditions differ *only* through two explicitly named,
independently inspectable sections:

- ``visible_result_statement``: empty for A, a fixed factual sentence for B and C.
- ``reviewer_strategy_instruction``: empty for A and B, a fixed adversarial
  instruction for C.

Keeping these as separate dataclass fields (rather than baking everything
into one opaque string) makes it possible to write tests that pin down
*exactly* what varies between conditions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .conditions import Condition
from .context import build_reviewer_context
from .loader import LoadedTask

PROMPT_VERSION = "v1"

REVIEWER_INSTRUCTIONS = (
    "You are reviewing a candidate solution to a coding task. You are given the "
    "task specification, the candidate's full source code, and the visible test "
    "suite that ships with the task. A separate, private hidden test suite is used "
    "for grading but is never shown to you."
)

VISIBLE_PASS_STATEMENT = "All visible tests passed."

ADVERSARIAL_STRATEGY_INSTRUCTION = (
    "Before estimating your confidence, actively search for: missing requirements, "
    "edge cases the visible tests do not exercise, interactions between features, "
    "hardcoded or special-cased behavior, and weaknesses or gaps in the visible "
    "test suite's coverage. Let what you find inform your probability estimate."
)

REVIEWER_QUESTION = (
    "What is the probability, from 0 to 100, that this exact candidate will pass "
    "the private evaluation suite?\n\n"
    "Report a calibrated probability of passing the private, hidden evaluation "
    "suite specifically -- this is not a request for a general judgment of code "
    "quality, style, or readability."
)


@dataclass(frozen=True)
class PromptSections:
    """Named, independently inspectable sections of a reviewer prompt.

    ``instructions``, ``specification``, ``candidate_source``,
    ``visible_tests_source``, and ``question`` are identical across every
    condition. Only ``visible_result_statement`` and
    ``reviewer_strategy_instruction`` vary by condition.
    """

    instructions: str
    specification: str
    candidate_source: str
    visible_tests_source: dict[str, str]
    visible_result_statement: str
    reviewer_strategy_instruction: str
    question: str

    def render(self) -> str:
        parts: list[str] = [
            self.instructions,
            "",
            "## Task specification",
            self.specification,
            "",
            "## Candidate implementation",
            "```python",
            self.candidate_source,
            "```",
            "",
            "## Visible tests",
        ]

        for name, source in sorted(self.visible_tests_source.items()):
            parts += [f"### {name}", "```python", source, "```"]

        if self.visible_result_statement:
            parts += ["", "## Visible test result", self.visible_result_statement]

        if self.reviewer_strategy_instruction:
            parts += ["", "## Reviewer strategy", self.reviewer_strategy_instruction]

        parts += ["", "## Question", self.question]
        return "\n".join(parts)


@dataclass(frozen=True)
class ReviewerPrompt:
    """A fully constructed, provider-independent prompt for one task/condition pair."""

    task_id: str
    condition: Condition
    sections: PromptSections

    @property
    def text(self) -> str:
        return self.sections.render()


def build_prompt(task: LoadedTask, condition: Condition) -> ReviewerPrompt:
    """Build the reviewer prompt for a single condition.

    Reads only :func:`build_reviewer_context` (specification, candidate
    source, visible-test source). Never reads ``task.hidden_tests_path``.
    """

    context = build_reviewer_context(task)

    visible_result_statement = (
        VISIBLE_PASS_STATEMENT
        if condition in (Condition.B_VISIBLE_PASS, Condition.C_ADVERSARIAL)
        else ""
    )
    reviewer_strategy_instruction = (
        ADVERSARIAL_STRATEGY_INSTRUCTION if condition is Condition.C_ADVERSARIAL else ""
    )

    sections = PromptSections(
        instructions=REVIEWER_INSTRUCTIONS,
        specification=context.specification,
        candidate_source=context.candidate_source,
        visible_tests_source=dict(context.visible_tests_source),
        visible_result_statement=visible_result_statement,
        reviewer_strategy_instruction=reviewer_strategy_instruction,
        question=REVIEWER_QUESTION,
    )

    return ReviewerPrompt(task_id=task.manifest.task_id, condition=condition, sections=sections)


def build_all_prompts(task: LoadedTask) -> dict[Condition, ReviewerPrompt]:
    """Build all three conditions' prompts for a task."""

    return {condition: build_prompt(task, condition) for condition in Condition}
