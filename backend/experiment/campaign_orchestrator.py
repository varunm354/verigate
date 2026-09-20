"""Durable preregistered campaign runner.

Exact operational order, matching ``docs/research_protocol.md``:

    1. Create records immutable config, protocol hash, and git commit
       (refusing a dirty tracked worktree). No provider is initialized.
    2. Before each *new* generation attempt, check the Pacific cutoff and
       the maximum-generation guard. An attempt already underway is not
       interrupted.
    3. Select the next task by round-robin over configured task ids,
       skipping any task that has already reached its qualifying quota.
    4. Assign the current generation seed, increment the next seed, and
       checkpoint before generating.
    5. Run hidden-blind candidate generation via
       :class:`~experiment.candidate_orchestrator.CandidateGenerationOrchestrator`.
    6. A generation that does not pass visible tests is attrition and
       receives zero reviewer calls.
    7. A qualifying candidate receives the next reviewer seed in
       chronological qualifying order; that seed is checkpointed before
       the reviewer experiment starts.
    8. Invoke the existing candidate-aware
       :class:`~experiment.orchestrator.ExperimentOrchestrator` with the
       exact saved candidate, configured reviewer provider/model,
       configured repetitions, and the assigned reviewer seed. That
       orchestrator remains responsible for visible verification, all
       A/B/C reviews, then hidden evaluation.
    9. Confidence values and hidden-test outcomes are never read for
       scheduling, stopping, or replacement. Partial/failed reviewer
       experiments block the campaign and are not rerun.

Runs are sequential: this module never parallelizes generation or
reviewer calls.
"""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence, Union
from zoneinfo import ZoneInfo

from .campaign_models import (
    CAMPAIGN_SCHEMA_VERSION,
    CAMPAIGN_TASK_IDS,
    DEFAULT_CUTOFF_TIMEZONE,
    DEFAULT_GENERATION_SEED_START,
    DEFAULT_MAX_CANDIDATE_ATTEMPTS,
    DEFAULT_MAX_GENERATION_ATTEMPTS,
    DEFAULT_REPETITIONS,
    DEFAULT_REVIEWER_SEED_START,
    DEFAULT_TARGET_PER_TASK,
    AttritionRecord,
    CampaignArtifact,
    CampaignConfig,
    CampaignCutoff,
    CampaignDirtyWorktreeError,
    CampaignErrorInfo,
    CampaignLoadError,
    CampaignOperationError,
    CampaignProvenance,
    CampaignTotals,
    GenerationAttemptRecord,
    QualifyingCandidateRecord,
)
from .campaign_store import (
    CampaignLock,
    campaign_lock_path,
    default_campaigns_dir,
    load_campaign,
    relative_artifact_identifier,
    repo_root,
    save_campaign,
)
from .candidate_generator import CandidateGenerator
from .candidate_loader import CandidateArtifactLoadError, CandidateArtifactLoader
from .candidate_orchestrator import CandidateGenerationOrchestrator, default_candidates_dir
from .experiment_models import ExperimentArtifact
from .loader import TaskLoader
from .orchestrator import ExperimentOrchestrator, default_experiments_dir
from .reviewer import Reviewer
from .runner import PytestRunner

PACIFIC = ZoneInfo(DEFAULT_CUTOFF_TIMEZONE)

KNOWN_PROVIDERS = ("mock", "openai")

