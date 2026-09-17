"""Provider-independent reviewer interface.

Defines the typed protocol that any reviewer backend (mock, OpenAI,
Anthropic, ...) must implement, plus a deterministic :class:`MockReviewer`
used for automated tests and for validating the experimental wiring
end-to-end before any real API calls are made or any API credits are
spent.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .conditions import Condition
from .models import ReviewerAssessment
from .prompts import ReviewerPrompt


@runtime_checkable
class Reviewer(Protocol):
    """Anything that can turn a (condition, prompt) pair into a validated assessment.

    Concrete implementations (mock, or later a real OpenAI/Anthropic
    provider) must expose ``provider``/``model`` identifiers and a
    ``review`` method with this exact signature. No implementation may
    read or return hidden-test source, paths, or results.
    """

    provider: str
    model: str

    def review(self, condition: Condition, prompt: ReviewerPrompt) -> ReviewerAssessment:
        """Return a validated :class:`ReviewerAssessment` for this prompt."""
        ...


class MockReviewer:
    """Deterministic reviewer with fixed, valid assessments per condition.

    Makes no network calls and reads nothing beyond the prompt it is
    given. Used to validate the condition/prompt/CLI wiring before a real
    provider-backed reviewer is added in a later milestone. The fixed
    assessments below are illustrative fixtures only -- they are not a
    real experimental signal.
    """

    provider = "mock"
    model = "mock-deterministic-v1"

    _FIXED_ASSESSMENTS: dict[Condition, ReviewerAssessment] = {
        Condition.A_NO_RESULT: ReviewerAssessment(
            condition=Condition.A_NO_RESULT,
            predicted_pass=True,
            confidence=55,
            suspected_issues=[
                "No visible-test outcome was provided, so even visible-test "
                "behavior is unverified from this prompt alone.",
            ],
            rationale=(
                "Confidence is based only on reading the code, with no "
                "information about whether even the visible tests pass."
            ),
        ),
        Condition.B_VISIBLE_PASS: ReviewerAssessment(
            condition=Condition.B_VISIBLE_PASS,
            predicted_pass=True,
            confidence=85,
            suspected_issues=[],
            rationale=(
                "All visible tests passing increases confidence that the "
                "hidden suite will also pass."
            ),
        ),
        Condition.C_ADVERSARIAL: ReviewerAssessment(
            condition=Condition.C_ADVERSARIAL,
            predicted_pass=False,
            confidence=40,
            suspected_issues=[
                "Chained same-precedence operators may not be evaluated "
                "strictly left-to-right.",
            ],
            rationale=(
                "Actively searching for gaps surfaced a plausible "
                "associativity issue that the visible tests do not exercise."
            ),
        ),
    }

    def review(self, condition: Condition, prompt: ReviewerPrompt) -> ReviewerAssessment:
        if prompt.condition != condition:
            raise ValueError(
                f"Condition mismatch: requested {condition!r} but prompt was "
                f"built for {prompt.condition!r}"
            )
        return self._FIXED_ASSESSMENTS[condition]
