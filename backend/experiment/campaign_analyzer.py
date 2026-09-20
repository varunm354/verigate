"""Read-only, deterministic analysis of a *completed* VeriGate campaign.

This module never mutates a raw artifact. It loads (never writes) the
campaign artifact (``experiment.campaign_store`` / ``campaign_models``) and
every candidate/experiment artifact it references, validates every
invariant listed in the Milestone 11 requirements, applies
``docs/research_protocol.md`` and ``research/adjudications.json`` exactly
(never inventing an adjudication), and produces a
:class:`~experiment.analysis_models.CampaignAnalysisReport` plus a set of
sanitized, deterministic files suitable for version control.

Deliberately separate from ``experiment.campaign_orchestrator`` /
``experiment.campaign_store`` (collection): nothing in this module is
imported by, or writes through, the collection/orchestration code path,
and nothing here ever calls a reviewer, generator, or pytest runner.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, ValidationError

from .analysis_models import (
    ANALYSIS_SCHEMA_VERSION,
    AdjudicationQueueItem,
    CampaignAnalysisConfigSnapshot,
    CampaignAnalysisReport,
    CampaignAnalysisTotals,
    CandidateAnalysis,
    CohortConditionSummary,
    CohortSummary,
    ConditionObservationStats,
    RawArtifactProvenance,
    ValidationCheck,
    ValidationReport,
)
from .campaign_models import CampaignArtifact, QualifyingCandidateRecord
from .campaign_orchestrator import git_show_file_bytes, inspect_repository
from .campaign_store import (
    campaign_json_path,
    default_campaigns_dir,
    load_campaign,
    relative_artifact_identifier,
    repo_root,
)
from .candidate_loader import CandidateArtifactLoadError, CandidateArtifactLoader, LoadedCandidate
from .candidate_orchestrator import default_candidates_dir
from .conditions import Condition
from .experiment_models import ExperimentArtifact, ReviewerObservation
from .loader import LoadedTask, TaskLoadError, TaskLoader
from .orchestrator import default_experiments_dir

# Documented in docs/pilot_findings.md's "Identifiers" section and
# research/adjudications.json. This candidate/experiment predates
# docs/research_protocol.md and must never be pooled into a campaign's
# 12-candidate analysis.
PILOT_CANDIDATE_ID = uuid.UUID("3d51301c-b572-4891-9db4-84171b5b1b7c")
PILOT_EXPERIMENT_ID = uuid.UUID("60e5147f-e69c-4f38-bc34-26b2009a4eb1")

# Generic, task-level pointers a human adjudicator may find useful. These
# are static references to already-tracked documents -- never a claim
# about any specific candidate's actual failing hidden tests, which this
# analyzer never inspects.
_KNOWN_SPECIFICATION_NOTES: dict[str, tuple[str, ...]] = {
    "package_resolver": (
        "docs/research_protocol.md, 'Known task-specific ambiguities': any "
        "package_resolver hidden-test failure involving mutual/circular "
        "package references where one package was already fully resolved "
        "before the reference back to it was encountered must be classified "
        "'ambiguous' (not 'benchmark_mismatch' or 'valid_failure') unless "
        "specification.md is later revised to resolve that exact case.",
    ),
    "json_parser": (
        "docs/pilot_findings.md records a prior, different candidate's "
        "benchmark_mismatch adjudication for NaN/Infinity/-Infinity "
        "acceptance vs. specification.md's explicit 'No Infinity, NaN, or "
        "hex.' rule; check whether this candidate's failing hidden "
        "expectations involve the same or a different behavior before "
        "adjudicating.",
    ),
}

_REPETITIONS_PER_CONDITION = 3
_ALL_CONDITIONS = tuple(Condition)


class CampaignAnalysisError(RuntimeError):
    """Safe, secret-free error for campaign-analysis failures."""


class CampaignValidationError(CampaignAnalysisError):
    """Raised when a completed campaign fails any pre-analysis validation check.

    Every message on this type is safe to print: it may reference a task
    id, UUID, seed, or hash, but never hidden-test source, a full prompt,
    a secret, or an absolute filesystem path outside the repository.
    """


class ProtocolProvenanceResult(BaseModel):
    """Result of validating ``docs/research_protocol.md``'s provenance.

    See :class:`~experiment.analysis_models.RawArtifactProvenance` for the
    exact meaning of each field; this is the pre-report-construction
    computation of those same four values.
    """

    model_config = ConfigDict(frozen=True)

    campaign_protocol_sha256: str
    analyzed_protocol_sha256: str
    protocol_changed_after_collection: bool
    protocol_change_verified_append_only: bool


class _AdjudicationRecord(BaseModel):
    """Defensive, typed view of one entry in ``research/adjudications.json``."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    candidate_id: Optional[uuid.UUID] = None
    candidate_source_sha256: Optional[str] = None
    benchmark_pass: bool
    specification_correct: Optional[bool] = None
    adjudication: str
    excluded_from_primary_analysis: bool = False
    exclusion_reason: Optional[str] = None


def default_results_dir() -> Path:
    """``research/results``, resolved relative to this file."""

    return repo_root() / "research" / "results"


def default_adjudications_path() -> Path:
    return repo_root() / "research" / "adjudications.json"


def default_protocol_path() -> Path:
    return repo_root() / "docs" / "research_protocol.md"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


