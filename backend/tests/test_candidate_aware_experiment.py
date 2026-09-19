"""Tests for Milestone 8: candidate-aware reviewer experiments.

Every candidate artifact used here is a synthetic, temporary fixture
built under ``tmp_path`` -- never the real generated ``json_parser``
candidate ``3d51301c-b572-4891-9db4-84171b5b1b7c``. No test here makes a
network/paid API call, and no test evaluates hidden tests against that
real candidate. One test (``test_generated_candidate_environment_remains_sanitized``)
uses the real :class:`~experiment.runner.PytestRunner` (a real pytest
subprocess) against a synthetic, temporary task/candidate specifically to
prove environment sanitization end-to-end; everything else uses fakes.
"""

from __future__ import annotations

import hashlib
import json
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from experiment import cli
from experiment.candidate_loader import (
    CandidateArtifactLoadError,
    CandidateArtifactLoader,
    LoadedCandidate,
)
from experiment.candidate_models import CandidateArtifactMetadata
from experiment.conditions import Condition
from experiment.experiment_models import ExperimentArtifact
from experiment.loader import LoadedTask, TaskLoader
from experiment.models import ReviewerAssessment
from experiment.models import TestSuiteResult as PytestSuiteResult
from experiment.orchestrator import ExperimentOrchestrator
from experiment.reviewer import Reviewer
from experiment.runner import PytestRunner

TASK_ID = "toy_task"
TRACKED_REFERENCE_SOURCE = "def add(a, b):\n    return a + b  # TRACKED REFERENCE\n"
CANDIDATE_SOURCE = "def add(a, b):\n    return a + b  # GENERATED CANDIDATE\n"
HIDDEN_SENTINEL = "TOY_CANDIDATE_AWARE_HIDDEN_SENTINEL_d34db33f"


# --------------------------------------------------------------------------
# Fixture helpers -- all synthetic/temporary
# --------------------------------------------------------------------------


def _write_task(tasks_root: Path, task_id: str = TASK_ID) -> LoadedTask:
    task_dir = tasks_root / task_id
    (task_dir / "visible_tests").mkdir(parents=True)
    (task_dir / "hidden_tests").mkdir(parents=True)

    (task_dir / "specification.md").write_text("# Toy add spec.\n", encoding="utf-8")
    (task_dir / "toy.py").write_text(TRACKED_REFERENCE_SOURCE, encoding="utf-8")
    (task_dir / "visible_tests" / "test_visible.py").write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from toy import add


            def test_add_visible():
                assert add(1, 2) == 3
            """
        ),
        encoding="utf-8",
    )
    (task_dir / "hidden_tests" / "test_hidden.py").write_text(
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from toy import add

            # Sentinel: {HIDDEN_SENTINEL}

            def test_add_hidden():
                assert add(10, 20) == 30
            """
        ),
        encoding="utf-8",
    )
    (task_dir / "manifest.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "title": "Toy Add",
                "language": "python",
                "timeout_seconds": 10,
                "paths": {
                    "specification": "specification.md",
                    "candidate": "toy.py",
                    "visible_tests": "visible_tests",
                    "hidden_tests": "hidden_tests",
                },
            }
        ),
        encoding="utf-8",
    )
    return TaskLoader(tasks_root=tasks_root).load(task_id)


