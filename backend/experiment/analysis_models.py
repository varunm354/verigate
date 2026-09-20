"""Typed models for VeriGate's Milestone 11 campaign-analysis pipeline.

These describe the sanitized, deterministic, version-controlled output of
``experiment.campaign_analyzer.CampaignAnalyzer`` -- a read-only layer over
a *completed* campaign artifact (``experiment.campaign_models``) and the
candidate/experiment artifacts it references. This module is intentionally
separate from the collection/orchestration code
(``experiment.campaign_orchestrator`` / ``experiment.campaign_store``):
nothing here ever writes to a raw artifact, and none of the collection
modules import from here.

None of these models may ever carry: an OpenAI (or other provider)
response id, full prompt text, full candidate source, full visible/hidden
test source, a raw traceback, an absolute filesystem path, or any secret
environment value. Hidden-test information here is limited to the same
aggregate pass/fail counts already present on
``experiment.experiment_models.GroundTruth`` -- never test identifiers,
stdout/stderr, or source.
"""

from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .conditions import Condition

ANALYSIS_SCHEMA_VERSION = "campaign-analysis-v1"

AdjudicationStatus = Literal[
    "valid_pass",
    "valid_failure",
    "benchmark_mismatch",
    "ambiguous",
    "pending",
]

AdjudicationSource = Literal[
    "auto_valid_pass",
    "recorded_adjudication",
    "pending_new_failure",
]

