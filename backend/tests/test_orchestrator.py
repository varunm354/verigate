"""Tests for experiment.orchestrator.ExperimentOrchestrator.

Uses fake reviewers and a fake pytest runner (never the real subprocess
runner, and never a real OpenAI client), plus ``tmp_path`` for all saved
artifacts. No test here makes a network call.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pytest

from experiment.conditions import Condition
from experiment.experiment_models import ExperimentArtifact
from experiment.models import ReviewerAssessment
from experiment.models import TestSuiteResult as PytestSuiteResult
from experiment.orchestrator import ExperimentOrchestrator
from experiment.reviewer import Reviewer

TASK_ID = "expression_evaluator"
HIDDEN_SENTINEL = "VERIGATE_HIDDEN_TEST_SENTINEL_7f3c1a"


# --------------------------------------------------------------------------
# Fakes: reviewer (independent per call, no shared state) and pytest runner
# --------------------------------------------------------------------------


class _FakeReviewer:
    provider = "fake"
    model = "fake-model-v1"

    def __init__(self, call_log: list[str], counter: dict[str, int], fail_at_call_number: Optional[int]) -> None:
        self._call_log = call_log
        self._counter = counter
        self._fail_at_call_number = fail_at_call_number
        # Deliberately no `last_response_id`/etc: this fake does not
        # implement ProvidesResponseMetadata, mirroring MockReviewer.

    def review(self, condition: Condition, prompt) -> ReviewerAssessment:
        self._counter["n"] += 1
        n = self._counter["n"]
        self._call_log.append(f"review:{condition.value}:{n}")
        if self._fail_at_call_number is not None and n == self._fail_at_call_number:
            raise RuntimeError(f"simulated reviewer failure on call #{n}")
        return ReviewerAssessment(
            condition=condition,
            predicted_pass=True,
            confidence=70,
            suspected_issues=[],
            rationale=f"fake rationale for {condition.value}",
        )


def _make_reviewer_factory(
    call_log: list[str], fail_at_call_number: Optional[int] = None
):
    counter = {"n": 0}
    factory_calls: list[int] = []

    def factory() -> Reviewer:
        factory_calls.append(1)
        return _FakeReviewer(call_log, counter, fail_at_call_number)

    factory.calls = factory_calls  # type: ignore[attr-defined]
    return factory


def _test_suite_result(suite: str, *, passed: bool, passed_count: int, failed_count: int) -> PytestSuiteResult:
    return PytestSuiteResult(
        suite=suite,
        passed=passed,
        timed_out=False,
        exit_code=0 if passed else 1,
        duration_seconds=0.01,
        stdout="",
        stderr="",
        passed_count=passed_count,
        failed_count=failed_count,
    )


class _FakeRunner:
    def __init__(
        self,
        call_log: list[str],
        visible_result: Optional[PytestSuiteResult] = None,
        hidden_result: Optional[PytestSuiteResult] = None,
    ) -> None:
        self._call_log = call_log
        self._visible_result = visible_result or _test_suite_result(
            "visible", passed=True, passed_count=5, failed_count=0
        )
        self._hidden_result = hidden_result or _test_suite_result(
            "hidden", passed=True, passed_count=8, failed_count=0
        )

    def run_visible(self, task) -> PytestSuiteResult:
        self._call_log.append("visible")
        return self._visible_result

    def run_hidden(self, task) -> PytestSuiteResult:
        self._call_log.append("hidden")
        return self._hidden_result


def _make_orchestrator(
    tmp_path: Path,
    *,
    call_log: Optional[list[str]] = None,
    fail_at_call_number: Optional[int] = None,
    visible_result: Optional[PytestSuiteResult] = None,
    hidden_result: Optional[PytestSuiteResult] = None,
):
    call_log = call_log if call_log is not None else []
    factory = _make_reviewer_factory(call_log, fail_at_call_number=fail_at_call_number)
    runner = _FakeRunner(call_log, visible_result=visible_result, hidden_result=hidden_result)
    orchestrator = ExperimentOrchestrator(
        reviewer_factory=factory,
        output_dir=tmp_path,
        runner=runner,
    )
    return orchestrator, call_log, factory


# --------------------------------------------------------------------------
# Ordering: the critical scientific rule
# --------------------------------------------------------------------------


def test_visible_tests_run_before_any_reviewer_or_hidden_calls(tmp_path: Path):
    orchestrator, call_log, _ = _make_orchestrator(tmp_path)

    orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42)

    assert call_log[0] == "visible"
    review_indices = [i for i, entry in enumerate(call_log) if entry.startswith("review:")]
    assert all(i > 0 for i in review_indices)


def test_reviewer_calls_complete_before_hidden_tests_execute(tmp_path: Path):
    orchestrator, call_log, _ = _make_orchestrator(tmp_path)

    orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42)

    hidden_index = call_log.index("hidden")
    review_indices = [i for i, entry in enumerate(call_log) if entry.startswith("review:")]
    assert review_indices  # sanity: reviewer calls actually happened
    assert hidden_index > max(review_indices)


def test_hidden_tests_run_exactly_once_on_success(tmp_path: Path):
    orchestrator, call_log, _ = _make_orchestrator(tmp_path)

    orchestrator.run(task_id=TASK_ID, repetitions=3, random_seed=42)

    assert call_log.count("hidden") == 1
    assert call_log.count("visible") == 1


def test_full_call_order_is_visible_then_all_reviews_then_hidden(tmp_path: Path):
    orchestrator, call_log, _ = _make_orchestrator(tmp_path)

    orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42)

    assert call_log[0] == "visible"
    assert call_log[-1] == "hidden"
    assert all(entry.startswith("review:") for entry in call_log[1:-1])
    assert len(call_log) == 1 + (2 * 3) + 1  # visible + (2 reps * 3 conditions) + hidden


# --------------------------------------------------------------------------
# Visible-test failure: stop before reviewers/hidden
# --------------------------------------------------------------------------


def test_visible_failure_prevents_reviewer_and_hidden_calls(tmp_path: Path):
    failing_visible = _test_suite_result("visible", passed=False, passed_count=3, failed_count=2)
    orchestrator, call_log, _ = _make_orchestrator(tmp_path, visible_result=failing_visible)

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42)

    assert call_log == ["visible"]
    assert artifact.observations == []
    assert artifact.metadata.status == "failed"
    assert artifact.error is not None
    assert artifact.error.category == "visible_tests_failed"
    assert artifact.ground_truth is not None
    assert artifact.ground_truth.visible_passed is False
    assert artifact.ground_truth.hidden_passed is None
    # Never claims tests passed, and hashing/prompt-building never happened.
    assert artifact.metadata.candidate_sha256 is None
    assert artifact.metadata.specification_sha256 is None
    assert artifact.metadata.visible_tests_sha256 is None


# --------------------------------------------------------------------------
# Repetitions / observation counts / independence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("repetitions", [1, 2, 3])
def test_observation_count_equals_repetitions_times_three(tmp_path: Path, repetitions: int):
    orchestrator, _, _ = _make_orchestrator(tmp_path)

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=repetitions, random_seed=42)

    assert len(artifact.observations) == repetitions * 3
    assert artifact.metadata.status == "completed"


def test_each_condition_runs_exactly_once_per_repetition(tmp_path: Path):
    orchestrator, _, _ = _make_orchestrator(tmp_path)

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=3, random_seed=42)

    by_repetition: dict[int, set[Condition]] = {}
    for obs in artifact.observations:
        by_repetition.setdefault(obs.repetition_index, set()).add(obs.condition)

    assert set(by_repetition) == {0, 1, 2}
    for conditions in by_repetition.values():
        assert conditions == {Condition.A_NO_RESULT, Condition.B_VISIBLE_PASS, Condition.C_ADVERSARIAL}


def test_conditions_are_independently_invoked_with_a_fresh_reviewer_per_call(tmp_path: Path):
    orchestrator, _, factory = _make_orchestrator(tmp_path)

    orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42)

    # 1 probe call (to read provider/model) + 2 reps * 3 conditions.
    assert len(factory.calls) == 1 + 2 * 3


# --------------------------------------------------------------------------
# Reproducible seeded ordering
# --------------------------------------------------------------------------


def _execution_order(artifact: ExperimentArtifact) -> list[tuple[int, str]]:
    return [
        (obs.repetition_index, obs.condition.value)
        for obs in sorted(artifact.observations, key=lambda o: o.execution_order_index)
    ]


def test_same_seed_and_repetitions_produce_the_same_ordering(tmp_path: Path):
    orchestrator_a, _, _ = _make_orchestrator(tmp_path / "a")
    orchestrator_b, _, _ = _make_orchestrator(tmp_path / "b")

    artifact_a = orchestrator_a.run(task_id=TASK_ID, repetitions=3, random_seed=42)
    artifact_b = orchestrator_b.run(task_id=TASK_ID, repetitions=3, random_seed=42)

    assert _execution_order(artifact_a) == _execution_order(artifact_b)


def test_different_seeds_can_produce_different_orderings(tmp_path: Path):
    orchestrator_a, _, _ = _make_orchestrator(tmp_path / "a")
    orchestrator_b, _, _ = _make_orchestrator(tmp_path / "b")

    # Verified offline: seeds 42 and 7 give different 3-repetition orderings.
    artifact_a = orchestrator_a.run(task_id=TASK_ID, repetitions=3, random_seed=42)
    artifact_b = orchestrator_b.run(task_id=TASK_ID, repetitions=3, random_seed=7)

    assert _execution_order(artifact_a) != _execution_order(artifact_b)


# --------------------------------------------------------------------------
# Hashing: stable, frozen content
# --------------------------------------------------------------------------


def test_candidate_specification_visible_tests_hashes_are_stable(tmp_path: Path):
    orchestrator_a, _, _ = _make_orchestrator(tmp_path / "a")
    orchestrator_b, _, _ = _make_orchestrator(tmp_path / "b")

    artifact_a = orchestrator_a.run(task_id=TASK_ID, repetitions=1, random_seed=1)
    artifact_b = orchestrator_b.run(task_id=TASK_ID, repetitions=1, random_seed=2)

    for h in (
        artifact_a.metadata.candidate_sha256,
        artifact_a.metadata.specification_sha256,
        artifact_a.metadata.visible_tests_sha256,
    ):
        assert h is not None
        assert len(h) == 64  # hex sha256

    assert artifact_a.metadata.candidate_sha256 == artifact_b.metadata.candidate_sha256
    assert artifact_a.metadata.specification_sha256 == artifact_b.metadata.specification_sha256
    assert artifact_a.metadata.visible_tests_sha256 == artifact_b.metadata.visible_tests_sha256


# --------------------------------------------------------------------------
# Checkpointing and partial-failure durability
# --------------------------------------------------------------------------


def test_atomic_checkpoint_is_written_after_each_observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    checkpoint_observation_counts: list[int] = []
    original_save = ExperimentOrchestrator._save_checkpoint

    def spy(self, artifact, path):
        original_save(self, artifact, path)
        assert path.exists()
        on_disk = ExperimentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        checkpoint_observation_counts.append(len(on_disk.observations))

    monkeypatch.setattr(ExperimentOrchestrator, "_save_checkpoint", spy)

    orchestrator, _, _ = _make_orchestrator(tmp_path)
    artifact = orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42)

    # Observation counts recorded at each checkpoint must be non-decreasing,
    # end at repetitions*3, and include a checkpoint with 0 observations
    # (saved before any reviewer call, per requirement 3).
    assert checkpoint_observation_counts == sorted(checkpoint_observation_counts)
    assert checkpoint_observation_counts[0] == 0
    assert checkpoint_observation_counts[-1] == 6 == len(artifact.observations)

    # No leftover temp files from the write-then-rename dance.
    leftover_tmp_files = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp_files == []


def test_partial_results_survive_a_reviewer_failure(tmp_path: Path):
    # Fail on the 2nd reviewer call overall (repetition 0's 2nd condition).
    orchestrator, call_log, _ = _make_orchestrator(tmp_path, fail_at_call_number=2)

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=42)

    assert artifact.metadata.status == "partial"
    assert len(artifact.observations) == 1  # the 1st call succeeded and was preserved
    assert artifact.error is not None
    assert artifact.error.repetition_index == 0
    assert artifact.error.category == "RuntimeError"
    assert "simulated reviewer failure" in artifact.error.message

    # Reload from disk: the checkpoint must have preserved the same data.
    on_disk = ExperimentArtifact.model_validate_json(
        orchestrator.artifact_path(artifact.metadata.experiment_id).read_text(encoding="utf-8")
    )
    assert len(on_disk.observations) == 1
    assert on_disk.metadata.status == "partial"


def test_hidden_tests_do_not_run_following_a_reviewer_failure(tmp_path: Path):
    orchestrator, call_log, _ = _make_orchestrator(tmp_path, fail_at_call_number=1)

    orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42)

    assert "hidden" not in call_log


def test_reviewer_failure_stops_remaining_repetitions_too(tmp_path: Path):
    # Fail on the very first call; no further reviewer calls should happen
    # even though 2 repetitions (6 calls) were requested.
    orchestrator, call_log, _ = _make_orchestrator(tmp_path, fail_at_call_number=1)

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42)

    review_calls = [entry for entry in call_log if entry.startswith("review:")]
    assert len(review_calls) == 1
    assert artifact.observations == []
    assert artifact.metadata.status == "partial"


# --------------------------------------------------------------------------
# New experiment IDs never overwrite old runs
# --------------------------------------------------------------------------


def test_rerun_creates_a_new_experiment_id_and_does_not_overwrite(tmp_path: Path):
    orchestrator, _, _ = _make_orchestrator(tmp_path)

    artifact_1 = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=42)
    artifact_2 = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=42)

    assert artifact_1.metadata.experiment_id != artifact_2.metadata.experiment_id

    path_1 = orchestrator.artifact_path(artifact_1.metadata.experiment_id)
    path_2 = orchestrator.artifact_path(artifact_2.metadata.experiment_id)
    assert path_1 != path_2
    assert path_1.exists()
    assert path_2.exists()

    # The first run's artifact is untouched by the second run.
    reloaded_1 = ExperimentArtifact.model_validate_json(path_1.read_text(encoding="utf-8"))
    assert reloaded_1.metadata.experiment_id == artifact_1.metadata.experiment_id


# --------------------------------------------------------------------------
# Timestamps, artifact validation, and no leakage
# --------------------------------------------------------------------------


def test_timestamps_are_timezone_aware_utc(tmp_path: Path):
    orchestrator, _, _ = _make_orchestrator(tmp_path)

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=42)

    assert artifact.metadata.started_at.tzinfo is not None
    assert artifact.metadata.started_at.utcoffset() == timedelta(0)
    assert artifact.metadata.completed_at is not None
    assert artifact.metadata.completed_at.tzinfo is not None
    assert artifact.metadata.completed_at.utcoffset() == timedelta(0)

    for obs in artifact.observations:
        assert obs.result.timestamp.tzinfo is not None
        assert obs.result.timestamp.utcoffset() == timedelta(0)


def test_saved_artifact_validates_when_loaded_back_through_pydantic(tmp_path: Path):
    orchestrator, _, _ = _make_orchestrator(tmp_path)

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42)
    path = orchestrator.artifact_path(artifact.metadata.experiment_id)

    raw_text = path.read_text(encoding="utf-8")
    reloaded = ExperimentArtifact.model_validate_json(raw_text)

    assert reloaded.metadata.experiment_id == artifact.metadata.experiment_id
    assert len(reloaded.observations) == len(artifact.observations)
    assert reloaded.ground_truth == artifact.ground_truth
    assert reloaded.metadata.status == "completed"


def test_saved_artifact_contains_no_hidden_or_secret_content(tmp_path: Path):
    from experiment.loader import TaskLoader

    task = TaskLoader().load(TASK_ID)
    hidden_filenames = {p.name for p in task.hidden_tests_path.glob("*.py")}
    assert hidden_filenames  # sanity

    orchestrator, _, _ = _make_orchestrator(tmp_path)
    artifact = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=42)
    path = orchestrator.artifact_path(artifact.metadata.experiment_id)

    raw_text = path.read_text(encoding="utf-8")
    assert HIDDEN_SENTINEL not in raw_text
    assert "hidden_tests" not in raw_text
    for filename in hidden_filenames:
        assert filename not in raw_text
    assert "OPENAI_API_KEY" not in raw_text
    assert "sk-" not in raw_text


def test_no_experiment_artifacts_are_tracked_by_git():
    import subprocess

    from experiment.orchestrator import default_experiments_dir

    experiments_dir = default_experiments_dir()
    experiments_dir.mkdir(parents=True, exist_ok=True)
    probe_path = experiments_dir / f"{uuid.uuid4()}.json"
    probe_path.write_text("{}", encoding="utf-8")
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", str(probe_path)],
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert result.returncode == 0, "experiment artifacts must be git-ignored"
    finally:
        probe_path.unlink(missing_ok=True)