def _write_candidate_artifact(
    candidates_root: Path,
    *,
    task_id: str = TASK_ID,
    source: str = CANDIDATE_SOURCE,
    candidate_id: Optional[uuid.UUID] = None,
    attempt_count: int = 2,
    required_module_filename: str = "toy.py",
) -> uuid.UUID:
    candidate_id = candidate_id if candidate_id is not None else uuid.uuid4()
    artifact_dir = candidates_root / task_id / str(candidate_id)
    artifact_dir.mkdir(parents=True)
    (artifact_dir / required_module_filename).write_text(source, encoding="utf-8")

    now = datetime.now(timezone.utc)
    metadata = CandidateArtifactMetadata(
        candidate_id=candidate_id,
        task_id=task_id,
        provider="mock",
        model="mock-deterministic-v1",
        prompt_version="candidate-v1",
        random_seed=42,
        max_attempts=3,
        status="completed",
        stop_reason="visible_tests_passed",
        required_module_filename=required_module_filename,
        specification_sha256="0" * 64,
        starter_sha256="1" * 64,
        visible_tests_sha256="2" * 64,
        final_source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        visible_tests_passed=True,
        visible_passed_count=1,
        visible_failed_count=0,
        attempt_count=attempt_count,
        attempts=[],
        model_sampling_deterministic=True,
        seed_semantics="test seed semantics",
        started_at=now,
        completed_at=now,
    )
    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
    return candidate_id


def _load_candidate(task: LoadedTask, candidates_root: Path, candidate_id: uuid.UUID) -> LoadedCandidate:
    return CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


def _suite_result(suite: str, *, passed: bool, passed_count: int, failed_count: int) -> PytestSuiteResult:
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


# --------------------------------------------------------------------------
# Fakes: reviewer that records the candidate source it was shown, and a
# runner that only ever supports the isolated-workspace candidate methods
# (never the tracked-task run_visible/run_hidden).
# --------------------------------------------------------------------------


class _RecordingCandidateReviewer:
    provider = "fake"
    model = "fake-model-v1"

    def __init__(
        self,
        call_log: list[str],
        counter: dict[str, int],
        captured_sources: list[str],
        fail_at_call_number: Optional[int] = None,
    ) -> None:
        self._call_log = call_log
        self._counter = counter
        self._captured_sources = captured_sources
        self._fail_at_call_number = fail_at_call_number

    def review(self, condition: Condition, prompt) -> ReviewerAssessment:
        self._counter["n"] += 1
        n = self._counter["n"]
        self._call_log.append(f"review:{condition.value}:{n}")
        self._captured_sources.append(prompt.sections.candidate_source)
        if self._fail_at_call_number is not None and n == self._fail_at_call_number:
            raise RuntimeError(f"simulated reviewer failure on call #{n}")
        return ReviewerAssessment(
            condition=condition,
            predicted_pass=True,
            confidence=65,
            suspected_issues=[],
            rationale=f"fake rationale for {condition.value}",
        )


def _make_reviewer_factory(
    call_log: list[str], captured_sources: list[str], fail_at_call_number: Optional[int] = None
):
    counter = {"n": 0}
    factory_calls: list[int] = []

    def factory() -> Reviewer:
        factory_calls.append(1)
        return _RecordingCandidateReviewer(call_log, counter, captured_sources, fail_at_call_number)

    factory.calls = factory_calls  # type: ignore[attr-defined]
    return factory


class _FakeCandidateWorkspaceRunner:
    """Only supports the isolated-workspace candidate methods.

    ``run_visible``/``run_hidden`` (the tracked-task methods) raise if
    called at all -- a candidate-aware experiment must never touch them.
    """

    def __init__(
        self,
        call_log: list[str],
        *,
        visible_result: Optional[PytestSuiteResult] = None,
        hidden_result: Optional[PytestSuiteResult] = None,
    ) -> None:
        self._call_log = call_log
        self._visible_result = visible_result or _suite_result(
            "visible", passed=True, passed_count=1, failed_count=0
        )
        self._hidden_result = hidden_result or _suite_result(
            "hidden", passed=True, passed_count=1, failed_count=0
        )
        self.workspace_snapshots: list[dict[str, object]] = []

    def run_visible(self, task) -> PytestSuiteResult:
        raise AssertionError(
            "candidate-aware experiment must never run visible tests against the tracked task"
        )

    def run_hidden(self, task) -> PytestSuiteResult:
        raise AssertionError(
            "candidate-aware experiment must never run hidden tests against the tracked task"
        )

    def run_visible_workspace(self, tests_dir: Path, timeout_seconds: float, *, workspace: Path):
        self._call_log.append("visible_workspace")
        self._snapshot(workspace, tests_dir, "visible")
        return self._visible_result

    def run_hidden_workspace(self, tests_dir: Path, timeout_seconds: float, *, workspace: Path):
        self._call_log.append("hidden_workspace")
        self._snapshot(workspace, tests_dir, "hidden")
        return self._hidden_result

    def _snapshot(self, workspace: Path, tests_dir: Path, suite: str) -> None:
        files = {
            str(p.relative_to(workspace)): p.read_text(encoding="utf-8")
            for p in Path(workspace).rglob("*")
            if p.is_file()
        }
        self.workspace_snapshots.append(
            {"suite": suite, "files": files, "tests_dir_name": Path(tests_dir).name}
        )


