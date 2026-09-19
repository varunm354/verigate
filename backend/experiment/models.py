"""Typed data models for VeriGate's experiment harness.

These describe a task's manifest (as stored in ``manifest.json``) and the
structured results produced by running its visible/hidden test suites.
They intentionally contain no reviewer- or LLM-specific logic; that lives
in ``experiment.context`` (added in a later milestone).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .conditions import Condition


class TaskPaths(BaseModel):
    """Paths, relative to the task's root directory, to its components.

    ``starter`` is optional and backward-compatible: tasks that support
    candidate generation (e.g. ``json_parser``) declare a starter file;
    tasks that do not (e.g. ``expression_evaluator``) omit it.
    """

    specification: str
    candidate: str
    visible_tests: str
    hidden_tests: str
    starter: Optional[str] = None

    @field_validator("starter")
    @classmethod
    def _starter_must_be_nonempty_if_present(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("starter path must be a nonempty relative path if provided")
        return value


class TaskManifest(BaseModel):
    """Schema for a task's ``manifest.json`` file."""

    task_id: str
    title: str
    language: Literal["python"]
    timeout_seconds: float = Field(gt=0)
    paths: TaskPaths


class TestSuiteResult(BaseModel):
    """Outcome of running one task's visible or hidden pytest suite."""

    suite: Literal["visible", "hidden"]
    passed: bool
    timed_out: bool
    exit_code: Optional[int]
    duration_seconds: float
    stdout: str
    stderr: str
    passed_count: int
    failed_count: int


class ReviewerAssessment(BaseModel):
    """A validated reviewer output for one (task, condition) pair.

    Deliberately contains no hidden-test information of any kind --
    only what the reviewer inferred from the (specification, candidate,
    visible tests) prompt built in ``experiment.prompts``.

    ``confidence`` is defined as the probability (0-100) that the
    candidate passes private evaluation, so ``predicted_pass`` must be
    internally consistent with it: true iff ``confidence >= 50``.
    """

    condition: Condition
    predicted_pass: bool
    confidence: int = Field(ge=0, le=100)
    suspected_issues: list[str] = Field(default_factory=list)
    rationale: str

    @model_validator(mode="after")
    def _predicted_pass_matches_confidence(self) -> "ReviewerAssessment":
        expected_predicted_pass = self.confidence >= 50
        if self.predicted_pass != expected_predicted_pass:
            raise ValueError(
                "predicted_pass is inconsistent with confidence: "
                f"confidence={self.confidence} implies predicted_pass="
                f"{expected_predicted_pass}, got predicted_pass={self.predicted_pass}"
            )
        return self


class ReviewerResult(BaseModel):
    """Full structured record of one reviewer run, suitable for later analysis.

    Like :class:`ReviewerAssessment`, this must never carry hidden-test
    source, paths, or results -- those are only ever produced by
    :class:`experiment.runner.PytestRunner` for ground-truth evaluation,
    which is kept entirely separate from reviewer prompts/results.
    """

    task_id: str
    condition: Condition
    assessment: ReviewerAssessment
    prompt_version: str
    provider: str
    model: str
    timestamp: datetime
    latency_seconds: Optional[float] = None
    response_id: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_timezone_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(
                "timestamp must be timezone-aware (naive datetimes are not allowed); "
                "use e.g. datetime.now(timezone.utc)"
            )
        return value.astimezone(timezone.utc)
