"""Typed models for hidden-blind coding-agent candidate generation.

These describe the request/context a generator is allowed to see, each
attempt's visible-test feedback, and the durable on-disk artifact for
one generated candidate.

None of these models may ever carry hidden-test source, hidden-test
paths/filenames, hidden-test results, or an output filesystem path chosen
by a provider. The required module filename is always harness metadata
taken from the task manifest, never from model output.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GenerationStatus = Literal["completed", "failed"]

StopReason = Literal[
    "visible_tests_passed",
    "max_attempts_reached",
    "provider_error",
    "validation_error",
    "timeout",
]


class CandidateGenerationError(RuntimeError):
    """Safe, secret-free error for candidate-generation failures.

    Messages on this type are expected to be printable to CLI callers:
    they must never include ``OPENAI_API_KEY`` or other secrets.
    """


class _FrozenBase(BaseModel):
    """Base for generation models: reject unknown fields so a provider
    cannot smuggle an output path or hidden-test field through."""

    model_config = ConfigDict(extra="forbid")


def _require_tz_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "timestamp must be timezone-aware (naive datetimes are not allowed); "
            "use e.g. datetime.now(timezone.utc)"
        )
    return value.astimezone(timezone.utc)


class VisibleTestFeedback(BaseModel):
    """Visible-suite execution feedback sent back to the generator.

    Deliberately has no ``suite`` field (so the literal ``hidden`` can
    never appear here) and no hidden-test counts or paths.
    """

    model_config = ConfigDict(extra="forbid")

    passed: bool
    timed_out: bool
    exit_code: Optional[int]
    duration_seconds: float
    stdout: str
    stderr: str
    passed_count: int
    failed_count: int


class CandidateGenerationRequest(_FrozenBase):
    """Everything a generator is allowed to see for one attempt.

    Built from frozen specification + starter + visible tests, plus
    (on later attempts) the prior candidate source and that attempt's
    visible-test feedback. Never includes an output path, a task root,
    or any hidden-test information.
    """

    task_id: str
    specification: str
    starter_source: str
    visible_tests_source: dict[str, str]
    required_module_filename: str
    attempt_number: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    random_seed: int
    previous_source: Optional[str] = None
    visible_feedback: Optional[VisibleTestFeedback] = None

    @field_validator("required_module_filename")
    @classmethod
    def _filename_must_be_a_bare_name(cls, value: str) -> str:
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError(
                "required_module_filename must be a bare filename with no path separators"
            )
        return value

    @model_validator(mode="after")
    def _later_attempts_include_prior_source_and_feedback(self) -> "CandidateGenerationRequest":
        if self.attempt_number > 1:
            if not self.previous_source:
                raise ValueError("attempt_number > 1 requires previous_source")
            if self.visible_feedback is None:
                raise ValueError("attempt_number > 1 requires visible_feedback")
        return self


class CandidateGeneratorOutput(_FrozenBase):
    """Structured generator response: complete source plus a short summary.

    Extra fields (including any attempted output path) are forbidden.
    ``source`` must be nonempty after stripping whitespace; it is never
    interpreted as a filesystem path.
    """

    source: str
    summary: str

    @field_validator("source")
    @classmethod
    def _source_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate source must be nonempty")
        return value


class CandidateAttempt(_FrozenBase):
    """One generate-then-run-visible-tests iteration."""

    attempt_number: int = Field(ge=1)
    source_sha256: str
    summary: str
    visible_result: VisibleTestFeedback
    latency_seconds: Optional[float] = None
    response_id: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_tz_aware_utc(cls, value: datetime) -> datetime:
        return _require_tz_aware_utc(value)


class CandidateArtifactMetadata(_FrozenBase):
    """Durable metadata for one generated candidate, stored as ``metadata.json``.

    ``random_seed`` is recorded for workflow reproducibility. It does not
    mean the selected API model produced deterministic sampled output.
    """

    candidate_id: uuid.UUID
    task_id: str
    provider: str
    model: str
    prompt_version: str
    random_seed: int
    max_attempts: int = Field(ge=1)
    status: GenerationStatus
    stop_reason: StopReason
    required_module_filename: str
    specification_sha256: str
    starter_sha256: str
    visible_tests_sha256: str
    final_source_sha256: Optional[str] = None
    visible_tests_passed: bool
    visible_passed_count: Optional[int] = None
    visible_failed_count: Optional[int] = None
    attempt_count: int = Field(ge=0)
    attempts: list[CandidateAttempt] = Field(default_factory=list)
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    total_latency_seconds: Optional[float] = None
    model_sampling_deterministic: bool
    seed_semantics: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None

    @field_validator("started_at")
    @classmethod
    def _started_at_is_tz_aware_utc(cls, value: datetime) -> datetime:
        return _require_tz_aware_utc(value)

    @field_validator("completed_at")
    @classmethod
    def _completed_at_is_tz_aware_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return _require_tz_aware_utc(value)

    @field_validator("required_module_filename")
    @classmethod
    def _filename_must_be_a_bare_name(cls, value: str) -> str:
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError(
                "required_module_filename must be a bare filename with no path separators"
            )
        return value


DEFAULT_SEED_SEMANTICS = (
    "random_seed is recorded for workflow reproducibility (artifact identity "
    "and any harness-side ordering). It does not make provider model sampling "
    "deterministic unless model_sampling_deterministic is true."
)