def _make_candidate_orchestrator(
    tmp_path: Path,
    *,
    call_log: Optional[list[str]] = None,
    captured_sources: Optional[list[str]] = None,
    fail_at_call_number: Optional[int] = None,
    visible_result: Optional[PytestSuiteResult] = None,
    hidden_result: Optional[PytestSuiteResult] = None,
    tasks_root: Optional[Path] = None,
):
    call_log = call_log if call_log is not None else []
    captured_sources = captured_sources if captured_sources is not None else []
    factory = _make_reviewer_factory(call_log, captured_sources, fail_at_call_number=fail_at_call_number)
    runner = _FakeCandidateWorkspaceRunner(
        call_log, visible_result=visible_result, hidden_result=hidden_result
    )
    orchestrator = ExperimentOrchestrator(
        reviewer_factory=factory,
        tasks_root=tasks_root,
        output_dir=tmp_path / "experiments",
        runner=runner,  # type: ignore[arg-type]
    )
    return orchestrator, call_log, captured_sources, runner, factory


def _setup(tmp_path: Path):
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root)
    candidate = _load_candidate(task, candidates_root, candidate_id)
    return task, candidate, candidates_root


# --------------------------------------------------------------------------
# 8. Candidate prompt contains supplied candidate source, not tracked reference
# --------------------------------------------------------------------------


def test_reviewer_prompt_uses_supplied_candidate_source_not_tracked_reference(tmp_path: Path) -> None:
    task, candidate, _ = _setup(tmp_path)
    orchestrator, _, captured_sources, _, _ = _make_candidate_orchestrator(
        tmp_path, tasks_root=tmp_path / "tasks"
    )

    orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=1, candidate=candidate)

    assert captured_sources  # sanity: reviewer calls actually happened
    for source in captured_sources:
        assert source == CANDIDATE_SOURCE
        assert source != TRACKED_REFERENCE_SOURCE


# --------------------------------------------------------------------------
# 9/10. Workspace isolation: visible workspace has no hidden tests, and
# vice versa.
# --------------------------------------------------------------------------


def test_visible_candidate_workspace_contains_no_hidden_tests(tmp_path: Path) -> None:
    task, candidate, _ = _setup(tmp_path)
    orchestrator, _, _, runner, _ = _make_candidate_orchestrator(tmp_path, tasks_root=tmp_path / "tasks")

    orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=1, candidate=candidate)

    visible_snapshots = [s for s in runner.workspace_snapshots if s["suite"] == "visible"]
    assert visible_snapshots
    files = visible_snapshots[0]["files"]
    assert "toy.py" in files
    assert files["toy.py"] == CANDIDATE_SOURCE
    assert any("test_visible" in name for name in files)
    assert all("hidden" not in name.lower() for name in files)
    assert HIDDEN_SENTINEL not in "".join(files.values())


