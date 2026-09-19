"""Real OpenAI-backed candidate generator.

Compatible with :class:`experiment.candidate_generator.CandidateGenerator`.
Uses the OpenAI Responses API's structured-output parsing
(``client.responses.parse(..., text_format=<pydantic model>)``) so the
model's output is parsed directly into a small schema containing only
complete source code and a short summary -- never by extracting fenced
markdown from free-form text, and never with a provider-chosen filesystem
path.

Only the generation prompt (specification + starter + visible tests, plus
prior source and visible-test feedback on later attempts) is ever sent to
the API. No tools are enabled. Hidden-test content, paths, filenames,
sentinels, or results are never read or transmitted by this module.

This module never reads, logs, prints, or raises exceptions containing
the value of ``OPENAI_API_KEY``.

Model selection priority (never a silent fallback to a *different* model
after a choice has been made):

  1. explicit ``model`` argument (e.g. CLI ``--model``)
  2. ``OPENAI_CANDIDATE_MODEL`` environment variable
  3. :data:`DEFAULT_MODEL` (``gpt-5-mini``, an inexpensive default)

``random_seed`` on the generation request is *not* sent to the API. It is
harness metadata only; this provider does not claim deterministic sampled
output.
"""

from __future__ import annotations

import os
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

from .candidate_models import CandidateGenerationRequest, CandidateGeneratorOutput
from .candidate_prompts import build_generation_prompt

_ROOT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# Inexpensive default for candidate generation. Never silently substituted.
DEFAULT_MODEL = "gpt-5-mini"

ENV_VAR_MODEL = "OPENAI_CANDIDATE_MODEL"
ENV_VAR_API_KEY = "OPENAI_API_KEY"


class OpenAICandidateGeneratorError(RuntimeError):
    """Base class for all :class:`OpenAICandidateGenerator` errors.

    Every message on this hierarchy is safe to log, print, or return to
    a CLI caller: none of them ever include the value of
    ``OPENAI_API_KEY`` or any other secret.
    """


class OpenAICandidateGeneratorConfigError(OpenAICandidateGeneratorError):
    """Raised when required configuration (e.g. ``OPENAI_API_KEY``) is missing."""


class OpenAICandidateGeneratorAuthError(OpenAICandidateGeneratorError):
    """Raised when the OpenAI API rejects the request as unauthenticated."""


class OpenAICandidateGeneratorQuotaError(OpenAICandidateGeneratorError):
    """Raised when the OpenAI account has insufficient quota/billing."""


class OpenAICandidateGeneratorRateLimitError(OpenAICandidateGeneratorError):
    """Raised when the OpenAI API rate-limits the request."""


class OpenAICandidateGeneratorNetworkError(OpenAICandidateGeneratorError):
    """Raised on network/connection failures while contacting the OpenAI API."""


class OpenAICandidateGeneratorTimeoutError(OpenAICandidateGeneratorError):
    """Raised when the OpenAI API call times out."""


class OpenAICandidateGeneratorAPIError(OpenAICandidateGeneratorError):
    """Raised for other OpenAI API failures (bad request, server error, etc.)."""


class OpenAICandidateGeneratorOutputError(OpenAICandidateGeneratorError):
    """Raised when the model refuses, or its output cannot be parsed/validated."""


class OpenAICandidateResponseSchema(BaseModel):
    """Provider-specific structured-output schema for the Responses API.

    Deliberately contains only source text and a summary -- never a
    filesystem path, never hidden-test content.
    """

    source: str = Field(
        description=(
            "Complete Python source code for the required module. Do not wrap "
            "it in markdown fences. Must be a complete, directly writable .py file."
        ),
    )
    summary: str = Field(
        description="A short implementation summary (one or two sentences).",
    )


