"""Reproducible experiment orchestrator with durable local result storage.

Runs every reviewer condition (A/B/C) some number of times against one
frozen candidate, in a randomized-but-reproducible per-repetition order,
saving an atomic checkpoint after each completed observation so that
already-paid-for API calls are never lost. Hidden-test ground truth is
only ever obtained *after* every reviewer call has completed -- this is
the critical scientific rule this module exists to enforce mechanically
(see :meth:`ExperimentOrchestrator.run` and the ordering tests in
``tests/test_orchestrator.py``).

Exact step order (A-H), matching the milestone spec:

    A. Load and validate the task.
    B. Run the visible tests.
    C. If visible tests do not fully pass: stop before any reviewer
       calls, do not run hidden tests, save a ``failed`` artifact.
    D. Freeze and hash the candidate, specification, and visible tests.
    E. Build A/B/C prompts from that frozen content.
    F. For each repetition, run a seeded-random ordering of A/B/C as
       independent reviewer requests (fresh reviewer instance per call;
       no shared response IDs/conversation state), checkpointing after
       each.
    G. Only after every reviewer request has completed, run hidden tests.
    H. Save the completed experiment artifact.

Candidate-aware mode (Milestone 8): passing a validated
:class:`experiment.candidate_loader.LoadedCandidate` as ``candidate=`` to
:meth:`ExperimentOrchestrator.run` changes exactly three of the steps
above, and nothing else:

    - Step B runs the visible tests against the *supplied candidate
      source*, in a fresh isolated workspace, instead of against the
      task's tracked reference implementation.
    - Step D freezes/hashes that same supplied candidate source (never
      the tracked reference).
    - Step G runs hidden tests against that same supplied candidate
      source, in a fresh isolated workspace containing hidden tests only
      (never mixed with visible tests, and never overwriting the tracked
      task file).

The tracked reference implementation
(``LoadedTask.candidate_path``, e.g. ``backend/tasks/json_parser/json_parser.py``)
is never read, written, or otherwise touched when ``candidate`` is
supplied. Omitting ``candidate`` (the default, ``None``) reproduces the
exact pre-existing tracked-candidate behavior above, unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .candidate_loader import LoadedCandidate
from .candidate_workspace import run_candidate_against_suite
from .conditions import Condition
from .context import ReviewerContext, build_reviewer_context
from .experiment_models import (
    ExperimentArtifact,
    ExperimentError,
    ExperimentMetadata,
    GroundTruth,
    ReviewerObservation,
)
from .loader import LoadedTask, TaskLoader
from .models import ReviewerResult, TestSuiteResult
from .prompts import PROMPT_VERSION, build_all_prompts_from_context
from .reviewer import ProvidesResponseMetadata, Reviewer
from .runner import PytestRunner


def default_experiments_dir() -> Path:
    """``backend/data/experiments``, resolved relative to this file.

    Correct regardless of the current working directory the CLI/tests are
    invoked from.
    """

    return Path(__file__).resolve().parent.parent / "data" / "experiments"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_visible_tests(visible_tests_source: dict[str, str]) -> str:
    """A single stable hash over every visible-test file, sorted by filename."""

    hasher = hashlib.sha256()
    for name in sorted(visible_tests_source):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(visible_tests_source[name].encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _read_suite_source(tests_dir: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(tests_dir.glob("*.py"))
    }


def build_reviewer_context_for_candidate(
    task: LoadedTask, candidate: LoadedCandidate
) -> ReviewerContext:
    """Assemble a :class:`ReviewerContext` from the *supplied* candidate source.

    Identical to :func:`experiment.context.build_reviewer_context` except
    that ``candidate_source`` comes from the already-verified
    :class:`~experiment.candidate_loader.LoadedCandidate` rather than from
    ``task.candidate_path`` (the tracked reference implementation). The
    specification and visible-test source are still read from the task,
    unchanged; hidden tests are never read here.
    """

    return ReviewerContext(
        task_id=task.manifest.task_id,
        specification=task.specification_path.read_text(encoding="utf-8"),
        candidate_source=candidate.source,
        visible_tests_source=_read_suite_source(task.visible_tests_path),
    )


def _relative_generation_artifact_path(
    artifact_dir: Path, *, task_id: str, candidate_id: uuid.UUID
) -> str:
    """A safe, project-relative string identifying a candidate artifact directory.

    Never an absolute filesystem path (see
    ``ExperimentMetadata.generation_artifact_path``'s validator). Falls
    back to a stable ``<task_id>/<candidate_id>`` identifier if
    ``artifact_dir`` is not under this repository (e.g. a custom
    candidates root supplied for testing).
    """

    repo_root = Path(__file__).resolve().parents[2]
    try:
        return str(artifact_dir.resolve().relative_to(repo_root))
    except ValueError:
        return f"{task_id}/{candidate_id}"


def _run_visible_for_candidate(
    task: LoadedTask, candidate: LoadedCandidate, runner: PytestRunner
) -> TestSuiteResult:
    return run_candidate_against_suite(
        candidate_source=candidate.source,
        required_module_filename=candidate.required_module_filename,
        tests_source=_read_suite_source(task.visible_tests_path),
        suite="visible",
        timeout_seconds=task.manifest.timeout_seconds,
        runner=runner,
    )


def _run_hidden_for_candidate(
    task: LoadedTask, candidate: LoadedCandidate, runner: PytestRunner
) -> TestSuiteResult:
    return run_candidate_against_suite(
        candidate_source=candidate.source,
        required_module_filename=candidate.required_module_filename,
        tests_source=_read_suite_source(task.hidden_tests_path),
        suite="hidden",
        timeout_seconds=task.manifest.timeout_seconds,
        runner=runner,
    )


def _safe_error_from_exception(
    exc: Exception, *, condition: Optional[Condition], repetition_index: Optional[int]
) -> ExperimentError:
    """Build safe, secret-free error info from a failed reviewer call.

    Reviewer implementations (``MockReviewer``, ``OpenAIReviewer``) are
    already required to never put secrets into their exception messages
    (see ``experiment.openai_reviewer``'s exception hierarchy), so
    ``str(exc)`` is safe to store/print here.
    """

    return ExperimentError(
        category=type(exc).__name__,
        message=str(exc),
        condition=condition,
        repetition_index=repetition_index,
    )


class ExperimentOrchestrator:
    """Runs a full A/B/C x repetitions experiment and persists the result.

    ``reviewer_factory`` must be a zero-argument callable that returns a
    *fresh* :class:`Reviewer` instance every time it is called. The
    orchestrator calls it once per individual reviewer request (never
    reusing an instance across calls), which is what guarantees no two
    calls can ever share response IDs, conversation state, or previous
    responses -- there is no instance around long enough to carry any
    such state forward.
    """

    def __init__(
        self,
        *,
        reviewer_factory: Callable[[], Reviewer],
        tasks_root: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        runner: Optional[PytestRunner] = None,
    ) -> None:
        self._reviewer_factory = reviewer_factory
        self._tasks_root = tasks_root
        self._output_dir = Path(output_dir) if output_dir is not None else default_experiments_dir()
        self._runner = runner or PytestRunner()

    def artifact_path(self, experiment_id: uuid.UUID) -> Path:
        return self._output_dir / f"{experiment_id}.json"

    def run(
        self,
        *,
        task_id: str,
        repetitions: int,
        random_seed: int,
        candidate: Optional[LoadedCandidate] = None,
    ) -> ExperimentArtifact:
        if repetitions < 1:
            raise ValueError("repetitions must be a positive integer")
        if candidate is not None and candidate.task_id != task_id:
            raise ValueError(
                f"Candidate {candidate.candidate_id} was loaded for task "
                f"{candidate.task_id!r}, not {task_id!r}."
            )

        experiment_id = uuid.uuid4()
        path = self.artifact_path(experiment_id)
        started_at = _utcnow()

        # A. Load and validate the task.
        task = TaskLoader(tasks_root=self._tasks_root).load(task_id)

        # Identify provider/model up front (no API call is made just by
        # constructing a reviewer instance) so the very first artifact we
        # save -- before any reviewer request is made -- already records
        # what this experiment is running.
        probe_reviewer = self._reviewer_factory()

        metadata_kwargs: dict[str, object] = dict(
            experiment_id=experiment_id,
            task_id=task.manifest.task_id,
            provider=probe_reviewer.provider,
            model=probe_reviewer.model,
            prompt_version=PROMPT_VERSION,
            repetitions=repetitions,
            random_seed=random_seed,
            started_at=started_at,
            status="partial",
        )
        if candidate is not None:
            # Candidate provenance is recorded immediately, before any
            # reviewer call, and is guaranteed to agree with the
            # generation artifact: every value here comes directly from
            # the already-verified `CandidateArtifactMetadata` (see
            # `experiment.candidate_loader.CandidateArtifactLoader`), not
            # from anything re-derived or re-typed by hand.
            metadata_kwargs.update(
                candidate_id=candidate.candidate_id,
                candidate_source_sha256=candidate.metadata.final_source_sha256,
                generator_provider=candidate.metadata.provider,
                generator_model=candidate.metadata.model,
                generation_prompt_version=candidate.metadata.prompt_version,
                generation_attempt_count=candidate.metadata.attempt_count,
                generation_artifact_path=_relative_generation_artifact_path(
                    candidate.artifact_dir,
                    task_id=candidate.task_id,
                    candidate_id=candidate.candidate_id,
                ),
            )
        metadata = ExperimentMetadata(**metadata_kwargs)
        artifact = ExperimentArtifact(metadata=metadata)
        # Artifact exists on disk before any reviewer calls begin.
        self._save_checkpoint(artifact, path)

        # B. Run the visible tests -- against the supplied candidate source
        # (in a fresh isolated workspace) if one was given, otherwise
        # against the tracked reference implementation exactly as before.
        if candidate is not None:
            visible_result = _run_visible_for_candidate(task, candidate, self._runner)
        else:
            visible_result = self._runner.run_visible(task)
        ground_truth = GroundTruth(
            visible_passed=visible_result.passed,
            visible_passed_count=visible_result.passed_count,
            visible_failed_count=visible_result.failed_count,
        )
        artifact = artifact.model_copy(update={"ground_truth": ground_truth})
        self._save_checkpoint(artifact, path)

        # C. Stop before any reviewer calls if visible tests didn't fully pass.
        if not visible_result.passed:
            error = ExperimentError(
                category="visible_tests_failed",
                message=(
                    "Visible tests did not fully pass "
                    f"({visible_result.passed_count} passed, "
                    f"{visible_result.failed_count} failed); stopping before any "
                    "reviewer calls. Hidden tests were not run."
                ),
            )
            metadata = metadata.model_copy(update={"status": "failed", "completed_at": _utcnow()})
            artifact = artifact.model_copy(update={"metadata": metadata, "error": error})
            self._save_checkpoint(artifact, path)
            return artifact

        # D. Freeze and hash the candidate, specification, and visible tests.
        # Read once; every hash and every condition's prompt below is built
        # from this exact same snapshot, so they can never disagree. When a
        # candidate was supplied, this snapshot's `candidate_source` is
        # that exact supplied source -- never the tracked reference.
        if candidate is not None:
            context = build_reviewer_context_for_candidate(task, candidate)
        else:
            context = build_reviewer_context(task)
        metadata = metadata.model_copy(
            update={
                "candidate_sha256": _sha256_text(context.candidate_source),
                "specification_sha256": _sha256_text(context.specification),
                "visible_tests_sha256": _sha256_visible_tests(context.visible_tests_source),
            }
        )
        artifact = artifact.model_copy(update={"metadata": metadata})
        self._save_checkpoint(artifact, path)

        # E. Build A/B/C prompts from that frozen content.
        prompts = build_all_prompts_from_context(context, task.manifest.task_id)

        # F. For each repetition, run a seeded-random ordering of A/B/C as
        # independent reviewer requests.
        rng = random.Random(random_seed)
        observations: list[ReviewerObservation] = []
        execution_order_index = 0
        error: Optional[ExperimentError] = None

        for repetition_index in range(repetitions):
            condition_order = list(Condition)
            rng.shuffle(condition_order)

            for condition in condition_order:
                # A brand-new reviewer instance per call: no shared response
                # IDs, conversation state, or previous responses possible.
                reviewer = self._reviewer_factory()
                prompt = prompts[condition]

                start = time.perf_counter()
                try:
                    assessment = reviewer.review(condition, prompt)
                except Exception as exc:  # noqa: BLE001 -- convert to a safe, recorded error
                    error = _safe_error_from_exception(
                        exc, condition=condition, repetition_index=repetition_index
                    )
                    break
                latency_seconds = time.perf_counter() - start

                result_kwargs: dict[str, object] = dict(
                    task_id=task.manifest.task_id,
                    condition=condition,
                    assessment=assessment,
                    prompt_version=PROMPT_VERSION,
                    provider=reviewer.provider,
                    model=reviewer.model,
                    timestamp=_utcnow(),
                    latency_seconds=latency_seconds,
                )
                if isinstance(reviewer, ProvidesResponseMetadata):
                    result_kwargs["response_id"] = reviewer.last_response_id
                    result_kwargs["input_tokens"] = reviewer.last_input_tokens
                    result_kwargs["output_tokens"] = reviewer.last_output_tokens

                observation = ReviewerObservation(
                    observation_id=uuid.uuid4(),
                    repetition_index=repetition_index,
                    execution_order_index=execution_order_index,
                    condition=condition,
                    result=ReviewerResult(**result_kwargs),
                )
                execution_order_index += 1
                observations.append(observation)

                # Atomic checkpoint after every successful observation --
                # completed (and already possibly billed) work is never lost.
                artifact = artifact.model_copy(update={"observations": list(observations)})
                self._save_checkpoint(artifact, path)

            if error is not None:
                break

        if error is not None:
            metadata = metadata.model_copy(update={"status": "partial", "completed_at": _utcnow()})
            artifact = artifact.model_copy(update={"metadata": metadata, "error": error})
            self._save_checkpoint(artifact, path)
            return artifact

        # G. Only after every reviewer request has completed, run hidden
        # tests -- against the same supplied candidate source (in a fresh
        # isolated workspace containing hidden tests only) if one was
        # given, otherwise against the tracked reference implementation
        # exactly as before.
        if candidate is not None:
            hidden_result = _run_hidden_for_candidate(task, candidate, self._runner)
        else:
            hidden_result = self._runner.run_hidden(task)
        ground_truth = ground_truth.model_copy(
            update={
                "hidden_passed": hidden_result.passed,
                "hidden_passed_count": hidden_result.passed_count,
                "hidden_failed_count": hidden_result.failed_count,
            }
        )

        # H. Save the completed experiment artifact.
        metadata = metadata.model_copy(update={"status": "completed", "completed_at": _utcnow()})
        artifact = artifact.model_copy(update={"metadata": metadata, "ground_truth": ground_truth})
        self._save_checkpoint(artifact, path)
        return artifact

    def _save_checkpoint(self, artifact: ExperimentArtifact, path: Path) -> None:
        """Write ``artifact`` to ``path`` via write-temp-file-then-atomic-rename.

        Never leaves a partially written file at ``path`` even if the
        process is killed mid-write: readers only ever see either the
        previous complete checkpoint or the new complete one.
        """

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(artifact.model_dump(mode="json"), indent=2)

        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