def test_hidden_candidate_workspace_contains_no_visible_tests(tmp_path: Path) -> None:
    task, candidate, _ = _setup(tmp_path)
    orchestrator, _, _, runner, _ = _make_candidate_orchestrator(tmp_path, tasks_root=tmp_path / "tasks")

    orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=1, candidate=candidate)

    hidden_snapshots = [s for s in runner.workspace_snapshots if s["suite"] == "hidden"]
    assert hidden_snapshots
    files = hidden_snapshots[0]["files"]
    assert "toy.py" in files
    assert files["toy.py"] == CANDIDATE_SOURCE
    assert any("test_hidden" in name for name in files)
    assert all("visible" not in name.lower() for name in files)


# --------------------------------------------------------------------------
# 11. Generated candidate environment remains sanitized (real subprocess)
# --------------------------------------------------------------------------


def test_generated_candidate_environment_remains_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end with the *real* PytestRunner: secrets set on this process
    must not leak into either the visible-workspace or hidden-workspace
    subprocess used by a candidate-aware experiment."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANOTHER_SECRET_TOKEN", "top-secret-should-not-leak")

    task_dir = tmp_path / "tasks" / TASK_ID
    (task_dir / "visible_tests").mkdir(parents=True)
    (task_dir / "hidden_tests").mkdir(parents=True)
    (task_dir / "specification.md").write_text("# spec\n", encoding="utf-8")
    (task_dir / "toy.py").write_text(TRACKED_REFERENCE_SOURCE, encoding="utf-8")

    env_check = textwrap.dedent(
        """
        import os

        def test_no_secret_env_leaks():
            assert "OPENAI_API_KEY" not in os.environ
            assert "ANOTHER_SECRET_TOKEN" not in os.environ
        """
    )
    (task_dir / "visible_tests" / "test_env.py").write_text(env_check, encoding="utf-8")
    (task_dir / "hidden_tests" / "test_env.py").write_text(env_check, encoding="utf-8")
    (task_dir / "manifest.json").write_text(
        json.dumps(
            {
                "task_id": TASK_ID,
                "title": "Toy",
                "language": "python",
                "timeout_seconds": 10,
                "paths": {
                    "specification": "specification.md",
                    "candidate": "toy.py",
                    "visible_tests": "visible_tests",
                    "hidden_tests": "hidden_tests",
                },
            }
        ),
        encoding="utf-8",
    )
    task = TaskLoader(tasks_root=tmp_path / "tasks").load(TASK_ID)

    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, source=CANDIDATE_SOURCE)
    candidate = _load_candidate(task, candidates_root, candidate_id)

    factory = _make_reviewer_factory([], [])
    orchestrator = ExperimentOrchestrator(
        reviewer_factory=factory,
        tasks_root=tmp_path / "tasks",
        output_dir=tmp_path / "experiments",
        runner=PytestRunner(),
    )

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=1, candidate=candidate)

    assert artifact.metadata.status == "completed"
    assert artifact.ground_truth is not None
    assert artifact.ground_truth.visible_passed is True
    assert artifact.ground_truth.hidden_passed is True
    raw = artifact.model_dump_json()
    assert "sk-should-not-leak" not in raw
    assert "top-secret-should-not-leak" not in raw


# --------------------------------------------------------------------------
# 12. Visible failure -> zero reviewer calls, zero hidden calls
# --------------------------------------------------------------------------


def test_candidate_visible_failure_causes_zero_reviewer_and_hidden_calls(tmp_path: Path) -> None:
    task, candidate, _ = _setup(tmp_path)
    failing_visible = _suite_result("visible", passed=False, passed_count=0, failed_count=1)
    orchestrator, call_log, captured_sources, runner, _ = _make_candidate_orchestrator(
        tmp_path, tasks_root=tmp_path / "tasks", visible_result=failing_visible
    )

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=1, candidate=candidate)

    assert call_log == ["visible_workspace"]
    assert captured_sources == []
    assert artifact.observations == []
    assert artifact.metadata.status == "failed"
    assert artifact.ground_truth is not None
    assert artifact.ground_truth.hidden_passed is None
    assert "hidden_workspace" not in call_log


