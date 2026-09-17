"""Real OpenAI-backed reviewer, compatible with the :class:`experiment.reviewer.Reviewer` protocol.

Uses the OpenAI Responses API's structured-output parsing
(``client.responses.parse(..., text_format=<pydantic model>)``) so the
model's output is parsed directly into a small, provider-specific
Pydantic schema -- never by manually extracting JSON from free-form
text. The final :class:`experiment.models.ReviewerAssessment` is then
constructed in application code, attaching the known experimental
``condition`` and reusing that model's existing validation (confidence
bounds, predicted-pass/confidence consistency).

Only ``prompt.text`` (specification + candidate + visible tests +
condition-specific sections -- see ``experiment.prompts``) is ever sent
to the API. No tools (web search, code execution, file search, ...) are
enabled, and no hidden-test content, paths, filenames, sentinels, or
results are ever read or transmitted by this module.

This module never reads, logs, prints, or raises exceptions containing
the value of ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from pydantic import BaseModel, Field, ValidationError

from .conditions import Condition
from .models import ReviewerAssessment
from .prompts import ReviewerPrompt

# Project-root .env (two levels up from backend/experiment/).
_ROOT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# Exact default model ID for development. Never silently substituted for
# another model -- the effective model is always exactly what
# ``resolve_model()`` returns, and it is recorded verbatim on every result.
DEFAULT_MODEL = "gpt-5.6-luna"

ENV_VAR_MODEL = "OPENAI_REVIEWER_MODEL"
ENV_VAR_API_KEY = "OPENAI_API_KEY"


class OpenAIReviewerError(RuntimeError):
    """Base class for all :class:`OpenAIReviewer` errors.

    Every message on this hierarchy is safe to log, print, or return to
    a CLI caller: none of them ever include the value of
    ``OPENAI_API_KEY`` or any other secret.
    """


class OpenAIReviewerConfigError(OpenAIReviewerError):
    """Raised when required configuration (e.g. ``OPENAI_API_KEY``) is missing."""


class OpenAIReviewerAuthError(OpenAIReviewerError):
    """Raised when the OpenAI API rejects the request as unauthenticated."""


class OpenAIReviewerQuotaError(OpenAIReviewerError):
    """Raised when the OpenAI account has insufficient quota/billing."""


class OpenAIReviewerRateLimitError(OpenAIReviewerError):
    """Raised when the OpenAI API rate-limits the request."""


class OpenAIReviewerNetworkError(OpenAIReviewerError):
    """Raised on network/connection failures while contacting the OpenAI API."""


class OpenAIReviewerAPIError(OpenAIReviewerError):
    """Raised for other OpenAI API failures (bad request, server error, etc.)."""


class OpenAIReviewerOutputError(OpenAIReviewerError):
    """Raised when the model refuses, or its output cannot be parsed into
    (or validated as) a :class:`ReviewerAssessment`."""


class OpenAIReviewerResponseSchema(BaseModel):
    """Small, provider-specific structured-output schema for the Responses API.

    Deliberately does *not* include ``condition`` -- the experimental
    condition is authoritative harness metadata attached by application
    code after parsing, not something the model reports about itself.

    Also deliberately does *not* include ``predicted_pass``: an early live
    smoke test showed models sometimes treat a separate boolean field as
    "confidence in my verdict" rather than "confidence of passing" (e.g.
    returning ``predicted_pass=False, confidence=99`` to mean "99% sure it
    fails"). Asking for only ``confidence`` -- with an explicit
    probability-of-passing description -- and deriving ``predicted_pass``
    from it in application code removes that ambiguity entirely.
    """

    confidence: int = Field(
        ge=0,
        le=100,
        description=(
            "Probability from 0 through 100 that this exact candidate will "
            "pass the private evaluation suite. This is always probability "
            "of PASSING, not confidence in the reviewer's verdict."
        ),
    )
    suspected_issues: list[str] = Field(default_factory=list)
    rationale: str


def resolve_model(explicit_model: Optional[str] = None) -> str:
    """Resolve the exact model ID to use, in priority order.

    1. ``explicit_model`` (e.g. a CLI ``--model`` override), if given.
    2. The ``OPENAI_REVIEWER_MODEL`` environment variable, if set.
    3. :data:`DEFAULT_MODEL`.

    Never falls back silently to a *different* model after a choice has
    been made at one of these levels -- whatever is resolved here is
    exactly what gets sent to the API and recorded on the result.
    """

    if explicit_model:
        return explicit_model
    env_model = os.environ.get(ENV_VAR_MODEL)
    if env_model:
        return env_model
    return DEFAULT_MODEL


def _load_project_env() -> None:
    """Safely load the project-root ``.env`` file, if present.

    ``override=False`` (python-dotenv's default) ensures that a value
    already present in the process environment always wins over the
    ``.env`` file, so this works identically whether ``OPENAI_API_KEY``
    was exported directly or only defined in ``.env``.
    """

    load_dotenv(dotenv_path=_ROOT_ENV_PATH, override=False)


def _build_client() -> OpenAI:
    _load_project_env()
    api_key = os.environ.get(ENV_VAR_API_KEY)
    if not api_key:
        raise OpenAIReviewerConfigError(
            f"{ENV_VAR_API_KEY} is not set. Add it to the project-root .env file "
            "or export it in the environment before using --provider openai."
        )
    return OpenAI(api_key=api_key)


class OpenAIReviewer:
    """Reviewer backend that calls the real OpenAI Responses API.

    Compatible with :class:`experiment.reviewer.Reviewer`. Pass an
    explicit ``client`` (e.g. a fake/mock) to avoid any real network
    calls and to avoid requiring ``OPENAI_API_KEY`` at all -- this is how
    the automated test suite exercises this class.
    """

    provider = "openai"

    def __init__(self, model: Optional[str] = None, client: Optional[OpenAI] = None) -> None:
        self.model = resolve_model(model)
        self._client = client if client is not None else _build_client()

        # Populated after each successful `review()` call, when available.
        self.last_response_id: Optional[str] = None
        self.last_input_tokens: Optional[int] = None
        self.last_output_tokens: Optional[int] = None

    def review(self, condition: Condition, prompt: ReviewerPrompt) -> ReviewerAssessment:
        if prompt.condition != condition:
            raise ValueError(
                f"Condition mismatch: requested {condition!r} but prompt was "
                f"built for {prompt.condition!r}"
            )

        response = self._call_api(prompt)

        parsed = response.output_parsed
        if parsed is None:
            raise OpenAIReviewerOutputError(
                "OpenAI response contained no parsed structured output. The "
                "model may have refused to answer, or its output did not "
                "match the requested schema."
            )

        try:
            assessment = ReviewerAssessment(
                condition=condition,
                # Derived, never requested from the model: this is the
                # single source of truth for "confidence means probability
                # of passing", and it is definitionally consistent with
                # ReviewerAssessment's own validator.
                predicted_pass=parsed.confidence >= 50,
                confidence=parsed.confidence,
                suspected_issues=list(parsed.suspected_issues),
                rationale=parsed.rationale,
            )
        except ValidationError as exc:
            raise OpenAIReviewerOutputError(
                f"OpenAI structured output failed ReviewerAssessment validation: {exc}"
            ) from exc

        self.last_response_id = getattr(response, "id", None)
        usage = getattr(response, "usage", None)
        self.last_input_tokens = getattr(usage, "input_tokens", None) if usage else None
        self.last_output_tokens = getattr(usage, "output_tokens", None) if usage else None

        return assessment

    def _call_api(self, prompt: ReviewerPrompt):
        try:
            return self._client.responses.parse(
                model=self.model,
                input=prompt.text,
                text_format=OpenAIReviewerResponseSchema,
                # Explicitly no tools: no web search, code execution, file
                # search, or anything else. Model settings are identical
                # across conditions A/B/C -- nothing here branches on
                # `prompt.condition`.
                tools=[],
            )
        except AuthenticationError as exc:
            raise OpenAIReviewerAuthError(
                "OpenAI authentication failed. Check that OPENAI_API_KEY is valid."
            ) from exc
        except RateLimitError as exc:
            if exc.code == "insufficient_quota":
                raise OpenAIReviewerQuotaError(
                    "OpenAI reported insufficient quota/billing for this account."
                ) from exc
            raise OpenAIReviewerRateLimitError(
                "OpenAI rate-limited this request. Retry later or reduce request volume."
            ) from exc
        except (APIConnectionError, APITimeoutError) as exc:
            raise OpenAIReviewerNetworkError(
                "Network error while contacting the OpenAI API."
            ) from exc
        except APIStatusError as exc:
            raise OpenAIReviewerAPIError(
                f"OpenAI API returned an error (HTTP {exc.status_code})."
            ) from exc
        except OpenAIError as exc:
            # Deliberately do not interpolate `exc` into the message: this is
            # a catch-all for otherwise-unclassified SDK errors, and we never
            # want to risk echoing request/auth details into CLI output.
            raise OpenAIReviewerAPIError(
                f"OpenAI API call failed ({type(exc).__name__})."
            ) from exc