def resolve_model(explicit_model: Optional[str] = None) -> str:
    """Resolve the exact model ID to use, in priority order.

    1. ``explicit_model`` (e.g. a CLI ``--model`` override), if given.
    2. The ``OPENAI_CANDIDATE_MODEL`` environment variable, if set.
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
    load_dotenv(dotenv_path=_ROOT_ENV_PATH, override=False)


def _build_client() -> OpenAI:
    _load_project_env()
    api_key = os.environ.get(ENV_VAR_API_KEY)
    if not api_key:
        raise OpenAICandidateGeneratorConfigError(
            f"{ENV_VAR_API_KEY} is not set. Add it to the project-root .env file "
            "or export it in the environment before using --provider openai."
        )
    return OpenAI(api_key=api_key)


def _strip_optional_markdown_fences(source: str) -> str:
    """If the entire source is wrapped in a single markdown fence, unwrap it.

    Fences are not requested; this only recovers from a model that wrapped
    anyway. Inner code is otherwise returned unchanged.
    """

    stripped = source.strip()
    if not stripped.startswith("```"):
        return source
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        body = lines[1:-1]
        return "\n".join(body) + ("\n" if source.endswith("\n") else "")
    return source


class OpenAICandidateGenerator:
    """Candidate-generator backend that calls the real OpenAI Responses API.

    Pass an explicit ``client`` (e.g. a fake/mock) to avoid any real
    network calls and to avoid requiring ``OPENAI_API_KEY`` -- this is
    how the automated test suite exercises this class.
    """

    provider = "openai"

    def __init__(self, model: Optional[str] = None, client: Optional[OpenAI] = None) -> None:
        self.model = resolve_model(model)
        self._client = client if client is not None else _build_client()

        self.last_response_id: Optional[str] = None
        self.last_input_tokens: Optional[int] = None
        self.last_output_tokens: Optional[int] = None

    def generate(self, request: CandidateGenerationRequest) -> CandidateGeneratorOutput:
        prompt = build_generation_prompt(request)
        response = self._call_api(prompt.text)

        parsed = response.output_parsed
        if parsed is None:
            raise OpenAICandidateGeneratorOutputError(
                "OpenAI response contained no parsed structured output. The "
                "model may have refused to answer, or its output did not "
                "match the requested schema."
            )

        try:
            output = CandidateGeneratorOutput(
                source=_strip_optional_markdown_fences(parsed.source),
                summary=parsed.summary,
            )
        except ValidationError as exc:
            raise OpenAICandidateGeneratorOutputError(
                f"OpenAI structured output failed CandidateGeneratorOutput validation: {exc}"
            ) from exc

        self.last_response_id = getattr(response, "id", None)
        usage = getattr(response, "usage", None)
        self.last_input_tokens = getattr(usage, "input_tokens", None) if usage else None
        self.last_output_tokens = getattr(usage, "output_tokens", None) if usage else None

        return output

    def _call_api(self, prompt_text: str):
        try:
            return self._client.responses.parse(
                model=self.model,
                input=prompt_text,
                text_format=OpenAICandidateResponseSchema,
                tools=[],
            )
        except AuthenticationError as exc:
            raise OpenAICandidateGeneratorAuthError(
                "OpenAI authentication failed. Check that OPENAI_API_KEY is valid."
            ) from exc
        except RateLimitError as exc:
            if exc.code == "insufficient_quota":
                raise OpenAICandidateGeneratorQuotaError(
                    "OpenAI reported insufficient quota/billing for this account."
                ) from exc
            raise OpenAICandidateGeneratorRateLimitError(
                "OpenAI rate-limited this request. Retry later or reduce request volume."
            ) from exc
        except APITimeoutError as exc:
            raise OpenAICandidateGeneratorTimeoutError(
                "OpenAI API call timed out."
            ) from exc
        except APIConnectionError as exc:
            raise OpenAICandidateGeneratorNetworkError(
                "Network error while contacting the OpenAI API."
            ) from exc
        except APIStatusError as exc:
            raise OpenAICandidateGeneratorAPIError(
                f"OpenAI API returned an error (HTTP {exc.status_code})."
            ) from exc
        except OpenAIError as exc:
            raise OpenAICandidateGeneratorAPIError(
                f"OpenAI API call failed ({type(exc).__name__})."
            ) from exc