# --------------------------------------------------------------------------
# 13. Successful order is visible -> all randomized reviews -> hidden
# --------------------------------------------------------------------------


def test_candidate_aware_successful_order_is_visible_then_reviews_then_hidden(tmp_path: Path) -> None:
    task, candidate, _ = _setup(tmp_path)
    orchestrator, call_log, _, _, _ = _make_candidate_orchestrator(tmp_path, tasks_root=tmp_path / "tasks")

    orchestrator.run(task_id=TASK_ID, repetitions=2, random_seed=42, candidate=candidate)

    assert call_log[0] == "visible_workspace"
    assert call_log[-1] == "hidden_workspace"
    assert all(entry.startswith("review:") for entry in call_log[1:-1])
    assert len(call_log) == 1 + (2 * 3) + 1


# --------------------------------------------------------------------------
# 14. Reviewer failure preserves partial observations, prevents hidden run
# --------------------------------------------------------------------------


def test_candidate_aware_reviewer_failure_preserves_partial_and_skips_hidden(tmp_path: Path) -> None:
    task, candidate, _ = _setup(tmp_path)
    orchestrator, call_log, _, runner, _ = _make_candidate_orchestrator(
        tmp_path, tasks_root=tmp_path / "tasks", fail_at_call_number=2
    )

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=42, candidate=candidate)

    assert artifact.metadata.status == "partial"
    assert len(artifact.observations) == 1
    assert artifact.error is not None
    assert "hidden_workspace" not in call_log

    on_disk = ExperimentArtifact.model_validate_json(
        orchestrator.artifact_path(artifact.metadata.experiment_id).read_text(encoding="utf-8")
    )
    assert len(on_disk.observations) == 1
    assert on_disk.metadata.status == "partial"
    assert on_disk.metadata.candidate_id == candidate.candidate_id


# --------------------------------------------------------------------------
# 15. Exact same candidate source/hash used for visible, prompts, hidden
# --------------------------------------------------------------------------


def test_same_candidate_source_used_for_visible_prompts_and_hidden(tmp_path: Path) -> None:
    task, candidate, _ = _setup(tmp_path)
    orchestrator, _, captured_sources, runner, _ = _make_candidate_orchestrator(
        tmp_path, tasks_root=tmp_path / "tasks"
    )

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=1, candidate=candidate)

    visible_files = next(s["files"] for s in runner.workspace_snapshots if s["suite"] == "visible")
    hidden_files = next(s["files"] for s in runner.workspace_snapshots if s["suite"] == "hidden")

    assert visible_files["toy.py"] == CANDIDATE_SOURCE
    assert hidden_files["toy.py"] == CANDIDATE_SOURCE
    assert all(source == CANDIDATE_SOURCE for source in captured_sources)

    expected_hash = hashlib.sha256(CANDIDATE_SOURCE.encode("utf-8")).hexdigest()
    assert artifact.metadata.candidate_sha256 == expected_hash
    assert artifact.metadata.candidate_source_sha256 == expected_hash
    assert candidate.metadata.final_source_sha256 == expected_hash


# --------------------------------------------------------------------------
# 16. Candidate provenance saved in experiment metadata
# --------------------------------------------------------------------------


def test_candidate_provenance_is_saved_in_experiment_metadata(tmp_path: Path) -> None:
    task, candidate, candidates_root = _setup(tmp_path)
    orchestrator, _, _, _, _ = _make_candidate_orchestrator(tmp_path, tasks_root=tmp_path / "tasks")

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=1, candidate=candidate)
    metadata = artifact.metadata

    assert metadata.candidate_id == candidate.candidate_id
    assert metadata.candidate_source_sha256 == candidate.metadata.final_source_sha256
    assert metadata.generator_provider == candidate.metadata.provider == "mock"
    assert metadata.generator_model == candidate.metadata.model
    assert metadata.generation_prompt_version == candidate.metadata.prompt_version
    assert metadata.generation_attempt_count == candidate.metadata.attempt_count == 2
    assert metadata.generation_artifact_path is not None
    assert not Path(metadata.generation_artifact_path).is_absolute()
    assert str(candidate.candidate_id) in metadata.generation_artifact_path


