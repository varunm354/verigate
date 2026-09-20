"""Typed models for a durable, preregistered VeriGate campaign.

A campaign is the operational record of the exploratory protocol in
``docs/research_protocol.md``: alternating generation attempts, sequential
seed consumption, qualifying-candidate reviewer experiments, attrition,
and the calendar cutoff. These models never carry hidden-test source,
hidden-test paths/filenames, reviewer confidence values, or hidden-test
outcomes. Scheduling decisions must not be made from those quantities;
they are therefore absent from this schema.

Artifact identifiers stored here are project-relative strings, never
absolute filesystem paths.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .candidate_models import StopReason
from .experiment_models import ExperimentStatus

CAMPAIGN_SCHEMA_VERSION = "campaign-v1"

CAMPAIGN_TASK_IDS: tuple[str, ...] = ("json_parser", "package_resolver")

CampaignStatus = Literal[
    "created",
    "running",
    "completed",
    "stopped_cutoff",
    "blocked",
    "failed",
]

GenerationAttemptStatus = Literal["in_progress", "qualified", "attrition"]

DEFAULT_TARGET_PER_TASK = 6
DEFAULT_MAX_CANDIDATE_ATTEMPTS = 3
DEFAULT_GENERATION_SEED_START = 42
DEFAULT_REVIEWER_SEED_START = 1000
DEFAULT_REPETITIONS = 3
DEFAULT_MAX_GENERATION_ATTEMPTS = 36
DEFAULT_CUTOFF_TIMEZONE = "America/Los_Angeles"
DEFAULT_CUTOFF_LOCAL = "2026-09-21T12:00:00"


class CampaignOperationError(RuntimeError):
    """Safe, secret-free error for campaign create/status/run failures."""


class CampaignDirtyWorktreeError(CampaignOperationError):
    """Raised when campaign creation is refused because tracked files are dirty."""


class CampaignLockError(CampaignOperationError):
    """Raised when another process already holds the campaign lock."""


class CampaignLoadError(CampaignOperationError):
    """Raised when a saved campaign artifact cannot be safely loaded."""


class _FrozenBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _MutableBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_tz_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "timestamp must be timezone-aware (naive datetimes are not allowed); "
            "use e.g. datetime.now(timezone.utc)"
        )
    return value.astimezone(timezone.utc)


def _reject_absolute_path(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError(
            "artifact identifiers must be project-relative paths, never an "
            "absolute filesystem path"
        )
    return value


class CampaignCutoff(_FrozenBase):
    """Fixed operational cutoff with original Pacific meaning and UTC equivalent."""

    original: str
    timezone: str = DEFAULT_CUTOFF_TIMEZONE
    local_iso: str
    utc: datetime

    @field_validator("utc")
    @classmethod
    def _utc_is_tz_aware(cls, value: datetime) -> datetime:
        return _require_tz_aware_utc(value)


class CampaignConfig(_FrozenBase):
    """Immutable campaign configuration, recorded at creation."""

    generator_provider: str
    generator_model: str
    reviewer_provider: str
    reviewer_model: str
    task_ids: tuple[str, ...] = CAMPAIGN_TASK_IDS
    target_per_task: int = Field(default=DEFAULT_TARGET_PER_TASK, ge=1)
    max_candidate_attempts: int = Field(default=DEFAULT_MAX_CANDIDATE_ATTEMPTS, ge=1)
    generation_seed_start: int = DEFAULT_GENERATION_SEED_START
    reviewer_seed_start: int = DEFAULT_REVIEWER_SEED_START
    repetitions: int = Field(default=DEFAULT_REPETITIONS, ge=1)
    cutoff: CampaignCutoff
    max_generation_attempts: int = Field(default=DEFAULT_MAX_GENERATION_ATTEMPTS, ge=1)

    @field_validator("task_ids")
    @classmethod
    def _task_ids_must_be_nonempty_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("task_ids must contain at least one task")
        if len(set(value)) != len(value):
            raise ValueError("task_ids must be unique")
        if any(not task_id or "/" in task_id or "\\" in task_id for task_id in value):
            raise ValueError("task_ids must be bare task identifiers")
        return value


class CampaignProvenance(_FrozenBase):
    """Repository and protocol identity recorded at campaign creation."""

    research_protocol_sha256: str
    repository_commit: Optional[str] = None
    repository_dirty: bool = False
    protocol_path: str

    @field_validator("protocol_path")
    @classmethod
    def _protocol_path_must_be_relative(cls, value: str) -> str:
        relative = _reject_absolute_path(value)
        assert relative is not None
        return relative


class CampaignErrorInfo(_FrozenBase):
    """Safe blocker/failure information recorded on the campaign artifact."""

    category: str
    message: str
    generation_attempt_index: Optional[int] = None
    candidate_id: Optional[uuid.UUID] = None
    experiment_id: Optional[uuid.UUID] = None


class CampaignTotals(_FrozenBase):
    """Aggregated API-call / token / latency metadata from child artifacts.

    Fields are ``None`` when no child artifact has supplied that quantity.
    This object is accounting metadata only and is never consulted for
    scheduling, stopping, or candidate selection.
    """

    generation_calls: Optional[int] = None
    generation_input_tokens: Optional[int] = None
    generation_output_tokens: Optional[int] = None
    generation_latency_seconds: Optional[float] = None
    reviewer_calls: Optional[int] = None
    reviewer_input_tokens: Optional[int] = None
    reviewer_output_tokens: Optional[int] = None
    reviewer_latency_seconds: Optional[float] = None


class GenerationAttemptRecord(_MutableBase):
    """One campaign-level generation attempt, including failed attempts."""

    attempt_index: int = Field(ge=0)
    task_id: str
    generation_seed: int
    status: GenerationAttemptStatus
    candidate_id: Optional[uuid.UUID] = None
    source_sha256: Optional[str] = None
    generation_artifact_path: Optional[str] = None
    stop_reason: Optional[StopReason] = None
    visible_tests_passed: bool = False
    generator_attempt_count: int = Field(default=0, ge=0)
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_seconds: Optional[float] = None
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

    @field_validator("generation_artifact_path")
    @classmethod
    def _generation_artifact_path_must_be_relative(cls, value: Optional[str]) -> Optional[str]:
        return _reject_absolute_path(value)


class QualifyingCandidateRecord(_MutableBase):
    """One chronologically qualifying candidate and its assigned reviewer experiment.

    Does not store reviewer confidence or hidden-test outcomes. Those live
    only on the child experiment artifact and must not affect later
    scheduling.
    """

    qualification_index: int = Field(ge=0)
    task_id: str
    candidate_id: uuid.UUID
    source_sha256: str
    generation_seed: int
    generation_attempt_index: int = Field(ge=0)
    generation_artifact_path: str
    reviewer_seed: int
    experiment_started: bool = False
    experiment_id: Optional[uuid.UUID] = None
    experiment_status: Optional[ExperimentStatus] = None
    experiment_artifact_path: Optional[str] = None
    reviewer_observation_count: Optional[int] = None
    reviewer_input_tokens: Optional[int] = None
    reviewer_output_tokens: Optional[int] = None
    reviewer_latency_seconds: Optional[float] = None

    @field_validator("generation_artifact_path", "experiment_artifact_path")
    @classmethod
    def _paths_must_be_relative(cls, value: Optional[str]) -> Optional[str]:
        return _reject_absolute_path(value)


class AttritionRecord(_FrozenBase):
    """A generation-attempt seed that did not produce a qualifying candidate."""

    generation_attempt_index: int = Field(ge=0)
    task_id: str
    generation_seed: int
    candidate_id: Optional[uuid.UUID] = None
    stop_reason: Optional[StopReason] = None
    generator_attempt_count: int = Field(ge=0)
    reason: str


class CampaignArtifact(_MutableBase):
    """The complete, durable, on-disk record of one campaign."""

    schema_version: str = CAMPAIGN_SCHEMA_VERSION
    campaign_id: uuid.UUID
    status: CampaignStatus
    config: CampaignConfig
    provenance: CampaignProvenance
    created_at: datetime
    started_at: Optional[datetime] = None
    updated_at: datetime
    completed_at: Optional[datetime] = None
    next_generation_seed: int
    next_reviewer_seed: int
    qualifying_counts: dict[str, int] = Field(default_factory=dict)
    generation_attempts: list[GenerationAttemptRecord] = Field(default_factory=list)
    qualifying_candidates: list[QualifyingCandidateRecord] = Field(default_factory=list)
    attrition: list[AttritionRecord] = Field(default_factory=list)
    totals: CampaignTotals = Field(default_factory=CampaignTotals)
    error: Optional[CampaignErrorInfo] = None
    artifact_path: Optional[str] = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _required_timestamps_are_tz_aware_utc(cls, value: datetime) -> datetime:
        return _require_tz_aware_utc(value)

    @field_validator("started_at", "completed_at")
    @classmethod
    def _optional_timestamps_are_tz_aware_utc(
        cls, value: Optional[datetime]
    ) -> Optional[datetime]:
        if value is None:
            return None
        return _require_tz_aware_utc(value)

    @field_validator("artifact_path")
    @classmethod
    def _artifact_path_must_be_relative(cls, value: Optional[str]) -> Optional[str]:
        return _reject_absolute_path(value)

    @model_validator(mode="after")
    def _qualifying_counts_cover_configured_tasks(self) -> "CampaignArtifact":
        for task_id in self.config.task_ids:
            self.qualifying_counts.setdefault(task_id, 0)
        return self