TERMINAL_STATUSES = frozenset({"completed", "stopped_cutoff", "blocked", "failed"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_protocol_path() -> Path:
    return repo_root() / "docs" / "research_protocol.md"


def parse_cutoff(cutoff: str) -> CampaignCutoff:
    """Parse a cutoff string, interpreting naive values as America/Los_Angeles."""

    try:
        parsed = datetime.fromisoformat(cutoff)
    except ValueError as exc:
        raise ValueError(
            f"Invalid cutoff datetime {cutoff!r}; use ISO-8601, e.g. "
            "2026-09-21T12:00:00 or 2026-09-21T12:00:00-07:00."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PACIFIC)
    local = parsed.astimezone(PACIFIC)
    utc = parsed.astimezone(timezone.utc)
    return CampaignCutoff(
        original=cutoff,
        timezone=DEFAULT_CUTOFF_TIMEZONE,
        local_iso=local.isoformat(),
        utc=utc,
    )


def hash_protocol_file(protocol_path: Path) -> str:
    return hashlib.sha256(protocol_path.read_bytes()).hexdigest()


def inspect_repository(repo: Path) -> tuple[Optional[str], bool]:
    """Return ``(commit_hash_or_none, tracked_files_are_dirty)``.

    Ignored paths (including ``backend/data/**`` campaign/candidate/
    experiment artifacts) are not considered. Untracked files are not
    considered. Only modifications to already-tracked files make the
    worktree dirty for campaign-creation purposes.
    """

    commit: Optional[str]
    try:
        commit_result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        commit = commit_result.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        commit = None

    try:
        status_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        dirty = bool(status_result.stdout.strip())
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        dirty = False

    return commit, dirty


def select_next_task(
    *,
    task_ids: Sequence[str],
    qualifying_counts: dict[str, int],
    target_per_task: int,
    generation_attempt_count: int,
) -> Optional[str]:
    """Round-robin next task that is still under its qualifying quota.

    The only inputs are task ids, per-task qualifying counts, the quota,
    and how many generation attempts have already been recorded.
    """

    if all(qualifying_counts.get(task_id, 0) >= target_per_task for task_id in task_ids):
        return None
    count = len(task_ids)
    start = generation_attempt_count % count
    for offset in range(count):
        task_id = task_ids[(start + offset) % count]
        if qualifying_counts.get(task_id, 0) < target_per_task:
            return task_id
    return None


def _recompute_qualifying_counts(artifact: CampaignArtifact) -> dict[str, int]:
    counts = {task_id: 0 for task_id in artifact.config.task_ids}
    for record in artifact.qualifying_candidates:
        counts[record.task_id] = counts.get(record.task_id, 0) + 1
    return counts


def _recompute_totals(artifact: CampaignArtifact) -> CampaignTotals:
    gen_calls = 0
    gen_in = 0
    gen_out = 0
    gen_lat = 0.0
    has_gen_in = False
    has_gen_out = False
    has_gen_lat = False
    for attempt in artifact.generation_attempts:
        gen_calls += attempt.generator_attempt_count
        if attempt.input_tokens is not None:
            gen_in += attempt.input_tokens
            has_gen_in = True
        if attempt.output_tokens is not None:
            gen_out += attempt.output_tokens
            has_gen_out = True
        if attempt.latency_seconds is not None:
            gen_lat += attempt.latency_seconds
            has_gen_lat = True

    rev_calls = 0
    rev_in = 0
    rev_out = 0
    rev_lat = 0.0
    has_rev_in = False
    has_rev_out = False
    has_rev_lat = False
    has_rev_calls = False
    for record in artifact.qualifying_candidates:
        if record.reviewer_observation_count is not None:
            rev_calls += record.reviewer_observation_count
            has_rev_calls = True
        if record.reviewer_input_tokens is not None:
            rev_in += record.reviewer_input_tokens
            has_rev_in = True
        if record.reviewer_output_tokens is not None:
            rev_out += record.reviewer_output_tokens
            has_rev_out = True
        if record.reviewer_latency_seconds is not None:
            rev_lat += record.reviewer_latency_seconds
            has_rev_lat = True

    return CampaignTotals(
        generation_calls=gen_calls if gen_calls else None,
        generation_input_tokens=gen_in if has_gen_in else None,
        generation_output_tokens=gen_out if has_gen_out else None,
        generation_latency_seconds=gen_lat if has_gen_lat else None,
        reviewer_calls=rev_calls if has_rev_calls else None,
        reviewer_input_tokens=rev_in if has_rev_in else None,
        reviewer_output_tokens=rev_out if has_rev_out else None,
        reviewer_latency_seconds=rev_lat if has_rev_lat else None,
    )


def _usage_from_experiment(experiment: ExperimentArtifact) -> dict[str, Optional[object]]:
    """Token/call/latency accounting from an experiment. Never reads confidence."""

    observation_count = len(experiment.observations)
    input_tokens = [
        obs.result.input_tokens
        for obs in experiment.observations
        if obs.result.input_tokens is not None
    ]
    output_tokens = [
        obs.result.output_tokens
        for obs in experiment.observations
        if obs.result.output_tokens is not None
    ]
    latencies = [
        obs.result.latency_seconds
        for obs in experiment.observations
        if obs.result.latency_seconds is not None
    ]
    return {
        "reviewer_observation_count": observation_count,
        "reviewer_input_tokens": sum(input_tokens) if input_tokens else None,
        "reviewer_output_tokens": sum(output_tokens) if output_tokens else None,
        "reviewer_latency_seconds": sum(latencies) if latencies else None,
    }


def campaign_status_summary(artifact: CampaignArtifact) -> dict[str, object]:
    """Read-only status dict. Never initializes a provider."""

    next_task = select_next_task(
        task_ids=artifact.config.task_ids,
        qualifying_counts=_recompute_qualifying_counts(artifact),
        target_per_task=artifact.config.target_per_task,
        generation_attempt_count=len(artifact.generation_attempts),
    )
    completed_experiments = sum(
        1
        for record in artifact.qualifying_candidates
        if record.experiment_status == "completed"
    )
    return {
        "campaign_id": str(artifact.campaign_id),
        "status": artifact.status,
        "schema_version": artifact.schema_version,
        "qualifying_counts": _recompute_qualifying_counts(artifact),
        "total_generation_attempts": len(artifact.generation_attempts),
        "attrition_count": len(artifact.attrition),
        "completed_experiments": completed_experiments,
        "next_generation_seed": artifact.next_generation_seed,
        "next_reviewer_seed": artifact.next_reviewer_seed,
        "next_scheduled_task": next_task,
        "cutoff": artifact.config.cutoff.model_dump(mode="json"),
        "totals": artifact.totals.model_dump(mode="json"),
        "error": artifact.error.model_dump(mode="json") if artifact.error else None,
        "artifact_path": artifact.artifact_path,
        "started_at": artifact.started_at.isoformat() if artifact.started_at else None,
        "updated_at": artifact.updated_at.isoformat(),
        "completed_at": artifact.completed_at.isoformat() if artifact.completed_at else None,
    }


def create_campaign(
    *,
    generator_provider: str,
    generator_model: str,
    reviewer_provider: str,
    reviewer_model: str,
    target_per_task: int = DEFAULT_TARGET_PER_TASK,
    max_candidate_attempts: int = DEFAULT_MAX_CANDIDATE_ATTEMPTS,
    generation_seed_start: int = DEFAULT_GENERATION_SEED_START,
    reviewer_seed_start: int = DEFAULT_REVIEWER_SEED_START,
    repetitions: int = DEFAULT_REPETITIONS,
    cutoff: str,
    max_generation_attempts: int = DEFAULT_MAX_GENERATION_ATTEMPTS,
    task_ids: Sequence[str] = CAMPAIGN_TASK_IDS,
    campaigns_dir: Optional[Path] = None,
    repo: Optional[Path] = None,
    protocol_path: Optional[Path] = None,
    inspect_repo: Optional[Callable[[Path], tuple[Optional[str], bool]]] = None,
) -> CampaignArtifact:
    """Create and persist a campaign. Makes no provider or network calls."""

    if generator_provider not in KNOWN_PROVIDERS:
        raise ValueError(
            f"Unknown generator provider {generator_provider!r}. Available: {sorted(KNOWN_PROVIDERS)}"
        )
    if reviewer_provider not in KNOWN_PROVIDERS:
        raise ValueError(
            f"Unknown reviewer provider {reviewer_provider!r}. Available: {sorted(KNOWN_PROVIDERS)}"
        )

    repo = Path(repo) if repo is not None else repo_root()
    protocol_path = Path(protocol_path) if protocol_path is not None else default_protocol_path()
    if not protocol_path.is_file():
        raise CampaignOperationError("Research protocol file is missing; refusing to create a campaign.")

    inspector = inspect_repo if inspect_repo is not None else inspect_repository
    commit, dirty = inspector(repo)
    if dirty:
        raise CampaignDirtyWorktreeError(
            "Tracked files are dirty; refusing to create a campaign. "
            "Commit or revert tracked changes first. Git-ignored campaign, "
            "candidate, and experiment artifacts are not considered."
        )

    protocol_hash = hash_protocol_file(protocol_path)
    protocol_identifier = relative_artifact_identifier(
        protocol_path, fallback="docs/research_protocol.md"
    )
    now = _utcnow()
    parsed_cutoff = parse_cutoff(cutoff)
    config = CampaignConfig(
        generator_provider=generator_provider,
        generator_model=generator_model,
        reviewer_provider=reviewer_provider,
        reviewer_model=reviewer_model,
        task_ids=tuple(task_ids),
        target_per_task=target_per_task,
        max_candidate_attempts=max_candidate_attempts,
        generation_seed_start=generation_seed_start,
        reviewer_seed_start=reviewer_seed_start,
        repetitions=repetitions,
        cutoff=parsed_cutoff,
        max_generation_attempts=max_generation_attempts,
    )
    campaign_id = uuid.uuid4()
    artifact = CampaignArtifact(
        schema_version=CAMPAIGN_SCHEMA_VERSION,
        campaign_id=campaign_id,
        status="created",
        config=config,
        provenance=CampaignProvenance(
            research_protocol_sha256=protocol_hash,
            repository_commit=commit,
            repository_dirty=False,
            protocol_path=protocol_identifier,
        ),
        created_at=now,
        updated_at=now,
        next_generation_seed=generation_seed_start,
        next_reviewer_seed=reviewer_seed_start,
        qualifying_counts={task_id: 0 for task_id in config.task_ids},
    )
    campaigns_dir = Path(campaigns_dir) if campaigns_dir is not None else default_campaigns_dir()
    save_campaign(artifact, campaigns_dir)
    return load_campaign(campaign_id, campaigns_dir)


class CampaignOrchestrator:
    """Runs or resumes one campaign sequentially with durable checkpoints."""

    def __init__(
        self,
        *,
        generator_factory: Callable[[], CandidateGenerator],
        reviewer_factory: Callable[[], Reviewer],
        tasks_root: Optional[Path] = None,
        candidates_dir: Optional[Path] = None,
        experiments_dir: Optional[Path] = None,
        campaigns_dir: Optional[Path] = None,
        runner: Optional[PytestRunner] = None,
        now_fn: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._generator_factory = generator_factory
        self._reviewer_factory = reviewer_factory
        self._tasks_root = tasks_root
        self._candidates_dir = (
            Path(candidates_dir) if candidates_dir is not None else default_candidates_dir()
        )
        self._experiments_dir = (
            Path(experiments_dir) if experiments_dir is not None else default_experiments_dir()
        )
        self._campaigns_dir = (
            Path(campaigns_dir) if campaigns_dir is not None else default_campaigns_dir()
        )
        shared_runner = runner or PytestRunner()
        self._candidate_orchestrator = CandidateGenerationOrchestrator(
            generator_factory=generator_factory,
            tasks_root=tasks_root,
            output_dir=self._candidates_dir,
            runner=shared_runner,
        )
        self._experiment_orchestrator = ExperimentOrchestrator(
            reviewer_factory=reviewer_factory,
            tasks_root=tasks_root,
            output_dir=self._experiments_dir,
            runner=shared_runner,
        )
        self._candidate_loader = CandidateArtifactLoader(candidates_root=self._candidates_dir)
        self._now = now_fn

    def run(self, campaign_id: Union[str, uuid.UUID]) -> CampaignArtifact:
        artifact = load_campaign(campaign_id, self._campaigns_dir)
        lock = CampaignLock(campaign_lock_path(artifact.campaign_id, self._campaigns_dir))
        lock.acquire()
        try:
            artifact = load_campaign(artifact.campaign_id, self._campaigns_dir)
            if artifact.status in TERMINAL_STATUSES:
                return artifact
            return self._run_locked(artifact)
        finally:
            lock.release()

    def _run_locked(self, artifact: CampaignArtifact) -> CampaignArtifact:
        now = self._now()
        updates: dict[str, object] = {"status": "running", "updated_at": now}
        if artifact.started_at is None:
            updates["started_at"] = now
        artifact = artifact.model_copy(update=updates)
        artifact = self._checkpoint(artifact)

        try:
            while True:
                artifact = self._validate_resume_invariants(artifact)

                pending = self._pending_reviewer_work(artifact)
                if pending is not None:
                    artifact = self._finish_pending_reviewer_work(artifact, pending)
                    if artifact.status in TERMINAL_STATUSES:
                        return artifact
                    continue

                interrupted = self._interrupted_generation(artifact)
                if interrupted is not None:
                    return self._block(
                        artifact,
                        category="interrupted_generation",
                        message=(
                            "Generation attempt "
                            f"{interrupted.attempt_index} (seed {interrupted.generation_seed}) "
                            "was interrupted before completion; refusing to regenerate or "
                            "reassign its seed. Manual inspection is required."
                        ),
                        generation_attempt_index=interrupted.attempt_index,
                        candidate_id=interrupted.candidate_id,
                    )

                counts = _recompute_qualifying_counts(artifact)
                artifact = artifact.model_copy(update={"qualifying_counts": counts})
                if all(
                    counts.get(task_id, 0) >= artifact.config.target_per_task
                    for task_id in artifact.config.task_ids
                ):
                    return self._finalize(artifact, "completed")

                if self._now() >= artifact.config.cutoff.utc:
                    return self._finalize(artifact, "stopped_cutoff")

                if len(artifact.generation_attempts) >= artifact.config.max_generation_attempts:
                    return self._block(
                        artifact,
                        category="max_generation_attempts",
                        message=(
                            "Reached max_generation_attempts="
                            f"{artifact.config.max_generation_attempts} before both task "
                            "quotas were filled. The target was not changed."
                        ),
                    )

                task_id = select_next_task(
                    task_ids=artifact.config.task_ids,
                    qualifying_counts=counts,
                    target_per_task=artifact.config.target_per_task,
                    generation_attempt_count=len(artifact.generation_attempts),
                )
                if task_id is None:
                    return self._finalize(artifact, "completed")

                artifact = self._start_generation_attempt(artifact, task_id)
                artifact = self._complete_generation_attempt(artifact)
                if artifact.status in TERMINAL_STATUSES:
                    return artifact
        except CampaignLoadError as exc:
            return self._finalize(
                artifact,
                "failed",
                error=CampaignErrorInfo(category=type(exc).__name__, message=str(exc)),
            )

    def _start_generation_attempt(self, artifact: CampaignArtifact, task_id: str) -> CampaignArtifact:
        seed = artifact.next_generation_seed
        attempt = GenerationAttemptRecord(
            attempt_index=len(artifact.generation_attempts),
            task_id=task_id,
            generation_seed=seed,
            status="in_progress",
            started_at=self._now(),
        )
        attempts = list(artifact.generation_attempts)
        attempts.append(attempt)
        artifact = artifact.model_copy(
            update={
                "generation_attempts": attempts,
                "next_generation_seed": seed + 1,
                "updated_at": self._now(),
            }
        )
        return self._checkpoint(artifact)

    def _complete_generation_attempt(self, artifact: CampaignArtifact) -> CampaignArtifact:
        attempt = artifact.generation_attempts[-1]
        try:
            metadata = self._candidate_orchestrator.run(
                task_id=attempt.task_id,
                max_attempts=artifact.config.max_candidate_attempts,
                random_seed=attempt.generation_seed,
            )
        except Exception as exc:  # noqa: BLE001
            return self._record_generation_failure(artifact, error=str(exc), block=True)

        artifact_dir = self._candidate_orchestrator.artifact_dir(
            attempt.task_id, metadata.candidate_id
        )
        relative_dir = relative_artifact_identifier(
            artifact_dir, fallback=f"{attempt.task_id}/{metadata.candidate_id}"
        )
        qualified = (
            metadata.status == "completed"
            and metadata.stop_reason == "visible_tests_passed"
            and metadata.visible_tests_passed
        )
        updated_attempt = attempt.model_copy(
            update={
                "status": "qualified" if qualified else "attrition",
                "candidate_id": metadata.candidate_id,
                "source_sha256": metadata.final_source_sha256,
                "generation_artifact_path": relative_dir,
                "stop_reason": metadata.stop_reason,
                "visible_tests_passed": metadata.visible_tests_passed,
                "generator_attempt_count": metadata.attempt_count,
                "input_tokens": metadata.total_input_tokens,
                "output_tokens": metadata.total_output_tokens,
                "latency_seconds": metadata.total_latency_seconds,
                "completed_at": metadata.completed_at or self._now(),
                "error": metadata.error,
            }
        )
        attempts = list(artifact.generation_attempts)
        attempts[-1] = updated_attempt
        updates: dict[str, object] = {
            "generation_attempts": attempts,
            "updated_at": self._now(),
        }
        if not qualified:
            attrition = list(artifact.attrition)
            attrition.append(
                AttritionRecord(
                    generation_attempt_index=updated_attempt.attempt_index,
                    task_id=updated_attempt.task_id,
                    generation_seed=updated_attempt.generation_seed,
                    candidate_id=updated_attempt.candidate_id,
                    stop_reason=updated_attempt.stop_reason,
                    generator_attempt_count=updated_attempt.generator_attempt_count,
                    reason=(
                        "Generation did not pass visible tests "
                        f"(stop_reason={updated_attempt.stop_reason!r}); "
                        "no reviewer calls will be made."
                    ),
                )
            )
            updates["attrition"] = attrition
        artifact = artifact.model_copy(update=updates)
        artifact = artifact.model_copy(update={"totals": _recompute_totals(artifact)})
        return self._checkpoint(artifact)

    def _record_generation_failure(
        self, artifact: CampaignArtifact, *, error: str, block: bool
    ) -> CampaignArtifact:
        attempt = artifact.generation_attempts[-1]
        updated_attempt = attempt.model_copy(
            update={
                "status": "attrition",
                "completed_at": self._now(),
                "error": error,
                "visible_tests_passed": False,
            }
        )
        attempts = list(artifact.generation_attempts)
        attempts[-1] = updated_attempt
        attrition = list(artifact.attrition)
        attrition.append(
            AttritionRecord(
                generation_attempt_index=updated_attempt.attempt_index,
                task_id=updated_attempt.task_id,
                generation_seed=updated_attempt.generation_seed,
                candidate_id=updated_attempt.candidate_id,
                stop_reason=None,
                generator_attempt_count=updated_attempt.generator_attempt_count,
                reason=f"Generation failed before producing a qualifying candidate: {error}",
            )
        )
        artifact = artifact.model_copy(
            update={
                "generation_attempts": attempts,
                "attrition": attrition,
                "updated_at": self._now(),
            }
        )
        artifact = artifact.model_copy(update={"totals": _recompute_totals(artifact)})
        artifact = self._checkpoint(artifact)
        if block:
            return self._block(
                artifact,
                category="generation_error",
                message=error,
                generation_attempt_index=updated_attempt.attempt_index,
                candidate_id=updated_attempt.candidate_id,
            )
        return artifact

    def _pending_reviewer_work(
        self, artifact: CampaignArtifact
    ) -> Optional[tuple[GenerationAttemptRecord, Optional[QualifyingCandidateRecord]]]:
        recorded_ids = {record.candidate_id for record in artifact.qualifying_candidates}
        for attempt in artifact.generation_attempts:
            if attempt.status != "qualified" or attempt.candidate_id is None:
                continue
            if attempt.candidate_id not in recorded_ids:
                return attempt, None
        for record in artifact.qualifying_candidates:
            if record.experiment_status != "completed":
                return None, record
        return None

    def _interrupted_generation(
        self, artifact: CampaignArtifact
    ) -> Optional[GenerationAttemptRecord]:
        if not artifact.generation_attempts:
            return None
        last = artifact.generation_attempts[-1]
        if last.status == "in_progress":
            return last
        return None

    def _finish_pending_reviewer_work(
        self,
        artifact: CampaignArtifact,
        pending: tuple[GenerationAttemptRecord, Optional[QualifyingCandidateRecord]],
    ) -> CampaignArtifact:
        attempt, record = pending
        if record is None:
            if attempt.candidate_id is None or attempt.source_sha256 is None:
                return self._block(
                    artifact,
                    category="missing_candidate",
                    message=(
                        "A generation attempt is marked qualified but has no candidate "
                        "id/source hash; refusing to invent a replacement."
                    ),
                    generation_attempt_index=attempt.attempt_index,
                )
            if attempt.generation_artifact_path is None:
                return self._block(
                    artifact,
                    category="missing_candidate",
                    message="Qualifying generation attempt is missing its artifact identifier.",
                    generation_attempt_index=attempt.attempt_index,
                    candidate_id=attempt.candidate_id,
                )
            reviewer_seed = artifact.next_reviewer_seed
            record = QualifyingCandidateRecord(
                qualification_index=len(artifact.qualifying_candidates),
                task_id=attempt.task_id,
                candidate_id=attempt.candidate_id,
                source_sha256=attempt.source_sha256,
                generation_seed=attempt.generation_seed,
                generation_attempt_index=attempt.attempt_index,
                generation_artifact_path=attempt.generation_artifact_path,
                reviewer_seed=reviewer_seed,
            )
            qualifying = list(artifact.qualifying_candidates)
            qualifying.append(record)
            counts = _recompute_qualifying_counts(
                artifact.model_copy(update={"qualifying_candidates": qualifying})
            )
            artifact = artifact.model_copy(
                update={
                    "qualifying_candidates": qualifying,
                    "next_reviewer_seed": reviewer_seed + 1,
                    "qualifying_counts": counts,
                    "updated_at": self._now(),
                }
            )
            artifact = self._checkpoint(artifact)
            record = artifact.qualifying_candidates[-1]

        if record.experiment_status == "completed":
            return artifact

        if record.experiment_started and record.experiment_id is not None:
            return self._adopt_or_block_existing_experiment(artifact, record)

        if record.experiment_started and record.experiment_id is None:
            return self._block(
                artifact,
                category="interrupted_experiment",
                message=(
                    f"Reviewer experiment for candidate {record.candidate_id} was started "
                    "but no experiment id was recorded; refusing to rerun. "
                    "Manual inspection is required."
                ),
                candidate_id=record.candidate_id,
            )

        return self._run_reviewer_experiment(artifact, record)

    def _adopt_or_block_existing_experiment(
        self, artifact: CampaignArtifact, record: QualifyingCandidateRecord
    ) -> CampaignArtifact:
        assert record.experiment_id is not None
        path = self._experiments_dir / f"{record.experiment_id}.json"
        if not path.is_file():
            return self._block(
                artifact,
                category="interrupted_experiment",
                message=(
                    f"Reviewer experiment {record.experiment_id} was assigned but the "
                    "artifact is missing; refusing to rerun. Manual inspection is required."
                ),
                candidate_id=record.candidate_id,
                experiment_id=record.experiment_id,
            )
        try:
            experiment = ExperimentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return self._block(
                artifact,
                category="interrupted_experiment",
                message=f"Could not load existing experiment artifact: {exc}",
                candidate_id=record.candidate_id,
                experiment_id=record.experiment_id,
            )
        if experiment.metadata.status != "completed":
            return self._block_partial_experiment(artifact, record, experiment)
        return self._record_experiment_completion(artifact, record, experiment)

    def _run_reviewer_experiment(
        self, artifact: CampaignArtifact, record: QualifyingCandidateRecord
    ) -> CampaignArtifact:
        index = self._qualifying_index(artifact, record.candidate_id)
        started = artifact.qualifying_candidates[index].model_copy(
            update={"experiment_started": True}
        )
        qualifying = list(artifact.qualifying_candidates)
        qualifying[index] = started
        artifact = artifact.model_copy(
            update={"qualifying_candidates": qualifying, "updated_at": self._now()}
        )
        artifact = self._checkpoint(artifact)

        try:
            task = TaskLoader(tasks_root=self._tasks_root).load(record.task_id)
            loaded = self._candidate_loader.load(task, record.candidate_id)
            experiment = self._experiment_orchestrator.run(
                task_id=record.task_id,
                repetitions=artifact.config.repetitions,
                random_seed=record.reviewer_seed,
                candidate=loaded,
            )
        except (CandidateArtifactLoadError, Exception) as exc:
            return self._block(
                artifact,
                category="experiment_error",
                message=str(exc),
                candidate_id=record.candidate_id,
            )

        if experiment.metadata.status != "completed":
            artifact = self._record_experiment_completion(artifact, started, experiment)
            return self._block_partial_experiment(artifact, started, experiment)

        return self._record_experiment_completion(artifact, started, experiment)

    def _record_experiment_completion(
        self,
        artifact: CampaignArtifact,
        record: QualifyingCandidateRecord,
        experiment: ExperimentArtifact,
    ) -> CampaignArtifact:
        relative = relative_artifact_identifier(
            self._experiment_orchestrator.artifact_path(experiment.metadata.experiment_id),
            fallback=f"experiments/{experiment.metadata.experiment_id}.json",
        )
        usage = _usage_from_experiment(experiment)
        index = self._qualifying_index(artifact, record.candidate_id)
        updated = artifact.qualifying_candidates[index].model_copy(
            update={
                "experiment_started": True,
                "experiment_id": experiment.metadata.experiment_id,
                "experiment_status": experiment.metadata.status,
                "experiment_artifact_path": relative,
                **usage,
            }
        )
        qualifying = list(artifact.qualifying_candidates)
        qualifying[index] = updated
        artifact = artifact.model_copy(
            update={"qualifying_candidates": qualifying, "updated_at": self._now()}
        )
        artifact = artifact.model_copy(update={"totals": _recompute_totals(artifact)})
        return self._checkpoint(artifact)

    def _block_partial_experiment(
        self,
        artifact: CampaignArtifact,
        record: QualifyingCandidateRecord,
        experiment: ExperimentArtifact,
    ) -> CampaignArtifact:
        return self._block(
            artifact,
            category="partial_experiment",
            message=(
                f"Reviewer experiment {experiment.metadata.experiment_id} for candidate "
                f"{record.candidate_id} ended with status {experiment.metadata.status!r}; "
                "refusing to rerun or continue to later candidates. "
                "Manual inspection is required."
            ),
            candidate_id=record.candidate_id,
            experiment_id=experiment.metadata.experiment_id,
        )

    def _qualifying_index(self, artifact: CampaignArtifact, candidate_id: uuid.UUID) -> int:
        for index, record in enumerate(artifact.qualifying_candidates):
            if record.candidate_id == candidate_id:
                return index
        raise CampaignLoadError(f"Qualifying candidate {candidate_id} is missing from campaign state.")

    def _validate_resume_invariants(self, artifact: CampaignArtifact) -> CampaignArtifact:
        expected_next_generation = (
            artifact.config.generation_seed_start + len(artifact.generation_attempts)
        )
        if artifact.next_generation_seed != expected_next_generation:
            raise CampaignLoadError(
                "Campaign next_generation_seed does not match recorded generation attempts; "
                "refusing to invent or reuse a seed."
            )
        expected_next_reviewer = (
            artifact.config.reviewer_seed_start + len(artifact.qualifying_candidates)
        )
        if artifact.next_reviewer_seed != expected_next_reviewer:
            raise CampaignLoadError(
                "Campaign next_reviewer_seed does not match recorded qualifying candidates; "
                "refusing to invent or reuse a seed."
            )
        counts = _recompute_qualifying_counts(artifact)
        return artifact.model_copy(update={"qualifying_counts": counts, "totals": _recompute_totals(artifact)})

    def _checkpoint(self, artifact: CampaignArtifact) -> CampaignArtifact:
        save_campaign(artifact, self._campaigns_dir)
        return load_campaign(artifact.campaign_id, self._campaigns_dir)

    def _finalize(
        self,
        artifact: CampaignArtifact,
        status: str,
        error: Optional[CampaignErrorInfo] = None,
    ) -> CampaignArtifact:
        now = self._now()
        artifact = artifact.model_copy(
            update={
                "status": status,
                "updated_at": now,
                "completed_at": now,
                "qualifying_counts": _recompute_qualifying_counts(artifact),
                "totals": _recompute_totals(artifact),
                "error": error if error is not None else artifact.error,
            }
        )
        return self._checkpoint(artifact)

    def _block(
        self,
        artifact: CampaignArtifact,
        *,
        category: str,
        message: str,
        generation_attempt_index: Optional[int] = None,
        candidate_id: Optional[uuid.UUID] = None,
        experiment_id: Optional[uuid.UUID] = None,
    ) -> CampaignArtifact:
        return self._finalize(
            artifact,
            "blocked",
            error=CampaignErrorInfo(
                category=category,
                message=message,
                generation_attempt_index=generation_attempt_index,
                candidate_id=candidate_id,
                experiment_id=experiment_id,
            ),
        )