# --------------------------------------------------------------------------
# 17. Backward compatibility: tracked-candidate experiments unaffected
# --------------------------------------------------------------------------


def test_backward_compatible_tracked_candidate_experiment_has_none_provenance(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    call_log: list[str] = []
    captured_sources: list[str] = []
    factory = _make_reviewer_factory(call_log, captured_sources)

    class _TrackedFakeRunner:
        def run_visible(self, task) -> PytestSuiteResult:
            call_log.append("visible")
            return _suite_result("visible", passed=True, passed_count=1, failed_count=0)

        def run_hidden(self, task) -> PytestSuiteResult:
            call_log.append("hidden")
            return _suite_result("hidden", passed=True, passed_count=1, failed_count=0)

    orchestrator = ExperimentOrchestrator(
        reviewer_factory=factory,
        tasks_root=tmp_path / "tasks",
        output_dir=tmp_path / "experiments",
        runner=_TrackedFakeRunner(),  # type: ignore[arg-type]
    )

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=1)

    assert artifact.metadata.status == "completed"
    assert artifact.metadata.candidate_id is None
    assert artifact.metadata.candidate_source_sha256 is None
    assert artifact.metadata.generator_provider is None
    assert artifact.metadata.generator_model is None
    assert artifact.metadata.generation_prompt_version is None
    assert artifact.metadata.generation_attempt_count is None
    assert artifact.metadata.generation_artifact_path is None
    assert captured_sources == [TRACKED_REFERENCE_SOURCE] * 3


def test_orchestrator_rejects_candidate_generated_for_a_different_task(tmp_path: Path) -> None:
    task_a = _write_task(tmp_path / "tasks", task_id="task_a")
    _write_task(tmp_path / "tasks", task_id="task_b")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, task_id="task_a")
    candidate = _load_candidate(task_a, candidates_root, candidate_id)

    orchestrator, _, _, _, _ = _make_candidate_orchestrator(tmp_path, tasks_root=tmp_path / "tasks")

    with pytest.raises(ValueError, match="task_a"):
        orchestrator.run(task_id="task_b", repetitions=1, random_seed=1, candidate=candidate)


# --------------------------------------------------------------------------
# 18. Tracked reference file is never touched
# --------------------------------------------------------------------------


def test_tracked_reference_file_is_never_touched_by_candidate_aware_run(tmp_path: Path) -> None:
    # Uses the real json_parser task definition (read-only) so this proves
    # the guarantee against the actual tracked file path referenced in the
    # milestone, but with a synthetic candidate and a fake runner so no
    # real subprocess ever touches json_parser's real hidden tests.
    real_task = TaskLoader().load("json_parser")
    before = real_task.candidate_path.read_text(encoding="utf-8")
    before_hash = hashlib.sha256(before.encode("utf-8")).hexdigest()

    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(
        candidates_root,
        task_id="json_parser",
        source="def parse(s):\n    return None  # synthetic\n",
        required_module_filename="json_parser.py",
    )
    candidate = _load_candidate(real_task, candidates_root, candidate_id)

    orchestrator, _, _, _, _ = _make_candidate_orchestrator(tmp_path)  # default tasks_root

    orchestrator.run(task_id="json_parser", repetitions=1, random_seed=1, candidate=candidate)

    after = real_task.candidate_path.read_text(encoding="utf-8")
    assert after == before
    assert hashlib.sha256(after.encode("utf-8")).hexdigest() == before_hash


# --------------------------------------------------------------------------
# 19. No hidden source/path/result enters A/B/C prompts or saved metadata
# --------------------------------------------------------------------------


