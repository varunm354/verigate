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

from .conditions import Condition
from .context import build_reviewer_context
from .experiment_models import (
    ExperimentArtifact,
    ExperimentError,
    ExperimentMetadata,
    GroundTruth,
    ReviewerObservation,
)
from .loader import TaskLoader
from .models import ReviewerResult
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

    def run(self, *, task_id: str, repetitions: int, random_seed: int) -> ExperimentArtifact:
        if repetitions < 1:
            raise ValueError("repetitions must be a positive integer")

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

        metadata = ExperimentMetadata(
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
        artifact = ExperimentArtifact(metadata=metadata)
        # Artifact exists on disk before any reviewer calls begin.
        self._save_checkpoint(artifact, path)

        # B. Run the visible tests.
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
        # from this exact same snapshot, so they can never disagree.
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

        # G. Only after every reviewer request has completed, run hidden tests.
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
