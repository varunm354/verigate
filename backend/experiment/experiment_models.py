"""Experiment-level models for Milestone 5's orchestrator.

These describe the durable, on-disk record of one full experiment run:
identifying/configuration metadata, each individual (independent)
reviewer observation, ground-truth test outcomes, and the complete
artifact that combines them.

Like ``experiment.models``, none of these may ever carry hidden-test
source, hidden-test paths/filenames, or hidden-test result content beyond
aggregate pass/fail counts -- and none may ever carry ``OPENAI_API_KEY``
or any other secret. Error messages recorded here are expected to already
be the safe, secret-free messages produced by reviewer implementations
(e.g. ``experiment.openai_reviewer``'s exception hierarchy).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .conditions import Condition
from .models import ReviewerResult

ExperimentStatus = Literal["completed", "partial", "failed"]


def _require_tz_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "timestamp must be timezone-aware (naive datetimes are not allowed); "
            "use e.g. datetime.now(timezone.utc)"
        )
    return value.astimezone(timezone.utc)


class ExperimentMetadata(BaseModel):
    """Identifying/configuration metadata for one experiment run.

    ``candidate_sha256`` / ``specification_sha256`` / ``visible_tests_sha256``
    are ``None`` until the frozen content has actually been hashed, which
    only happens once visible tests are confirmed to fully pass (an
    experiment that fails at the visible-test stage never reaches that
    step, so these stay ``None`` for a ``status="failed"`` artifact of
    that kind).

    ``candidate_id`` / ``candidate_source_sha256`` / ``generator_provider`` /
    ``generator_model`` / ``generation_prompt_version`` /
    ``generation_attempt_count`` / ``generation_artifact_path`` are all
    optional and ``None`` by default (Milestone 8). They are only
    populated when this experiment reviewed a saved, generated candidate
    artifact rather than the tracked reference implementation -- see
    ``experiment.candidate_loader.CandidateArtifactLoader`` and the
    ``candidate=`` parameter on :meth:`experiment.orchestrator
    .ExperimentOrchestrator.run`. Existing experiments created before
    Milestone 8 (or any experiment run without ``--candidate-id``) simply
    have all of these as ``None``, so old saved artifacts remain valid
    without migration. ``generation_artifact_path`` is always a
    project-relative string, never an absolute filesystem path.
    """

    experiment_id: uuid.UUID
    task_id: str
    provider: str
    model: str
    prompt_version: str
    repetitions: int = Field(gt=0)
    random_seed: int
    candidate_sha256: Optional[str] = None
    specification_sha256: Optional[str] = None
    visible_tests_sha256: Optional[str] = None
    candidate_id: Optional[uuid.UUID] = None
    candidate_source_sha256: Optional[str] = None
    generator_provider: Optional[str] = None
    generator_model: Optional[str] = None
    generation_prompt_version: Optional[str] = None
    generation_attempt_count: Optional[int] = None
    generation_artifact_path: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: ExperimentStatus

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

    @field_validator("generation_artifact_path")
    @classmethod
    def _generation_artifact_path_must_be_relative(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ValueError(
                "generation_artifact_path must be a project-relative path, never an "
                "absolute filesystem path"
            )
        return value


class ReviewerObservation(BaseModel):
    """One completed, independent reviewer call within an experiment.

    Each observation corresponds to exactly one reviewer request -- no
    two observations ever share a response ID, conversation, or previous
    response with each other.
    """

    observation_id: uuid.UUID
    repetition_index: int = Field(ge=0)
    execution_order_index: int = Field(ge=0)
    condition: Condition
    result: ReviewerResult


class GroundTruth(BaseModel):
    """Ground-truth test outcomes.

    ``hidden_*`` fields are ``None`` until hidden tests are actually run,
    which only ever happens after every reviewer observation for the
    experiment has completed successfully -- never before, and never at
    all if visible tests failed or any reviewer call failed.
    """

    visible_passed: bool
    visible_passed_count: int
    visible_failed_count: int
    hidden_passed: Optional[bool] = None
    hidden_passed_count: Optional[int] = None
    hidden_failed_count: Optional[int] = None


class ExperimentError(BaseModel):
    """Safe, secret-free error information for a partial/failed experiment."""

    category: str
    message: str
    condition: Optional[Condition] = None
    repetition_index: Optional[int] = None


class ExperimentArtifact(BaseModel):
    """The complete, durable, on-disk record of one experiment run."""

    metadata: ExperimentMetadata
    observations: list[ReviewerObservation] = Field(default_factory=list)
    ground_truth: Optional[GroundTruth] = None
    error: Optional[ExperimentError] = None