class CampaignAnalyzer:
    """Validates and analyzes exactly one completed campaign."""

    def __init__(
        self,
        *,
        campaigns_dir: Optional[Path] = None,
        candidates_dir: Optional[Path] = None,
        experiments_dir: Optional[Path] = None,
        tasks_root: Optional[Path] = None,
        protocol_path: Optional[Path] = None,
        adjudications_path: Optional[Path] = None,
        repo: Optional[Path] = None,
        inspect_repo=None,
    ) -> None:
        self._repo = Path(repo) if repo is not None else repo_root()
        self._campaigns_dir = (
            Path(campaigns_dir) if campaigns_dir is not None else default_campaigns_dir()
        )
        self._candidates_dir = (
            Path(candidates_dir) if candidates_dir is not None else default_candidates_dir()
        )
        self._experiments_dir = (
            Path(experiments_dir) if experiments_dir is not None else default_experiments_dir()
        )
        self._protocol_path = (
            Path(protocol_path) if protocol_path is not None else default_protocol_path()
        )
        self._adjudications_path = (
            Path(adjudications_path) if adjudications_path is not None else default_adjudications_path()
        )
        self._inspect_repo = inspect_repo if inspect_repo is not None else inspect_repository
        self._candidate_loader = CandidateArtifactLoader(candidates_root=self._candidates_dir)
        self._task_loader = TaskLoader(tasks_root=tasks_root)
        self._task_cache: dict[str, LoadedTask] = {}

    # -- public API ---------------------------------------------------

    def analyze(self, campaign_id: Union[str, uuid.UUID]) -> CampaignAnalysisReport:
        campaign = load_campaign(campaign_id, self._campaigns_dir)
        checks: list[ValidationCheck] = []

        def record(name: str, detail: str) -> None:
            checks.append(ValidationCheck(name=name, detail=detail))

        def require(name: str, condition: bool, detail: str) -> None:
            if not condition:
                raise CampaignValidationError(f"{name}: {detail}")
            record(name, detail)

        require(
            "campaign_completed",
            campaign.status == "completed",
            f"campaign status is {campaign.status!r}; analysis requires status='completed'",
        )

        protocol_provenance = self._validate_protocol_provenance(campaign, require)

        self._validate_task_quotas(campaign, require)
        self._validate_no_duplicates(campaign, require)
        self._validate_no_pilot_inclusion(campaign, require)

        candidates: list[CandidateAnalysis] = []
        candidate_metadata_hashes: dict[str, str] = {}
        experiment_artifact_hashes: dict[str, str] = {}
        spec_hashes_by_task: dict[str, set[str]] = {}
        visible_hashes_by_task: dict[str, set[str]] = {}
        prompt_versions: set[str] = set()
        reviewer_providers: set[str] = set()
        reviewer_models: set[str] = set()

        ordered_records = sorted(campaign.qualifying_candidates, key=lambda r: r.qualification_index)
        require(
            "qualification_index_sequence",
            [r.qualification_index for r in ordered_records] == list(range(len(ordered_records))),
            "qualification_index values must be exactly 0..N-1 with no gaps or duplicates",
        )

        for record_item in ordered_records:
            loaded_candidate, experiment = self._validate_and_load_candidate(
                campaign, record_item, require
            )
            candidate_metadata_hashes[str(record_item.candidate_id)] = _sha256_file(
                loaded_candidate.artifact_dir / "metadata.json"
            )
            experiment_path = self._experiment_path(record_item)
            experiment_artifact_hashes[str(record_item.experiment_id)] = _sha256_file(experiment_path)

            spec_hashes_by_task.setdefault(record_item.task_id, set()).add(
                experiment.metadata.specification_sha256 or ""
            )
            visible_hashes_by_task.setdefault(record_item.task_id, set()).add(
                experiment.metadata.visible_tests_sha256 or ""
            )
            prompt_versions.add(experiment.metadata.prompt_version)
            reviewer_providers.add(experiment.metadata.provider)
            reviewer_models.add(experiment.metadata.model)

            candidates.append(self._build_candidate_analysis(record_item, experiment))

        for task_id, hashes in spec_hashes_by_task.items():
            require(
                "specification_hash_consistent_within_task",
                len(hashes) == 1 and "" not in hashes,
                f"task {task_id!r} has inconsistent/missing specification_sha256 across its "
                "campaign experiments; refusing to analyze possibly-tampered provenance",
            )
        for task_id, hashes in visible_hashes_by_task.items():
            require(
                "visible_tests_hash_consistent_within_task",
                len(hashes) == 1 and "" not in hashes,
                f"task {task_id!r} has inconsistent/missing visible_tests_sha256 across its "
                "campaign experiments; refusing to analyze possibly-tampered provenance",
            )
        require(
            "single_prompt_version_across_campaign",
            len(prompt_versions) == 1,
            f"expected exactly one prompt_version across all reviewer experiments, found {sorted(prompt_versions)}",
        )
        require(
            "reviewer_provider_matches_config",
            reviewer_providers == {campaign.config.reviewer_provider},
            f"expected reviewer provider {{{campaign.config.reviewer_provider!r}}}, found {sorted(reviewer_providers)}",
        )
        require(
            "reviewer_model_matches_config",
            reviewer_models == {campaign.config.reviewer_model},
            f"expected reviewer model {{{campaign.config.reviewer_model!r}}}, found {sorted(reviewer_models)}",
        )

        expected_total = campaign.config.target_per_task * len(campaign.config.task_ids)
        total_observations = sum(
            sum(c.conditions[condition.value].observation_count for condition in _ALL_CONDITIONS)
            for c in candidates
        )
        require(
            "total_reviewer_observation_count",
            total_observations == expected_total * campaign.config.repetitions * len(_ALL_CONDITIONS),
            f"expected {expected_total * campaign.config.repetitions * len(_ALL_CONDITIONS)} total "
            f"reviewer observations, found {total_observations}",
        )

        adjudication_index, adjudications_raw_bytes = self._load_adjudications()
        candidates_with_adjudication: list[CandidateAnalysis] = []
        adjudication_queue: list[AdjudicationQueueItem] = []
        for candidate in candidates:
            resolved, queue_item = self._resolve_adjudication(candidate, adjudication_index)
            candidates_with_adjudication.append(resolved)
            if queue_item is not None:
                adjudication_queue.append(queue_item)

        eligible = [c for c in candidates_with_adjudication if c.eligible_for_primary_analysis]
        excluded = [
            c
            for c in candidates_with_adjudication
            if not c.eligible_for_primary_analysis and c.adjudication_status != "pending"
        ]
        pending = [c for c in candidates_with_adjudication if c.adjudication_status == "pending"]

        full_cohort_summaries = self._cohort_summaries(candidates_with_adjudication, "full_cohort", campaign.config.task_ids)
        primary_eligible_summaries = self._cohort_summaries(eligible, "primary_eligible", campaign.config.task_ids)

        campaign_artifact_hash = _sha256_file(campaign_json_path(campaign.campaign_id, self._campaigns_dir))
        analysis_commit, analysis_dirty = self._inspect_repo(self._repo)

        notes = [
            "Exploratory study (n<=12 candidates per docs/research_protocol.md): no p-values, "
            "confidence intervals, or claims of statistical significance are computed or implied "
            "anywhere in this report.",
            "The pre-protocol pilot candidate/experiment (docs/pilot_findings.md) is excluded by "
            "design and is never pooled into this 12-candidate campaign analysis.",
            "The estimand and the A/B/C experimental conditions were preregistered in "
            "docs/research_protocol.md before data collection. 'primary_eligible_summaries' is the "
            "primary estimand evaluated on the eligible set produced under the transparently "
            "disclosed post-data adjudication amendment (see the protocol-amendment note below) -- "
            "it is not merely 'the preregistered primary analysis', because which candidates are "
            "eligible depends on a rule that was itself added after data collection. "
            "'full_cohort_summaries' is a separate, purely descriptive view over every qualifying "
            "candidate regardless of adjudication status, and its statistics do not depend on that "
            "eligibility rule at all -- excluding or including any candidate from "
            "'primary_eligible_summaries' never changes any value under 'full_cohort_summaries'.",
        ]
        if pending:
            notes.append(
                f"{len(pending)} of {len(candidates_with_adjudication)} candidate(s) have a hidden-suite "
                "failure with no existing research/adjudications.json record and are marked "
                "'pending' -- excluded from primary_eligible summaries until a human adjudicator "
                "records a decision per docs/research_protocol.md. See adjudication_queue.json."
            )
        if eligible and len(eligible) < len(candidates_with_adjudication):
            notes.append(
                "Any statistic computed only over primary_eligible candidates reflects a smaller, "
                "adjudication-filtered subset of the full campaign; see candidate_count on each "
                "primary_eligible summary row."
            )
        if protocol_provenance.protocol_changed_after_collection:
            notes.append(
                "docs/research_protocol.md was amended (verified, via provenance."
                "protocol_change_verified_append_only, to be a pure append -- never an edit or "
                "removal of pre-existing text) after this campaign's data was fully collected -- "
                "see 'Amendments' in the protocol. provenance.campaign_protocol_sha256 (recorded at "
                "campaign creation, never overwritten) vs. provenance.analyzed_protocol_sha256 "
                "(hashed just now) differ for exactly this reason; provenance."
                "protocol_changed_after_collection is True. The amendment adds a post-data, "
                "non-preregistered candidate-level adjudication precedence rule for mixed-cause "
                "hidden-suite failures -- it determines which candidates are eligible for the "
                "primary estimand (see the note above), but it did not change this campaign's "
                "preregistered target sample, hypotheses, estimands, experimental conditions, "
                "seeds, or stopping rule, and no already-collected observation was regenerated, "
                "rerun, or edited because of it."
            )

        report = CampaignAnalysisReport(
            schema_version=ANALYSIS_SCHEMA_VERSION,
            campaign_id=campaign.campaign_id,
            campaign_status=campaign.status,
            campaign_repository_commit=campaign.provenance.repository_commit,
            campaign_repository_dirty=campaign.provenance.repository_dirty,
            analysis_repository_commit=analysis_commit,
            analysis_repository_dirty=analysis_dirty,
            protocol_path=campaign.provenance.protocol_path,
            config=CampaignAnalysisConfigSnapshot(
                generator_provider=campaign.config.generator_provider,
                generator_model=campaign.config.generator_model,
                reviewer_provider=campaign.config.reviewer_provider,
                reviewer_model=campaign.config.reviewer_model,
                task_ids=campaign.config.task_ids,
                target_per_task=campaign.config.target_per_task,
                repetitions=campaign.config.repetitions,
                generation_seed_start=campaign.config.generation_seed_start,
                reviewer_seed_start=campaign.config.reviewer_seed_start,
                max_candidate_attempts=campaign.config.max_candidate_attempts,
                cutoff_utc=campaign.config.cutoff.utc.isoformat(),
            ),
            totals=CampaignAnalysisTotals(
                qualifying_candidate_count=len(candidates_with_adjudication),
                generation_attempt_count=len(campaign.generation_attempts),
                attrition_count=len(campaign.attrition),
                reviewer_observation_count=total_observations,
            ),
            provenance=RawArtifactProvenance(
                campaign_artifact_sha256=campaign_artifact_hash,
                candidate_metadata_sha256=dict(sorted(candidate_metadata_hashes.items())),
                experiment_artifact_sha256=dict(sorted(experiment_artifact_hashes.items())),
                campaign_protocol_sha256=protocol_provenance.campaign_protocol_sha256,
                analyzed_protocol_sha256=protocol_provenance.analyzed_protocol_sha256,
                protocol_changed_after_collection=protocol_provenance.protocol_changed_after_collection,
                protocol_change_verified_append_only=(
                    protocol_provenance.protocol_change_verified_append_only
                ),
                adjudications_file_sha256=_sha256_bytes(adjudications_raw_bytes),
            ),
            validation=ValidationReport(campaign_status=campaign.status, checks=checks),
            candidate_count=len(candidates_with_adjudication),
            eligible_count=len(eligible),
            excluded_count=len(excluded),
            pending_count=len(pending),
            candidates=candidates_with_adjudication,
            full_cohort_summaries=full_cohort_summaries,
            primary_eligible_summaries=primary_eligible_summaries,
            adjudication_queue=adjudication_queue,
            notes=notes,
        )
        return report

    def write_outputs(self, report: CampaignAnalysisReport, output_dir: Path) -> dict[str, str]:
        """Write every sanitized output file deterministically. Returns path->sha256."""

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        written: dict[str, str] = {}

        def write(name: str, text: str) -> None:
            _atomic_write_text(output_dir / name, text)
            written[name] = _sha256_bytes(text.encode("utf-8"))

        write("analysis.json", json.dumps(report.model_dump(mode="json"), indent=2) + "\n")
        write("candidate_results.csv", self._render_candidate_csv(report))
        write("condition_summary.csv", self._render_condition_summary_csv(report))
        write("paired_differences.csv", self._render_paired_differences_csv(report))
        write(
            "adjudication_queue.json",
            json.dumps(
                {
                    "schema_version": report.schema_version,
                    "campaign_id": str(report.campaign_id),
                    "campaign_artifact_sha256": report.provenance.campaign_artifact_sha256,
                    "pending_count": len(report.adjudication_queue),
                    "items": [item.model_dump(mode="json") for item in report.adjudication_queue],
                },
                indent=2,
            )
            + "\n",
        )
        write("README.md", self._render_readme(report))
        return written

    # -- validation helpers --------------------------------------------

    def _validate_task_quotas(self, campaign: CampaignArtifact, require) -> None:
        recomputed: dict[str, int] = {task_id: 0 for task_id in campaign.config.task_ids}
        for r in campaign.qualifying_candidates:
            recomputed[r.task_id] = recomputed.get(r.task_id, 0) + 1
        require(
            "qualifying_counts_field_matches_recorded_candidates",
            dict(campaign.qualifying_counts) == recomputed,
            f"campaign.qualifying_counts={dict(campaign.qualifying_counts)} does not match "
            f"recomputed counts from qualifying_candidates={recomputed}",
        )
        for task_id in campaign.config.task_ids:
            require(
                "task_quota_met_exactly",
                recomputed.get(task_id, 0) == campaign.config.target_per_task,
                f"task {task_id!r} has {recomputed.get(task_id, 0)} qualifying candidates, "
                f"expected exactly target_per_task={campaign.config.target_per_task}",
            )
        expected_total = campaign.config.target_per_task * len(campaign.config.task_ids)
        require(
            "total_qualifying_candidate_count",
            len(campaign.qualifying_candidates) == expected_total,
            f"expected exactly {expected_total} qualifying candidates, found "
            f"{len(campaign.qualifying_candidates)}",
        )
        require(
            "repetitions_matches_protocol",
            campaign.config.repetitions == _REPETITIONS_PER_CONDITION,
            f"docs/research_protocol.md requires exactly {_REPETITIONS_PER_CONDITION} repetitions "
            f"per condition, campaign config has repetitions={campaign.config.repetitions}",
        )

    def _validate_no_duplicates(self, campaign: CampaignArtifact, require) -> None:
        candidate_ids = [r.candidate_id for r in campaign.qualifying_candidates]
        experiment_ids = [r.experiment_id for r in campaign.qualifying_candidates]
        reviewer_seeds = [r.reviewer_seed for r in campaign.qualifying_candidates]
        generation_seeds_qualifying = [r.generation_seed for r in campaign.qualifying_candidates]
        all_generation_seeds = [a.generation_seed for a in campaign.generation_attempts]

        require(
            "no_duplicate_candidate_ids",
            len(candidate_ids) == len(set(candidate_ids)),
            "duplicate candidate_id found across qualifying_candidates",
        )
        require(
            "no_duplicate_experiment_ids",
            len(experiment_ids) == len(set(experiment_ids)) and None not in experiment_ids,
            "duplicate or missing experiment_id found across qualifying_candidates",
        )
        require(
            "no_duplicate_reviewer_seeds",
            len(reviewer_seeds) == len(set(reviewer_seeds)),
            "duplicate reviewer_seed found across qualifying_candidates",
        )
        require(
            "no_duplicate_generation_seeds_overall",
            len(all_generation_seeds) == len(set(all_generation_seeds)),
            "duplicate generation_seed found across campaign.generation_attempts",
        )
        require(
            "reviewer_seeds_are_the_expected_contiguous_sequence",
            sorted(reviewer_seeds)
            == list(
                range(
                    campaign.config.reviewer_seed_start,
                    campaign.config.reviewer_seed_start + len(reviewer_seeds),
                )
            ),
            "reviewer seeds must be exactly the contiguous sequence starting at "
            f"{campaign.config.reviewer_seed_start} with no reuse or gaps",
        )
        require(
            "generation_seeds_are_the_expected_contiguous_sequence",
            sorted(all_generation_seeds)
            == list(
                range(
                    campaign.config.generation_seed_start,
                    campaign.config.generation_seed_start + len(all_generation_seeds),
                )
            ),
            "generation-attempt seeds must be exactly the contiguous sequence starting at "
            f"{campaign.config.generation_seed_start} with no reuse or gaps",
        )
        require(
            "qualifying_generation_seeds_subset_of_generation_attempts",
            set(generation_seeds_qualifying) <= set(all_generation_seeds),
            "every qualifying candidate's generation_seed must correspond to a recorded "
            "generation attempt",
        )

    def _validate_no_pilot_inclusion(self, campaign: CampaignArtifact, require) -> None:
        candidate_ids = {r.candidate_id for r in campaign.qualifying_candidates}
        experiment_ids = {r.experiment_id for r in campaign.qualifying_candidates}
        generation_candidate_ids = {
            a.candidate_id for a in campaign.generation_attempts if a.candidate_id is not None
        }
        require(
            "pilot_candidate_not_included",
            PILOT_CANDIDATE_ID not in candidate_ids and PILOT_CANDIDATE_ID not in generation_candidate_ids,
            f"pilot candidate {PILOT_CANDIDATE_ID} (docs/pilot_findings.md) must never be included "
            "in a preregistered campaign's qualifying cohort",
        )
        require(
            "pilot_experiment_not_included",
            PILOT_EXPERIMENT_ID not in experiment_ids,
            f"pilot experiment {PILOT_EXPERIMENT_ID} (docs/pilot_findings.md) must never be "
            "included in a preregistered campaign's reviewer experiments",
        )

    def _validate_protocol_provenance(
        self, campaign: CampaignArtifact, require
    ) -> ProtocolProvenanceResult:
        """Validate the protocol document's provenance; return its four typed fields.

        Passes (i.e. does not raise) if either:

        1. The current ``docs/research_protocol.md`` bytes hash to exactly
           the ``research_protocol_sha256`` recorded at campaign creation
           (the common case: the protocol never changed), or
        2. The protocol was changed only by a **pure append** after
           campaign creation: the exact bytes committed at
           ``campaign.provenance.repository_commit`` (verified via
           ``git show``, read-only, local-only) hash to the recorded
           value, and the current file's bytes start with those exact
           historical bytes (i.e. every byte that existed at campaign
           creation is still present, unmodified, at the same offset;
           only new content was appended). This is exactly the amendment
           mechanism `docs/research_protocol.md` itself documents
           ("updated only with clearly marked amendments... not silently
           rewritten").

        Any other change -- an edit, deletion, insertion, or reordering of
        pre-existing text anywhere in the document, or a change that could
        not be independently verified against git history (including git
        itself being unavailable, the commit missing, or the recorded path
        missing at that commit) -- fails closed: this method raises via
        ``require`` and no report is ever produced. The campaign's own
        recorded ``research_protocol_sha256`` is read-only input here and
        is never recomputed or overwritten.
        """

        current_bytes = self._protocol_path.read_bytes()
        analyzed_hash = hashlib.sha256(current_bytes).hexdigest()
        campaign_hash = campaign.provenance.research_protocol_sha256

        if analyzed_hash == campaign_hash:
            require(
                "protocol_hash_matches_current_document",
                True,
                "docs/research_protocol.md's current SHA-256 matches the hash recorded at "
                "campaign creation (protocol unchanged since data collection)",
            )
            return ProtocolProvenanceResult(
                campaign_protocol_sha256=campaign_hash,
                analyzed_protocol_sha256=analyzed_hash,
                protocol_changed_after_collection=False,
                protocol_change_verified_append_only=False,
            )

        historical_bytes: Optional[bytes] = None
        if campaign.provenance.repository_commit:
            historical_bytes = git_show_file_bytes(
                self._repo, campaign.provenance.repository_commit, campaign.provenance.protocol_path
            )

        is_verified_pure_append = (
            historical_bytes is not None
            and hashlib.sha256(historical_bytes).hexdigest() == campaign_hash
            and current_bytes.startswith(historical_bytes)
            and len(current_bytes) > len(historical_bytes)
        )

        require(
            "protocol_hash_matches_current_document_or_is_a_verified_pure_append_amendment",
            is_verified_pure_append,
            "docs/research_protocol.md's current SHA-256 does not match the hash recorded at "
            "campaign creation, and the difference could not be verified (via `git show` against "
            "campaign.provenance.repository_commit) to be a pure append of new text after every "
            "byte that existed at campaign creation; the protocol must never be rewritten or have "
            "pre-existing text edited/removed after data collection, only appended to via a dated "
            "Amendments entry",
        )
        return ProtocolProvenanceResult(
            campaign_protocol_sha256=campaign_hash,
            analyzed_protocol_sha256=analyzed_hash,
            protocol_changed_after_collection=True,
            protocol_change_verified_append_only=True,
        )

    def _load_task(self, task_id: str) -> LoadedTask:
        if task_id not in self._task_cache:
            try:
                self._task_cache[task_id] = self._task_loader.load(task_id)
            except TaskLoadError as exc:
                raise CampaignValidationError(f"could not load task {task_id!r}: {exc}") from exc
        return self._task_cache[task_id]

    def _experiment_path(self, record_item: QualifyingCandidateRecord) -> Path:
        experiments_root = self._experiments_dir.resolve()
        path = experiments_root / f"{record_item.experiment_id}.json"
        if path.is_symlink():
            raise CampaignValidationError(
                f"refusing to read experiment artifact for candidate {record_item.candidate_id}: "
                "path is a symlink"
            )
        if not path.is_file():
            raise CampaignValidationError(
                f"missing experiment artifact for candidate {record_item.candidate_id} "
                f"(experiment_id={record_item.experiment_id})"
            )
        resolved = path.resolve()
        if resolved.parent != experiments_root:
            raise CampaignValidationError(
                f"experiment artifact path for candidate {record_item.candidate_id} escapes the "
                "expected experiments directory"
            )
        return resolved

    def _validate_and_load_candidate(
        self, campaign: CampaignArtifact, record_item: QualifyingCandidateRecord, require
    ) -> tuple[LoadedCandidate, ExperimentArtifact]:
        prefix = f"candidate[{record_item.qualification_index}]={record_item.candidate_id}"

        require(
            f"{prefix}.experiment_status_completed",
            record_item.experiment_status == "completed" and record_item.experiment_id is not None,
            f"{prefix} experiment_status is {record_item.experiment_status!r}, expected 'completed'",
        )
        require(
            f"{prefix}.generation_artifact_path_present",
            record_item.generation_artifact_path is not None,
            f"{prefix} is missing generation_artifact_path",
        )
        require(
            f"{prefix}.experiment_artifact_path_present",
            record_item.experiment_artifact_path is not None,
            f"{prefix} is missing experiment_artifact_path",
        )

        task = self._load_task(record_item.task_id)
        try:
            loaded_candidate = self._candidate_loader.load(task, record_item.candidate_id)
        except CandidateArtifactLoadError as exc:
            raise CampaignValidationError(
                f"{prefix}: candidate artifact failed verification: {exc}"
            ) from exc

        require(
            f"{prefix}.candidate_metadata_task_id_matches",
            loaded_candidate.metadata.task_id == record_item.task_id,
            f"{prefix}: candidate metadata task_id={loaded_candidate.metadata.task_id!r} does not "
            f"match campaign record task_id={record_item.task_id!r}",
        )
        require(
            f"{prefix}.candidate_source_hash_matches_campaign_record",
            loaded_candidate.metadata.final_source_sha256 == record_item.source_sha256,
            f"{prefix}: candidate source hash mismatch between metadata.json and campaign record",
        )
        require(
            f"{prefix}.candidate_generation_seed_matches",
            loaded_candidate.metadata.random_seed == record_item.generation_seed,
            f"{prefix}: candidate metadata random_seed={loaded_candidate.metadata.random_seed} does "
            f"not match campaign record generation_seed={record_item.generation_seed}",
        )
        require(
            f"{prefix}.candidate_provider_model_matches_config",
            loaded_candidate.metadata.provider == campaign.config.generator_provider
            and loaded_candidate.metadata.model == campaign.config.generator_model,
            f"{prefix}: candidate provider/model does not match campaign generator config",
        )

        if record_item.generation_attempt_index >= len(campaign.generation_attempts):
            raise CampaignValidationError(
                f"{prefix}: generation_attempt_index={record_item.generation_attempt_index} is out "
                f"of range for {len(campaign.generation_attempts)} recorded generation attempts"
            )
        attempt = campaign.generation_attempts[record_item.generation_attempt_index]
        require(
            f"{prefix}.generation_attempt_record_consistent",
            attempt.candidate_id == record_item.candidate_id
            and attempt.task_id == record_item.task_id
            and attempt.generation_seed == record_item.generation_seed
            and attempt.source_sha256 == record_item.source_sha256
            and attempt.status == "qualified",
            f"{prefix}: generation_attempts[{record_item.generation_attempt_index}] does not match "
            "this qualifying candidate record",
        )

        experiment_path = self._experiment_path(record_item)
        try:
            experiment = ExperimentArtifact.model_validate_json(
                experiment_path.read_text(encoding="utf-8")
            )
        except ValidationError as exc:
            raise CampaignValidationError(
                f"{prefix}: malformed experiment artifact: {exc}"
            ) from exc

        expected_relative = relative_artifact_identifier(
            experiment_path, fallback=f"experiments/{record_item.experiment_id}.json"
        )
        require(
            f"{prefix}.experiment_artifact_path_matches_record",
            expected_relative == record_item.experiment_artifact_path,
            f"{prefix}: recorded experiment_artifact_path does not match the resolved artifact path",
        )
        expected_candidate_relative = relative_artifact_identifier(
            loaded_candidate.artifact_dir,
            fallback=f"{record_item.task_id}/{record_item.candidate_id}",
        )
        require(
            f"{prefix}.generation_artifact_path_matches_record",
            expected_candidate_relative == record_item.generation_artifact_path,
            f"{prefix}: recorded generation_artifact_path does not match the resolved candidate directory",
        )

        require(
            f"{prefix}.experiment_status_is_completed",
            experiment.metadata.status == "completed",
            f"{prefix}: experiment metadata status={experiment.metadata.status!r}, expected 'completed'",
        )
        require(
            f"{prefix}.experiment_task_id_matches",
            experiment.metadata.task_id == record_item.task_id,
            f"{prefix}: experiment task_id mismatch",
        )
        require(
            f"{prefix}.experiment_candidate_id_matches",
            experiment.metadata.candidate_id == record_item.candidate_id,
            f"{prefix}: experiment observations are associated with a different candidate_id "
            f"({experiment.metadata.candidate_id}) than this campaign record ({record_item.candidate_id})",
        )
        require(
            f"{prefix}.experiment_candidate_source_hash_matches",
            experiment.metadata.candidate_source_sha256 == record_item.source_sha256
            == loaded_candidate.metadata.final_source_sha256,
            f"{prefix}: candidate_source_sha256 mismatch between experiment metadata, campaign "
            "record, and candidate metadata",
        )
        require(
            f"{prefix}.experiment_reviewer_seed_matches",
            experiment.metadata.random_seed == record_item.reviewer_seed,
            f"{prefix}: experiment random_seed={experiment.metadata.random_seed} does not match "
            f"campaign record reviewer_seed={record_item.reviewer_seed}",
        )
        require(
            f"{prefix}.experiment_repetitions_matches_config",
            experiment.metadata.repetitions == campaign.config.repetitions,
            f"{prefix}: experiment repetitions={experiment.metadata.repetitions} does not match "
            f"campaign config repetitions={campaign.config.repetitions}",
        )
        require(
            f"{prefix}.experiment_provider_model_matches_config",
            experiment.metadata.provider == campaign.config.reviewer_provider
            and experiment.metadata.model == campaign.config.reviewer_model,
            f"{prefix}: experiment provider/model does not match campaign reviewer config",
        )

        expected_observation_count = campaign.config.repetitions * len(_ALL_CONDITIONS)
        require(
            f"{prefix}.observation_count_matches_repetitions_times_conditions",
            len(experiment.observations) == expected_observation_count,
            f"{prefix}: expected {expected_observation_count} observations "
            f"({campaign.config.repetitions} repetitions x {len(_ALL_CONDITIONS)} conditions), "
            f"found {len(experiment.observations)}",
        )
        self._validate_condition_counts(prefix, experiment.observations, campaign.config.repetitions, require)
        self._validate_observation_fields(prefix, experiment, record_item, require)
        self._validate_hidden_after_reviews(prefix, experiment, require)

        require(
            f"{prefix}.ground_truth_present",
            experiment.ground_truth is not None,
            f"{prefix}: completed experiment is missing ground_truth",
        )
        assert experiment.ground_truth is not None
        require(
            f"{prefix}.visible_tests_passed",
            experiment.ground_truth.visible_passed,
            f"{prefix}: qualifying candidate's experiment does not show visible_passed=True",
        )
        require(
            f"{prefix}.visible_counts_match_candidate_metadata",
            experiment.ground_truth.visible_passed_count == loaded_candidate.metadata.visible_passed_count
            and experiment.ground_truth.visible_failed_count == loaded_candidate.metadata.visible_failed_count,
            f"{prefix}: experiment visible pass/fail counts do not match the candidate's own "
            "generation-time visible result",
        )
        require(
            f"{prefix}.hidden_result_present",
            experiment.ground_truth.hidden_passed is not None
            and experiment.ground_truth.hidden_passed_count is not None
            and experiment.ground_truth.hidden_failed_count is not None,
            f"{prefix}: completed experiment is missing hidden-test results",
        )

        return loaded_candidate, experiment

    def _validate_condition_counts(
        self,
        prefix: str,
        observations: list[ReviewerObservation],
        repetitions: int,
        require,
    ) -> None:
        for condition in _ALL_CONDITIONS:
            count = sum(1 for o in observations if o.condition == condition)
            require(
                "per_condition_observation_count_equals_repetitions",
                count == repetitions,
                f"{prefix}: condition {condition.value} has {count} observations, expected exactly "
                f"{repetitions}",
            )
        repetition_indices = sorted(o.repetition_index for o in observations)
        conditions_per_repetition: dict[int, set[Condition]] = {}
        for o in observations:
            conditions_per_repetition.setdefault(o.repetition_index, set()).add(o.condition)
        for index, conditions in conditions_per_repetition.items():
            require(
                "each_repetition_has_all_three_conditions",
                conditions == set(_ALL_CONDITIONS),
                f"{prefix}: repetition {index} does not have exactly one observation per condition",
            )
        require(
            "repetition_indices_are_expected_sequence",
            repetition_indices == sorted(list(range(repetitions)) * len(_ALL_CONDITIONS)),
            f"{prefix}: repetition_index values are not the expected 0..{repetitions - 1} sequence",
        )

    def _validate_observation_fields(
        self,
        prefix: str,
        experiment: ExperimentArtifact,
        record_item: QualifyingCandidateRecord,
        require,
    ) -> None:
        for obs in experiment.observations:
            require(
                "observation_condition_matches_result_condition",
                obs.condition == obs.result.condition,
                f"{prefix}: observation {obs.observation_id} condition does not match its result condition",
            )
            require(
                "observation_task_id_matches_record",
                obs.result.task_id == record_item.task_id,
                f"{prefix}: observation {obs.observation_id} task_id does not match this candidate's task",
            )

    def _validate_hidden_after_reviews(
        self, prefix: str, experiment: ExperimentArtifact, require
    ) -> None:
        if experiment.metadata.completed_at is None or not experiment.observations:
            return
        latest_observation = max(obs.result.timestamp for obs in experiment.observations)
        require(
            "hidden_evaluation_after_all_reviewer_observations",
            latest_observation <= experiment.metadata.completed_at,
            f"{prefix}: latest reviewer observation timestamp ({latest_observation.isoformat()}) is "
            f"after the experiment's completed_at ({experiment.metadata.completed_at.isoformat()}); "
            "hidden evaluation must only ever run after every reviewer observation completes",
        )
        earliest_observation = min(obs.result.timestamp for obs in experiment.observations)
        require(
            "reviewer_observations_after_experiment_start",
            earliest_observation >= experiment.metadata.started_at,
            f"{prefix}: earliest reviewer observation timestamp precedes experiment.started_at",
        )

    # -- computation helpers --------------------------------------------

    def _build_candidate_analysis(
        self, record_item: QualifyingCandidateRecord, experiment: ExperimentArtifact
    ) -> CandidateAnalysis:
        conditions: dict[str, ConditionObservationStats] = {}
        for condition in _ALL_CONDITIONS:
            obs = sorted(
                (o for o in experiment.observations if o.condition == condition),
                key=lambda o: o.repetition_index,
            )
            raw = [o.result.assessment.confidence for o in obs]
            latencies = [o.result.latency_seconds for o in obs if o.result.latency_seconds is not None]
            input_tokens = [float(o.result.input_tokens) for o in obs if o.result.input_tokens is not None]
            output_tokens = [float(o.result.output_tokens) for o in obs if o.result.output_tokens is not None]
            conditions[condition.value] = ConditionObservationStats(
                condition=condition,
                raw_confidence=raw,
                mean_confidence=_mean([float(v) for v in raw]) or 0.0,
                predicted_pass_count=sum(1 for o in obs if o.result.assessment.predicted_pass),
                observation_count=len(obs),
                mean_latency_seconds=_mean(latencies),
                mean_input_tokens=_mean(input_tokens),
                mean_output_tokens=_mean(output_tokens),
            )

        mean_a = conditions[Condition.A_NO_RESULT.value].mean_confidence
        mean_b = conditions[Condition.B_VISIBLE_PASS.value].mean_confidence
        mean_c = conditions[Condition.C_ADVERSARIAL.value].mean_confidence

        assert experiment.ground_truth is not None
        gt = experiment.ground_truth

        return CandidateAnalysis(
            qualification_index=record_item.qualification_index,
            task_id=record_item.task_id,
            candidate_id=record_item.candidate_id,
            candidate_source_sha256=record_item.source_sha256,
            generation_seed=record_item.generation_seed,
            generation_attempt_index=record_item.generation_attempt_index,
            generation_attempt_count=experiment.metadata.generation_attempt_count or 0,
            reviewer_seed=record_item.reviewer_seed,
            experiment_id=experiment.metadata.experiment_id,
            visible_passed_count=gt.visible_passed_count,
            visible_failed_count=gt.visible_failed_count,
            hidden_passed_count=gt.hidden_passed_count,
            hidden_failed_count=gt.hidden_failed_count,
            benchmark_pass=gt.hidden_passed,
            conditions=conditions,
            mean_confidence_a=mean_a,
            mean_confidence_b=mean_b,
            mean_confidence_c=mean_c,
            b_minus_a=mean_b - mean_a,
            c_minus_b=mean_c - mean_b,
            adjudication_status="pending",
            adjudication_source="pending_new_failure",
            specification_correct=None,
            eligible_for_primary_analysis=False,
            exclusion_reason=None,
        )

    def _load_adjudications(self) -> tuple[dict[tuple[str, str], _AdjudicationRecord], bytes]:
        try:
            raw_bytes = self._adjudications_path.read_bytes()
        except OSError as exc:
            raise CampaignAnalysisError(
                f"could not read adjudications file at {self._adjudications_path.name}: {exc}"
            ) from exc
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CampaignAnalysisError(f"research/adjudications.json is not valid JSON: {exc}") from exc

        index: dict[tuple[str, str], _AdjudicationRecord] = {}
        for entry in payload.get("adjudications", []):
            try:
                record = _AdjudicationRecord.model_validate(entry)
            except ValidationError as exc:
                raise CampaignAnalysisError(
                    f"research/adjudications.json has a malformed entry: {exc}"
                ) from exc
            if record.candidate_id is None or record.candidate_source_sha256 is None:
                continue
            index[(str(record.candidate_id), record.candidate_source_sha256)] = record
        return index, raw_bytes

    def _resolve_adjudication(
        self,
        candidate: CandidateAnalysis,
        adjudication_index: dict[tuple[str, str], _AdjudicationRecord],
    ) -> tuple[CandidateAnalysis, Optional[AdjudicationQueueItem]]:
        key = (str(candidate.candidate_id), candidate.candidate_source_sha256)
        matched = adjudication_index.get(key)

        if matched is not None:
            if matched.benchmark_pass != candidate.benchmark_pass:
                raise CampaignValidationError(
                    f"candidate {candidate.candidate_id}: research/adjudications.json records "
                    f"benchmark_pass={matched.benchmark_pass}, but the campaign's own experiment "
                    f"artifact measured benchmark_pass={candidate.benchmark_pass}"
                )
            eligible = not matched.excluded_from_primary_analysis
            resolved = candidate.model_copy(
                update={
                    "adjudication_status": matched.adjudication,
                    "adjudication_source": "recorded_adjudication",
                    "specification_correct": matched.specification_correct,
                    "eligible_for_primary_analysis": eligible,
                    "exclusion_reason": None if eligible else matched.exclusion_reason,
                }
            )
            return resolved, None

        if candidate.benchmark_pass is True:
            resolved = candidate.model_copy(
                update={
                    "adjudication_status": "valid_pass",
                    "adjudication_source": "auto_valid_pass",
                    "specification_correct": None,
                    "eligible_for_primary_analysis": True,
                    "exclusion_reason": None,
                }
            )
            return resolved, None

        reason = (
            "Pending human/specification adjudication: this candidate's hidden-suite failure has "
            "no existing research/adjudications.json record (matched by candidate_id + "
            "candidate_source_sha256). Per docs/research_protocol.md's ground-truth/adjudication "
            "policy, no adjudication may be invented; a human must classify this as valid_failure, "
            "benchmark_mismatch, or ambiguous before it can be included in primary_eligible summaries."
        )
        resolved = candidate.model_copy(
            update={
                "adjudication_status": "pending",
                "adjudication_source": "pending_new_failure",
                "specification_correct": None,
                "eligible_for_primary_analysis": False,
                "exclusion_reason": reason,
            }
        )
        queue_item = AdjudicationQueueItem(
            task_id=candidate.task_id,
            candidate_id=candidate.candidate_id,
            candidate_source_sha256=candidate.candidate_source_sha256,
            qualification_index=candidate.qualification_index,
            visible_passed_count=candidate.visible_passed_count,
            visible_failed_count=candidate.visible_failed_count,
            hidden_passed_count=candidate.hidden_passed_count,
            hidden_failed_count=candidate.hidden_failed_count,
            benchmark_pass=candidate.benchmark_pass,
            status="pending_adjudication",
            reason=reason,
            failing_test_identifiers=None,
            failing_test_identifiers_note=(
                "Not available: the harness records only aggregate hidden pass/fail counts "
                "(experiment.experiment_models.GroundTruth), never hidden-test identifiers, "
                "stdout/stderr, or source, to prevent hidden-suite leakage. A human adjudicator "
                "must inspect backend/tasks/<task_id>/hidden_tests/ directly (outside this "
                "sanitized report) to identify the specific failing test(s)."
            ),
            specification_reference_notes=list(_KNOWN_SPECIFICATION_NOTES.get(candidate.task_id, ())),
        )
        return resolved, queue_item

    def _cohort_summaries(
        self, candidates: list[CandidateAnalysis], scope: str, task_ids: tuple[str, ...]
    ) -> list[CohortSummary]:
        summaries = [self._cohort_summary(candidates, scope, None)]
        for task_id in task_ids:
            summaries.append(
                self._cohort_summary([c for c in candidates if c.task_id == task_id], scope, task_id)
            )
        return summaries

    def _cohort_summary(
        self, candidates: list[CandidateAnalysis], scope: str, task_id: Optional[str]
    ) -> CohortSummary:
        n = len(candidates)
        condition_summaries: list[CohortConditionSummary] = []
        for condition in _ALL_CONDITIONS:
            key = condition.value
            confidences = [c.conditions[key].mean_confidence for c in candidates]
            predicted = sum(c.conditions[key].predicted_pass_count for c in candidates)
            total_obs = sum(c.conditions[key].observation_count for c in candidates)
            latencies = [
                c.conditions[key].mean_latency_seconds
                for c in candidates
                if c.conditions[key].mean_latency_seconds is not None
            ]
            in_tok = [
                c.conditions[key].mean_input_tokens
                for c in candidates
                if c.conditions[key].mean_input_tokens is not None
            ]
            out_tok = [
                c.conditions[key].mean_output_tokens
                for c in candidates
                if c.conditions[key].mean_output_tokens is not None
            ]
            condition_summaries.append(
                CohortConditionSummary(
                    condition=key,
                    candidate_count=n,
                    mean_confidence=_mean(confidences),
                    predicted_pass_count=predicted,
                    predicted_pass_rate=(predicted / total_obs) if total_obs else None,
                    mean_latency_seconds=_mean(latencies),
                    mean_input_tokens=_mean(in_tok),
                    mean_output_tokens=_mean(out_tok),
                )
            )

        bench_pass = sum(1 for c in candidates if c.benchmark_pass is True)
        bench_fail = sum(1 for c in candidates if c.benchmark_pass is False)
        bench_unknown = sum(1 for c in candidates if c.benchmark_pass is None)

        return CohortSummary(
            scope=scope,  # type: ignore[arg-type]
            task_id=task_id,
            candidate_count=n,
            conditions=condition_summaries,
            mean_b_minus_a=_mean([c.b_minus_a for c in candidates]),
            mean_c_minus_b=_mean([c.c_minus_b for c in candidates]),
            benchmark_pass_count=bench_pass,
            benchmark_fail_count=bench_fail,
            benchmark_unknown_count=bench_unknown,
            benchmark_pass_rate=(bench_pass / n) if n else None,
        )

    # -- rendering helpers -----------------------------------------------

    @staticmethod
    def _render_candidate_csv(report: CampaignAnalysisReport) -> str:
        buffer = io.StringIO()
        fieldnames = [
            "qualification_index",
            "task_id",
            "candidate_id",
            "candidate_source_sha256",
            "generation_seed",
            "generation_attempt_count",
            "reviewer_seed",
            "experiment_id",
            "visible_passed_count",
            "visible_failed_count",
            "hidden_passed_count",
            "hidden_failed_count",
            "benchmark_pass",
        ]
        for cond in ("a", "b", "c"):
            fieldnames += [
                f"raw_confidence_{cond}_1",
                f"raw_confidence_{cond}_2",
                f"raw_confidence_{cond}_3",
                f"mean_confidence_{cond}",
                f"predicted_pass_count_{cond}",
                f"mean_latency_seconds_{cond}",
                f"mean_input_tokens_{cond}",
                f"mean_output_tokens_{cond}",
            ]
        fieldnames += [
            "b_minus_a",
            "c_minus_b",
            "adjudication_status",
            "adjudication_source",
            "specification_correct",
            "eligible_for_primary_analysis",
            "exclusion_reason",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        condition_key_by_letter = {
            "a": Condition.A_NO_RESULT.value,
            "b": Condition.B_VISIBLE_PASS.value,
            "c": Condition.C_ADVERSARIAL.value,
        }
        for candidate in sorted(report.candidates, key=lambda c: c.qualification_index):
            row: dict[str, object] = {
                "qualification_index": candidate.qualification_index,
                "task_id": candidate.task_id,
                "candidate_id": str(candidate.candidate_id),
                "candidate_source_sha256": candidate.candidate_source_sha256,
                "generation_seed": candidate.generation_seed,
                "generation_attempt_count": candidate.generation_attempt_count,
                "reviewer_seed": candidate.reviewer_seed,
                "experiment_id": str(candidate.experiment_id),
                "visible_passed_count": candidate.visible_passed_count,
                "visible_failed_count": candidate.visible_failed_count,
                "hidden_passed_count": candidate.hidden_passed_count,
                "hidden_failed_count": candidate.hidden_failed_count,
                "benchmark_pass": candidate.benchmark_pass,
            }
            for letter, key in condition_key_by_letter.items():
                stats = candidate.conditions[key]
                raw = stats.raw_confidence + [None] * (3 - len(stats.raw_confidence))
                row[f"raw_confidence_{letter}_1"] = raw[0]
                row[f"raw_confidence_{letter}_2"] = raw[1]
                row[f"raw_confidence_{letter}_3"] = raw[2]
                row[f"mean_confidence_{letter}"] = stats.mean_confidence
                row[f"predicted_pass_count_{letter}"] = stats.predicted_pass_count
                row[f"mean_latency_seconds_{letter}"] = stats.mean_latency_seconds
                row[f"mean_input_tokens_{letter}"] = stats.mean_input_tokens
                row[f"mean_output_tokens_{letter}"] = stats.mean_output_tokens
            row["b_minus_a"] = candidate.b_minus_a
            row["c_minus_b"] = candidate.c_minus_b
            row["adjudication_status"] = candidate.adjudication_status
            row["adjudication_source"] = candidate.adjudication_source
            row["specification_correct"] = candidate.specification_correct
            row["eligible_for_primary_analysis"] = candidate.eligible_for_primary_analysis
            row["exclusion_reason"] = candidate.exclusion_reason
            writer.writerow(row)
        return buffer.getvalue()

    @staticmethod
    def _render_condition_summary_csv(report: CampaignAnalysisReport) -> str:
        buffer = io.StringIO()
        fieldnames = [
            "scope",
            "task_id",
            "condition",
            "candidate_count",
            "mean_confidence",
            "predicted_pass_count",
            "predicted_pass_rate",
            "mean_latency_seconds",
            "mean_input_tokens",
            "mean_output_tokens",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for scope_name, summaries in (
            ("full_cohort", report.full_cohort_summaries),
            ("primary_eligible", report.primary_eligible_summaries),
        ):
            for summary in summaries:
                task_label = summary.task_id if summary.task_id is not None else "ALL"
                for cond_summary in summary.conditions:
                    writer.writerow(
                        {
                            "scope": scope_name,
                            "task_id": task_label,
                            "condition": cond_summary.condition,
                            "candidate_count": cond_summary.candidate_count,
                            "mean_confidence": cond_summary.mean_confidence,
                            "predicted_pass_count": cond_summary.predicted_pass_count,
                            "predicted_pass_rate": cond_summary.predicted_pass_rate,
                            "mean_latency_seconds": cond_summary.mean_latency_seconds,
                            "mean_input_tokens": cond_summary.mean_input_tokens,
                            "mean_output_tokens": cond_summary.mean_output_tokens,
                        }
                    )
        return buffer.getvalue()

    @staticmethod
    def _render_paired_differences_csv(report: CampaignAnalysisReport) -> str:
        buffer = io.StringIO()
        fieldnames = [
            "scope",
            "task_id",
            "candidate_count",
            "mean_b_minus_a",
            "mean_c_minus_b",
            "benchmark_pass_count",
            "benchmark_fail_count",
            "benchmark_unknown_count",
            "benchmark_pass_rate",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for scope_name, summaries in (
            ("full_cohort", report.full_cohort_summaries),
            ("primary_eligible", report.primary_eligible_summaries),
        ):
            for summary in summaries:
                writer.writerow(
                    {
                        "scope": scope_name,
                        "task_id": summary.task_id if summary.task_id is not None else "ALL",
                        "candidate_count": summary.candidate_count,
                        "mean_b_minus_a": summary.mean_b_minus_a,
                        "mean_c_minus_b": summary.mean_c_minus_b,
                        "benchmark_pass_count": summary.benchmark_pass_count,
                        "benchmark_fail_count": summary.benchmark_fail_count,
                        "benchmark_unknown_count": summary.benchmark_unknown_count,
                        "benchmark_pass_rate": summary.benchmark_pass_rate,
                    }
                )
        return buffer.getvalue()

    @staticmethod
    def _render_readme(report: CampaignAnalysisReport) -> str:
        overall_full = next(s for s in report.full_cohort_summaries if s.task_id is None)
        overall_eligible = next(s for s in report.primary_eligible_summaries if s.task_id is None)
        per_task_full = [s for s in report.full_cohort_summaries if s.task_id is not None]
        per_task_eligible = [s for s in report.primary_eligible_summaries if s.task_id is not None]

        def fmt(value: Optional[float]) -> str:
            return "n/a" if value is None else f"{value:.3f}"

        category_counts: dict[str, int] = {}
        for candidate in report.candidates:
            category_counts[candidate.adjudication_status] = (
                category_counts.get(candidate.adjudication_status, 0) + 1
            )
        for status in ("valid_pass", "valid_failure", "benchmark_mismatch", "ambiguous", "pending"):
            category_counts.setdefault(status, 0)

        excluded_candidates = sorted(
            (
                c
                for c in report.candidates
                if not c.eligible_for_primary_analysis and c.adjudication_status != "pending"
            ),
            key=lambda c: c.qualification_index,
        )
        pending_candidates = sorted(
            (c for c in report.candidates if c.adjudication_status == "pending"),
            key=lambda c: c.qualification_index,
        )
        eligible_candidates = sorted(
            (c for c in report.candidates if c.eligible_for_primary_analysis),
            key=lambda c: c.qualification_index,
        )
        eligible_tasks = sorted({c.task_id for c in eligible_candidates})
        all_tasks = sorted({c.task_id for c in report.candidates})
        tasks_with_no_eligible = [t for t in all_tasks if t not in eligible_tasks]
        protocol_amended = report.provenance.protocol_changed_after_collection

        lines = [
            f"# Campaign analysis: `{report.campaign_id}`",
            "",
            "Deterministic, sanitized output of `experiment.campaign_analyzer.CampaignAnalyzer`. "
            "Generated by:",
            "",
            "```",
            f"python -m experiment.cli campaign-analyze --campaign-id {report.campaign_id}",
            "```",
            "",
            "This directory is safe to commit: it contains no API keys, no OpenAI response ids, "
            "no full prompts, no full candidate source, no full visible/hidden test source, no raw "
            "traceback dumps, and no absolute local filesystem paths.",
            "",
            "**Read this file top to bottom in order.** Section 1 reports the primary estimand "
            "evaluated on the eligible set produced under the transparently disclosed post-data "
            "adjudication amendment -- the estimand and the A/B/C experimental conditions were "
            "preregistered in `docs/research_protocol.md`, but the specific mixed-failure "
            "precedence rule that decides *which* candidates are eligible was **not** "
            "preregistered (see section 4). Section 2 is a separate descriptive view over all 12 "
            "qualifying candidates whose results do **not** depend on that eligibility rule at "
            "all. They answer different questions and must not be conflated.",
            "",
            "## Provenance",
            "",
            f"- Analysis schema version: `{report.schema_version}`",
            f"- Campaign status: `{report.campaign_status}`",
            f"- Campaign repository commit: `{report.campaign_repository_commit}`",
            f"- Analysis repository commit: `{report.analysis_repository_commit}`",
            f"- Protocol path: `{report.protocol_path}`",
            f"- `campaign_protocol_sha256` (recorded at campaign creation, never overwritten): "
            f"`{report.provenance.campaign_protocol_sha256}`",
            f"- `analyzed_protocol_sha256` (hash of the protocol document at analysis time): "
            f"`{report.provenance.analyzed_protocol_sha256}`",
            f"- `protocol_changed_after_collection`: `{str(report.provenance.protocol_changed_after_collection).lower()}`",
            f"- `protocol_change_verified_append_only`: "
            f"`{str(report.provenance.protocol_change_verified_append_only).lower()}`",
        ]
        if protocol_amended:
            lines.append(
                "  - **`campaign_protocol_sha256` and `analyzed_protocol_sha256` differ on "
                "purpose**: the protocol was amended (verified, via `git show` against the "
                "campaign-creation commit, to be a pure append -- never an edit or removal of "
                "pre-existing text) *after* this campaign's data was fully collected. See section "
                "4 below and the protocol's own 'Amendments' section."
            )
        lines += [
            f"- Campaign artifact SHA-256: `{report.provenance.campaign_artifact_sha256}`",
            f"- research/adjudications.json SHA-256: `{report.provenance.adjudications_file_sha256}`",
            "",
            "## Cohort",
            "",
            f"- Qualifying candidates loaded: {report.candidate_count} "
            f"({report.totals.reviewer_observation_count} reviewer observations)",
            f"- Generation attempts: {report.totals.generation_attempt_count} "
            f"(attrition: {report.totals.attrition_count})",
            f"- Eligible for primary analysis: {report.eligible_count}",
            f"- Excluded (adjudicated benchmark_mismatch/ambiguous): {report.excluded_count}",
            f"- Pending human adjudication: {report.pending_count}",
            f"- Benchmark pass rate (full cohort): {fmt(overall_full.benchmark_pass_rate)} "
            f"({overall_full.benchmark_pass_count}/{overall_full.candidate_count})",
            "- Adjudication category counts (all 12 candidates): "
            f"`valid_pass`={category_counts['valid_pass']}, "
            f"`valid_failure`={category_counts['valid_failure']}, "
            f"`benchmark_mismatch`={category_counts['benchmark_mismatch']}, "
            f"`ambiguous`={category_counts['ambiguous']}, "
            f"`pending`={category_counts['pending']}",
            "",
            "---",
            "",
            "## 1. Primary estimand, evaluated on the eligible set produced under the post-data "
            "adjudication amendment",
            "",
            "**Primary estimand evaluated on the eligible set produced under the transparently "
            "disclosed post-data adjudication amendment.** Precisely:",
            "",
            "- The primary/secondary **estimand** and the A/B/C **experimental conditions** below "
            "were **preregistered** in `docs/research_protocol.md` before any data was collected.",
            "- The **mixed-failure precedence rule** that decides which candidates count as "
            "eligible (Amendment 1, section 4 below) was **not preregistered** -- it was added "
            "after this campaign's data was already collected and each candidate's failure causes "
            "had already been inspected.",
            "- Per the adjudication policy (rule 4) plus that post-data precedence rule, this "
            "section **excludes** every candidate adjudicated `benchmark_mismatch` or `ambiguous`, "
            "and includes only `valid_pass`/`valid_failure` candidates. This is not simply \"the "
            "preregistered primary analysis\" -- the estimand was preregistered, but this "
            "particular eligible *set* was not, and a different (still-defensible) post-data rule "
            "could have produced a different eligible set from the same underlying data.",
            "",
            f"- **Eligible candidates: {overall_eligible.candidate_count} of {overall_full.candidate_count}**",
        ]
        if eligible_candidates:
            lines.append(
                "- Eligible candidate IDs (full, qualification_index): "
                + "; ".join(
                    f"`{c.candidate_id}` (idx {c.qualification_index}, {c.task_id})"
                    for c in eligible_candidates
                )
            )
        if tasks_with_no_eligible:
            lines.append(
                "- **Task coverage of the eligible subset is incomplete**: eligible candidates come "
                f"only from {', '.join(f'`{t}`' for t in eligible_tasks) or 'no task'}; "
                f"{', '.join(f'`{t}`' for t in tasks_with_no_eligible)} contributed **zero** eligible "
                "candidates in this campaign (see Limitations)."
            )
        lines += [
            "",
            "| Condition | Mean confidence | Predicted-pass rate |",
            "|---|---|---|",
        ]
        for cond in overall_eligible.conditions:
            lines.append(
                f"| {cond.condition} | {fmt(cond.mean_confidence)} | {fmt(cond.predicted_pass_rate)} |"
            )
        lines += [
            "",
            f"- Mean candidate-level B-A: {fmt(overall_eligible.mean_b_minus_a)}",
            f"- Mean candidate-level C-B: {fmt(overall_eligible.mean_c_minus_b)}",
        ]
        if per_task_eligible:
            lines += ["", "Per-task breakdown (primary-eligible subset):", ""]
            lines += ["| Task | Eligible n | Mean B-A | Mean C-B |", "|---|---|---|---|"]
            for summary in per_task_eligible:
                lines.append(
                    f"| {summary.task_id} | {summary.candidate_count} | "
                    f"{fmt(summary.mean_b_minus_a)} | {fmt(summary.mean_c_minus_b)} |"
                )
        lines += [
            "",
            "---",
            "",
            "## 2. Complete 12-candidate descriptive analysis (full cohort, all adjudication statuses)",
            "",
            "**This is not the primary-estimand analysis in section 1, and its results do not "
            "depend on the post-data eligibility rule at all.** It is a separate, purely "
            "descriptive view over *every* qualifying candidate regardless of adjudication "
            "status, reported prominently per the adjudication policy's rule 5 (a candidate's raw "
            "`benchmark_pass`/reviewer-confidence results are always reported unchanged, even when "
            "excluded from the section 1 eligible set). Whether Amendment 1 classifies any given "
            "candidate as `valid_failure`, `ambiguous`, or `benchmark_mismatch` never changes a "
            "single number below -- every one of the 12 candidates is always included here.",
            "",
            "| Condition | Mean confidence | Predicted-pass rate |",
            "|---|---|---|",
        ]
        for cond in overall_full.conditions:
            lines.append(
                f"| {cond.condition} | {fmt(cond.mean_confidence)} | {fmt(cond.predicted_pass_rate)} |"
            )
        lines += [
            "",
            f"- Mean candidate-level B-A: {fmt(overall_full.mean_b_minus_a)}",
            f"- Mean candidate-level C-B: {fmt(overall_full.mean_c_minus_b)}",
            f"- Benchmark pass rate: {fmt(overall_full.benchmark_pass_rate)} "
            f"({overall_full.benchmark_pass_count}/{overall_full.candidate_count}) -- "
            "every one of the 12 qualifying candidates in this campaign has at least one "
            "hidden-suite failure.",
        ]
        if per_task_full:
            lines += ["", "Per-task breakdown (full cohort, all 12):", ""]
            lines += ["| Task | n | Mean B-A | Mean C-B | Benchmark pass rate |", "|---|---|---|---|---|"]
            for summary in per_task_full:
                lines.append(
                    f"| {summary.task_id} | {summary.candidate_count} | "
                    f"{fmt(summary.mean_b_minus_a)} | {fmt(summary.mean_c_minus_b)} | "
                    f"{fmt(summary.benchmark_pass_rate)} ({summary.benchmark_pass_count}/{summary.candidate_count}) |"
                )
        lines += [
            "",
            "---",
            "",
            "## 3. Exclusions (candidates omitted from section 1)",
            "",
        ]
        if excluded_candidates or pending_candidates:
            lines += [
                "| Qualification idx | Task | Candidate ID | Category | Reason |",
                "|---|---|---|---|---|",
            ]
            for c in excluded_candidates:
                reason = (c.exclusion_reason or "").replace("|", "\\|")
                lines.append(
                    f"| {c.qualification_index} | {c.task_id} | `{c.candidate_id}` | "
                    f"`{c.adjudication_status}` | {reason} |"
                )
            for c in pending_candidates:
                reason = (c.exclusion_reason or "pending human adjudication").replace("|", "\\|")
                lines.append(
                    f"| {c.qualification_index} | {c.task_id} | `{c.candidate_id}` | "
                    f"`pending` | {reason} |"
                )
        else:
            lines.append("No candidates are excluded or pending in this campaign.")
        lines += [
            "",
            "Full per-candidate evidence (failure clusters, specification quotes, reasoning) is "
            "recorded in `research/adjudications.json`, matched by `candidate_id` + "
            "`candidate_source_sha256`; it is not duplicated here to keep this report sanitized "
            "and free of hidden-test detail.",
            "",
            "---",
            "",
            "## 4. Post-data adjudication precedence amendment",
            "",
            "`docs/research_protocol.md` did not originally specify which adjudication category "
            "applies to a candidate whose hidden-suite failures have more than one distinct cause "
            "(e.g. one failure that is a clear specification violation plus another that is the "
            "documented `package_resolver` circular-dependency ambiguity). This gap was discovered "
            "only while adjudicating this campaign's 12 candidates, **after** all generation, "
            "reviewer, and hidden-test data had already been collected.",
            "",
            "**Amendment 1** (dated 2026-09-20, appended to the protocol's 'Amendments' section, "
            "never rewriting or removing any pre-existing protocol text) adds this post-data, "
            "non-preregistered candidate-level precedence rule:",
            "",
            "1. `valid_pass` -- the complete hidden suite passes.",
            "2. `valid_failure` -- at least one failure is a clear specification violation; takes "
            "precedence over any co-occurring `ambiguous`/`benchmark_mismatch`-flavored failures.",
            "3. `ambiguous` -- no clear violation exists, but at least one failure is genuinely "
            "underspecified/disputable.",
            "4. `benchmark_mismatch` -- every observed failure expects behavior contradicting the "
            "written specification.",
            "",
            "This rule **was not preregistered** and is disclosed here exactly as such. It did not "
            "change, and was not used to decide, this campaign's target sample, hypotheses, "
            "estimands, generation/reviewer seeds, A/B/C conditions, or stopping rule, and no "
            "already-collected observation was regenerated, rerun, or edited because of it -- it "
            "only determines which already-fixed `benchmark_pass`/adjudication-category "
            "combination is reported per candidate. It directly affects **inclusion** in section 1 "
            "(see Limitations below) and is therefore reported as a limitation, not silently "
            "folded into the primary analysis as if it had been the original policy.",
            "",
            "---",
            "",
            "## 5. Limitations",
            "",
            f"- **Eligible n={overall_eligible.candidate_count}, not up to {overall_full.candidate_count}**: "
            "applying Amendment 1 to this campaign leaves only "
            f"{overall_eligible.candidate_count} of {overall_full.candidate_count} candidates "
            "eligible for the primary calibration analysis. Any B-A/C-B estimate in section 1 is "
            "computed over this small subset and is illustrative only, consistent with "
            "`docs/research_protocol.md`'s existing exploratory-study framing (n<=12, no "
            "inferential statistics).",
        ]
        if tasks_with_no_eligible and eligible_tasks:
            lines.append(
                f"- **Only {'/'.join(f'`{t}`' for t in eligible_tasks)} is represented in the "
                f"eligible subset**: {'/'.join(f'`{t}`' for t in tasks_with_no_eligible)} "
                "contributed zero eligible candidates in this campaign, so section 1's statistics "
                "cannot speak to reviewer calibration on that task at all -- only to the task(s) "
                "actually represented."
            )
        lines += [
            "- **All 12 qualifying candidates have a hidden-suite failure**: the full-cohort "
            f"benchmark pass rate is {fmt(overall_full.benchmark_pass_rate)} "
            f"({overall_full.benchmark_pass_count}/{overall_full.candidate_count}); there is no "
            "`valid_pass` candidate in this campaign to compare against, so every eligible-subset "
            "candidate in section 1 is a `valid_failure` case, not a mix of passing and failing "
            "candidates.",
            "- **Adjudication sensitivity**: several category assignments in `research/"
            "adjudications.json` are qualitative human judgment calls on genuinely disputable "
            "cases (e.g. unpaired-surrogate handling, numeric overflow to +-inf, optional-"
            "dependency-vs-conflict skipping). A different, still-defensible reading of a task's "
            "`specification.md` could reclassify one or more `ambiguous` candidates as "
            "`valid_failure` or `benchmark_mismatch`, which would change `eligible_count` and the "
            "section 1 statistics; see the `evidence.reasoning` field per candidate for the exact "
            "judgment made.",
            "- Exploratory study (n<=12 candidates per `docs/research_protocol.md`): no p-values, "
            "confidence intervals, or claims of statistical significance are computed or implied "
            "anywhere in this report.",
            "- The pre-protocol pilot candidate/experiment (`docs/pilot_findings.md`) is excluded "
            "by design and never pooled into this 12-candidate campaign analysis.",
            "",
            "---",
            "",
            "## Adjudication queue",
            "",
        ]
        if report.adjudication_queue:
            lines.append(
                f"{len(report.adjudication_queue)} candidate(s) require human/specification "
                "adjudication -- see `adjudication_queue.json`. No hidden-test source or per-test "
                "identifiers are recorded anywhere in this repository's artifacts; a human "
                "adjudicator must inspect the task's `hidden_tests/` directly, outside this "
                "sanitized report."
            )
        else:
            lines.append(
                "No candidates are currently pending adjudication -- every one of the "
                f"{report.candidate_count} qualifying candidates has a recorded adjudication in "
                "`research/adjudications.json`."
            )
        lines += [
            "",
            "## Files",
            "",
            "- `analysis.json` — the complete structured report (this README is derived from it).",
            "- `candidate_results.csv` — one row per qualifying candidate.",
            "- `condition_summary.csv` — per-condition mean confidence/predicted-pass, by scope and task.",
            "- `paired_differences.csv` — mean candidate-level B-A / C-B and benchmark pass rate, by scope and task.",
            "- `adjudication_queue.json` — the minimum information needed for manual review of pending candidates.",
            "",
            "## Notes",
            "",
        ]
        for note in report.notes:
            lines.append(f"- {note}")
        lines.append("")
        return "\n".join(lines)