def test_no_hidden_content_leaks_into_candidate_aware_artifact(tmp_path: Path) -> None:
    task, candidate, _ = _setup(tmp_path)
    orchestrator, _, _, _, _ = _make_candidate_orchestrator(tmp_path, tasks_root=tmp_path / "tasks")

    artifact = orchestrator.run(task_id=TASK_ID, repetitions=1, random_seed=1, candidate=candidate)
    path = orchestrator.artifact_path(artifact.metadata.experiment_id)
    raw = path.read_text(encoding="utf-8")

    assert HIDDEN_SENTINEL not in raw
    assert "test_hidden.py" not in raw
    assert "hidden_tests" not in raw


# --------------------------------------------------------------------------
# CLI: --candidate-id
# --------------------------------------------------------------------------


def test_cli_run_experiment_with_candidate_id_mock(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root)

    summary = cli.run_experiment(
        TASK_ID,
        "mock",
        1,
        42,
        tasks_root=tmp_path / "tasks",
        output_dir=tmp_path / "experiments",
        candidate_id=str(candidate_id),
        candidates_root=candidates_root,
    )

    assert summary["status"] == "completed"
    assert summary["candidate_id"] == str(candidate_id)
    assert summary["candidate_source_sha256"] == hashlib.sha256(CANDIDATE_SOURCE.encode()).hexdigest()
    assert summary["hidden"] is not None
    assert summary["hidden"]["passed"] is True


def test_cli_run_experiment_without_candidate_id_is_backward_compatible(tmp_path: Path) -> None:
    _write_task(tmp_path / "tasks")

    summary = cli.run_experiment(
        TASK_ID,
        "mock",
        1,
        42,
        tasks_root=tmp_path / "tasks",
        output_dir=tmp_path / "experiments",
    )

    assert summary["status"] == "completed"
    assert summary["candidate_id"] is None
    assert summary["candidate_source_sha256"] is None


def test_cli_rejects_candidate_id_for_the_wrong_task(tmp_path: Path) -> None:
    _write_task(tmp_path / "tasks", task_id="task_a")
    _write_task(tmp_path / "tasks", task_id="task_b")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, task_id="task_a")

    with pytest.raises(CandidateArtifactLoadError):
        cli.run_experiment(
            "task_b",
            "mock",
            1,
            42,
            tasks_root=tmp_path / "tasks",
            output_dir=tmp_path / "experiments",
            candidate_id=str(candidate_id),
            candidates_root=candidates_root,
        )


def test_cli_rejects_invalid_candidate_id_format(tmp_path: Path) -> None:
    _write_task(tmp_path / "tasks")

    with pytest.raises(CandidateArtifactLoadError):
        cli.run_experiment(
            TASK_ID,
            "mock",
            1,
            42,
            tasks_root=tmp_path / "tasks",
            output_dir=tmp_path / "experiments",
            candidate_id="not-a-uuid",
        )


def test_cli_main_experiment_with_candidate_id_prints_candidate_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercises real ``main()`` argv parsing end-to-end for ``--candidate-id``.

    Redirects the two roots ``main()`` resolves by default (tasks root and
    candidates root) to temporary, synthetic locations via monkeypatch,
    so this never reads/writes the real ``backend/tasks`` or
    ``backend/data/candidates`` trees.
    """

    import experiment.candidate_loader as candidate_loader_module
    import experiment.loader as loader_module

    _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root)

    monkeypatch.setattr(loader_module, "DEFAULT_TASKS_ROOT", tmp_path / "tasks")
    monkeypatch.setattr(candidate_loader_module, "default_candidates_dir", lambda: candidates_root)

    exit_code = cli.main(
        [
            "experiment",
            "--task",
            TASK_ID,
            "--candidate-id",
            str(candidate_id),
            "--provider",
            "mock",
            "--repetitions",
            "1",
            "--seed",
            "42",
            "--output-dir",
            str(tmp_path / "experiments"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["candidate_id"] == str(candidate_id)
    assert payload["candidate_source_sha256"]
    assert HIDDEN_SENTINEL not in captured.out