CohortScope = Literal["full_cohort", "primary_eligible"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationCheck(_Strict):
    """One passed validation check, recorded for transparency in the report.

    Only ever appended *after* the corresponding check has succeeded --
    :class:`~experiment.campaign_analyzer.CampaignValidationError` is
    raised immediately on the first failing check, so a report is only
    ever produced for a campaign that passed every check below.
    """

    name: str
    detail: str


class ValidationReport(_Strict):
    campaign_status: str
    checks: list[ValidationCheck] = Field(default_factory=list)


class RawArtifactProvenance(_Strict):
    """Deterministic SHA-256 hashes of every raw artifact this analysis read.

    Hashes are of the raw on-disk file bytes -- never of any decoded/derived
    content -- so a rerun against unchanged artifacts always reproduces the
    same values.

    The four ``*protocol*`` fields are kept as separate, explicitly typed
    values (never merged into one "the protocol hash" field) so that a
    reader can tell, without recomputing anything, exactly what changed:

    - ``campaign_protocol_sha256`` is the immutable hash recorded on the
      *campaign* artifact at campaign-creation time. This value is never
      recomputed or overwritten by analysis -- it is copied verbatim from
      ``campaign.provenance.research_protocol_sha256``.
    - ``analyzed_protocol_sha256`` is the hash of the protocol document as
      it exists right now, at analysis time.
    - ``protocol_changed_after_collection`` is ``True`` iff those two
      hashes differ.
    - ``protocol_change_verified_append_only`` is ``True`` iff the change
      (when one exists) was independently verified, via local ``git show``
      against the campaign-creation commit, to be a *pure append*: every
      byte present at campaign-creation time is still present, unmodified,
      at the same offset in the current document. Analysis refuses to
      proceed (raises before a report is ever produced) for any other kind
      of protocol change -- an edit, deletion, insertion, or reordering of
      pre-existing text, or a change that could not be verified against
      git history -- so if a report exists, this field is always ``False``
      when ``protocol_changed_after_collection`` is ``False``, and always
      ``True`` when it is ``True``.
    """

    campaign_artifact_sha256: str
    candidate_metadata_sha256: dict[str, str] = Field(default_factory=dict)
    experiment_artifact_sha256: dict[str, str] = Field(default_factory=dict)
    campaign_protocol_sha256: str
    analyzed_protocol_sha256: str
    protocol_changed_after_collection: bool
    protocol_change_verified_append_only: bool
    adjudications_file_sha256: str


class ConditionObservationStats(_Strict):
    """Per-candidate, per-condition statistics over its reviewer repetitions."""

    condition: Condition
    raw_confidence: list[int]
    mean_confidence: float
    predicted_pass_count: int
    observation_count: int
    mean_latency_seconds: Optional[float] = None
    mean_input_tokens: Optional[float] = None
    mean_output_tokens: Optional[float] = None


class CandidateAnalysis(_Strict):
    """Full descriptive record for one qualifying candidate.

    Retained for every candidate regardless of adjudication status --
    ``eligible_for_primary_analysis`` is what primary/summary statistics
    filter on, not omission from this table.
    """

    qualification_index: int = Field(ge=0)
    task_id: str
    candidate_id: uuid.UUID
    candidate_source_sha256: str
    generation_seed: int
    generation_attempt_index: int = Field(ge=0)
    generation_attempt_count: int = Field(ge=0)
    reviewer_seed: int
    experiment_id: uuid.UUID
    visible_passed_count: int
    visible_failed_count: int
    hidden_passed_count: Optional[int] = None
    hidden_failed_count: Optional[int] = None
    benchmark_pass: Optional[bool] = None
    conditions: dict[str, ConditionObservationStats]
    mean_confidence_a: float
    mean_confidence_b: float
    mean_confidence_c: float
    b_minus_a: float
    c_minus_b: float
    adjudication_status: AdjudicationStatus
    adjudication_source: AdjudicationSource
    specification_correct: Optional[bool] = None
    eligible_for_primary_analysis: bool
    exclusion_reason: Optional[str] = None


class AdjudicationQueueItem(_Strict):
    """The minimum information a human adjudicator needs for one pending candidate.

    Never carries full hidden-test source, hidden-test identifiers (not
    recorded anywhere in the harness's stored artifacts -- only aggregate
    pass/fail counts are), stdout/stderr, or any other leakage-risk field.
    """

    task_id: str
    candidate_id: uuid.UUID
    candidate_source_sha256: str
    qualification_index: int = Field(ge=0)
    visible_passed_count: int
    visible_failed_count: int
    hidden_passed_count: Optional[int] = None
    hidden_failed_count: Optional[int] = None
    benchmark_pass: Optional[bool] = None
    status: Literal["pending_adjudication"] = "pending_adjudication"
    reason: str
    failing_test_identifiers: Optional[list[str]] = None
    failing_test_identifiers_note: str
    specification_reference_notes: list[str] = Field(default_factory=list)


class CohortConditionSummary(_Strict):
    condition: str
    candidate_count: int
    mean_confidence: Optional[float] = None
    predicted_pass_count: int
    predicted_pass_rate: Optional[float] = None
    mean_latency_seconds: Optional[float] = None
    mean_input_tokens: Optional[float] = None
    mean_output_tokens: Optional[float] = None


class CohortSummary(_Strict):
    """One protocol-aligned summary row: a scope x task-or-overall slice."""

    scope: CohortScope
    task_id: Optional[str] = None  # None means "overall" (all configured tasks)
    candidate_count: int
    conditions: list[CohortConditionSummary]
    mean_b_minus_a: Optional[float] = None
    mean_c_minus_b: Optional[float] = None
    benchmark_pass_count: int
    benchmark_fail_count: int
    benchmark_unknown_count: int
    benchmark_pass_rate: Optional[float] = None


class CampaignAnalysisConfigSnapshot(_Strict):
    """A safe, non-secret snapshot of the campaign's immutable configuration."""

    generator_provider: str
    generator_model: str
    reviewer_provider: str
    reviewer_model: str
    task_ids: tuple[str, ...]
    target_per_task: int
    repetitions: int
    generation_seed_start: int
    reviewer_seed_start: int
    max_candidate_attempts: int
    cutoff_utc: str


class CampaignAnalysisTotals(_Strict):
    qualifying_candidate_count: int
    generation_attempt_count: int
    attrition_count: int
    reviewer_observation_count: int


class CampaignAnalysisReport(_Strict):
    """The complete sanitized analysis, written as ``analysis.json``."""

    schema_version: str = ANALYSIS_SCHEMA_VERSION
    campaign_id: uuid.UUID
    campaign_status: str
    campaign_repository_commit: Optional[str] = None
    campaign_repository_dirty: bool
    analysis_repository_commit: Optional[str] = None
    analysis_repository_dirty: bool
    protocol_path: str
    config: CampaignAnalysisConfigSnapshot
    totals: CampaignAnalysisTotals
    provenance: RawArtifactProvenance
    validation: ValidationReport
    candidate_count: int
    eligible_count: int
    excluded_count: int
    pending_count: int
    candidates: list[CandidateAnalysis]
    full_cohort_summaries: list[CohortSummary]
    primary_eligible_summaries: list[CohortSummary]
    adjudication_queue: list[AdjudicationQueueItem]
    notes: list[str] = Field(default_factory=list)
