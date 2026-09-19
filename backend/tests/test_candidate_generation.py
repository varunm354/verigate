"""Tests for hidden-blind coding-agent candidate generation.

All tests use mock/fake generators and (except one tiny toy-task
integration) a fake pytest runner. No test makes a network or paid API
call. Hidden tests are never run as part of candidate generation.
"""

from __future__ import annotations

import ast
import hashlib
import json
import textwrap
import uuid
from pathlib import Path
from typing import Optional

import pytest
from pydantic import ValidationError

from experiment import cli
from experiment.candidate_generator import CandidateGenerator, MockCandidateGenerator
from experiment.candidate_models import (
    CandidateArtifactMetadata,
    CandidateGenerationError,
    CandidateGenerationRequest,
    CandidateGeneratorOutput,
    VisibleTestFeedback,
)
from experiment.candidate_orchestrator import (
    CandidateGenerationOrchestrator,
    default_candidates_dir,
)
from experiment.candidate_prompts import (
    build_candidate_generation_context,
    build_generation_prompt,
)
from experiment.loader import LoadedTask, TaskLoader
from experiment.models import TestSuiteResult as PytestSuiteResult
from experiment.runner import PytestRunner

JSON_PARSER = "json_parser"
EXPRESSION_EVALUATOR = "expression_evaluator"
JSON_PARSER_HIDDEN_SENTINEL = "VERIGATE_HIDDEN_TEST_SENTINEL_json_parser_9e2b6f"
TOY_HIDDEN_SENTINEL = "TOY_HIDDEN_SENTINEL_c0ffee99"
EXPERIMENT_DIR = Path(__file__).resolve().parent.parent / "experiment"
REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _feedback(**overrides: object) -> VisibleTestFeedback:
    defaults: dict[str, object] = dict(
        passed=False,
        timed_out=False,
        exit_code=1,
        duration_seconds=0.01,
        stdout="1 failed",
        stderr="",
        passed_count=0,
        failed_count=1,
    )
    defaults.update(overrides)
    return VisibleTestFeedback(**defaults)  # type: ignore[arg-type]


def _suite_result(*, passed: bool, passed_count: int, failed_count: int, timed_out: bool = False) -> PytestSuiteResult:
    return PytestSuiteResult(
        suite="visible",
        passed=passed,
        timed_out=timed_out,
        exit_code=None if timed_out else (0 if passed else 1),
        duration_seconds=0.01,
        stdout="ok" if passed else "failed",
        stderr="",
        passed_count=passed_count,
        failed_count=failed_count,
    )


def _write_toy_task(tasks_root: Path) -> str:
    """Minimal task with starter + a hidden-test sentinel that must not leak."""

    task_id = "toy_add"
    task_dir = tasks_root / task_id
    (task_dir / "visible_tests").mkdir(parents=True)
    (task_dir / "hidden_tests").mkdir(parents=True)
    (task_dir / "starter").mkdir()

    (task_dir / "specification.md").write_text("# Add two integers.\n", encoding="utf-8")
    (task_dir / "toy.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (task_dir / "starter" / "toy.py").write_text(
        "def add(a, b):\n    raise NotImplementedError\n", encoding="utf-8"
    )
    (task_dir / "visible_tests" / "test_visible.py").write_text(
        textwrap.dedent(
            """
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from toy import add


            def test_add():
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

            # Sentinel for automated leakage checks: {TOY_HIDDEN_SENTINEL}

            def test_hidden_add():
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
                "timeout_seconds": 5,
                "paths": {
                    "specification": "specification.md",
                    "candidate": "toy.py",
                    "starter": "starter/toy.py",
                    "visible_tests": "visible_tests",
                    "hidden_tests": "hidden_tests",
                },
            }
        ),
        encoding="utf-8",
    )
    return task_id


PASSING_TOY_SOURCE = "def add(a, b):\n    return a + b\n"
FAILING_TOY_SOURCE = "def add(a, b):\n    raise NotImplementedError\n"


class RecordingRunner:
    """Fake runner that snapshots the isolated workspace and never runs hidden tests."""

    def __init__(self, results: Optional[list[PytestSuiteResult]] = None) -> None:
        self.results = list(results) if results is not None else []
        self._index = 0
        self.workspace_snapshots: list[dict[str, object]] = []
        self.run_hidden_called = False
        self.run_visible_on_task_called = False

    def run_visible(self, task: LoadedTask) -> PytestSuiteResult:
        self.run_visible_on_task_called = True
        raise AssertionError("candidate generation must not run visible tests against the tracked task")

    def run_hidden(self, task: LoadedTask) -> PytestSuiteResult:
        self.run_hidden_called = True
        raise AssertionError("candidate generation must not run hidden tests")

    def run_visible_workspace(
        self, tests_dir: Path, timeout_seconds: float, *, workspace: Path
    ) -> PytestSuiteResult:
        files = {
            str(p.relative_to(workspace)): p.read_text(encoding="utf-8")
            for p in workspace.rglob("*")
            if p.is_file()
        }
        dirs = {
            str(p.relative_to(workspace))
            for p in workspace.rglob("*")
            if p.is_dir()
        }
        self.workspace_snapshots.append(
            {"files": files, "dirs": dirs, "workspace": str(workspace), "tests_dir": str(tests_dir)}
        )
        if self._index < len(self.results):
            result = self.results[self._index]
        else:
            result = _suite_result(passed=False, passed_count=0, failed_count=1)
        self._index += 1
        return result


def _make_orchestrator(
    tmp_path: Path,
    *,
    generator: CandidateGenerator,
    runner: Optional[RecordingRunner] = None,
    tasks_root: Optional[Path] = None,
) -> tuple[CandidateGenerationOrchestrator, RecordingRunner]:
    recording = runner if runner is not None else RecordingRunner()
    orchestrator = CandidateGenerationOrchestrator(
        generator_factory=lambda: generator,
        tasks_root=tasks_root,
        output_dir=tmp_path / "candidates",
        runner=recording,  # type: ignore[arg-type]
    )
    return orchestrator, recording


# --------------------------------------------------------------------------
# A. Starter + backward-compatible manifest
# --------------------------------------------------------------------------


def test_json_parser_declares_starter_and_expression_evaluator_omits_it() -> None:
    json_task = TaskLoader().load(JSON_PARSER)
    expr_task = TaskLoader().load(EXPRESSION_EVALUATOR)

    assert json_task.manifest.paths.starter == "starter/json_parser.py"
    assert json_task.starter_path is not None
    assert json_task.starter_path.is_file()
    assert "NotImplementedError" in json_task.starter_path.read_text(encoding="utf-8")

    assert expr_task.manifest.paths.starter is None
    assert expr_task.starter_path is None


def test_json_parser_starter_is_not_the_reference_implementation() -> None:
    task = TaskLoader().load(JSON_PARSER)
    assert task.starter_path is not None
    starter = task.starter_path.read_text(encoding="utf-8")
    reference = task.candidate_path.read_text(encoding="utf-8")
    assert starter != reference
    assert "NotImplementedError" in starter
    assert "json.loads" in reference


def test_task_without_starter_cannot_build_generation_context() -> None:
    task = TaskLoader().load(EXPRESSION_EVALUATOR)
    with pytest.raises(CandidateGenerationError, match="no starter"):
        build_candidate_generation_context(task)


# --------------------------------------------------------------------------
# B. Models
# --------------------------------------------------------------------------


def test_generator_output_rejects_empty_source_and_unknown_path_fields() -> None:
    with pytest.raises(ValidationError):
        CandidateGeneratorOutput(source="   ", summary="empty")
    with pytest.raises(ValidationError):
        CandidateGeneratorOutput(
            source="x = 1\n",
            summary="ok",
            output_path="/tmp/evil.py",  # type: ignore[call-arg]
        )


def test_generation_request_rejects_path_separators_in_filename_and_unknown_fields() -> None:
    kwargs = dict(
        task_id="toy_add",
        specification="spec",
        starter_source="def add(a, b): raise NotImplementedError\n",
        visible_tests_source={"test_visible.py": "def test_add(): pass\n"},
        required_module_filename="toy.py",
        attempt_number=1,
        max_attempts=3,
        random_seed=42,
    )
    CandidateGenerationRequest(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CandidateGenerationRequest(**{**kwargs, "required_module_filename": "../toy.py"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CandidateGenerationRequest(**{**kwargs, "output_path": "/tmp/x.py"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CandidateGenerationRequest(**{**kwargs, "attempt_number": 2})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# C/D. Orchestrator loop
# --------------------------------------------------------------------------


def test_mock_generator_returns_starter_by_default(tmp_path: Path) -> None:
    task_id = _write_toy_task(tmp_path / "tasks")
    generator = MockCandidateGenerator()
    orchestrator, _ = _make_orchestrator(
        tmp_path, generator=generator, tasks_root=tmp_path / "tasks"
    )

    metadata = orchestrator.run(task_id=task_id, max_attempts=2, random_seed=42)

    assert metadata.attempt_count == 2
    assert metadata.stop_reason == "max_attempts_reached"
    assert metadata.status == "failed"
    assert metadata.visible_tests_passed is False
    assert generator.requests[0].previous_source is None
    assert generator.requests[1].previous_source == generator.requests[0].starter_source


def test_stops_early_when_visible_tests_pass(tmp_path: Path) -> None:
    task_id = _write_toy_task(tmp_path / "tasks")
    generator = MockCandidateGenerator(sources=[FAILING_TOY_SOURCE, PASSING_TOY_SOURCE])
    runner = RecordingRunner(
        results=[
            _suite_result(passed=False, passed_count=0, failed_count=1),
            _suite_result(passed=True, passed_count=1, failed_count=0),
        ]
    )
    orchestrator, _ = _make_orchestrator(
        tmp_path, generator=generator, runner=runner, tasks_root=tmp_path / "tasks"
    )

    metadata = orchestrator.run(task_id=task_id, max_attempts=3, random_seed=7)

    assert metadata.attempt_count == 2
    assert metadata.stop_reason == "visible_tests_passed"
    assert metadata.status == "completed"
    assert metadata.visible_tests_passed is True
    assert metadata.visible_passed_count == 1
    assert metadata.visible_failed_count == 0
    assert len(generator.requests) == 2


def test_later_attempts_receive_only_visible_feedback(tmp_path: Path) -> None:
    task_id = _write_toy_task(tmp_path / "tasks")
    generator = MockCandidateGenerator()
    runner = RecordingRunner(
        results=[_suite_result(passed=False, passed_count=0, failed_count=4)]
    )
    orchestrator, _ = _make_orchestrator(
        tmp_path, generator=generator, runner=runner, tasks_root=tmp_path / "tasks"
    )

    orchestrator.run(task_id=task_id, max_attempts=2, random_seed=1)

    first, second = generator.requests
    assert first.visible_feedback is None
    assert second.visible_feedback is not None
    assert second.visible_feedback.failed_count == 4
    assert second.visible_feedback.passed_count == 0
    assert "failed" in (second.visible_feedback.stdout + second.visible_feedback.stderr)

    prompt = build_generation_prompt(second)
    assert TOY_HIDDEN_SENTINEL not in prompt.text
    assert "test_hidden.py" not in prompt.text
    assert "hidden_tests" not in prompt.text
    serialized = str(second.model_dump())
    assert TOY_HIDDEN_SENTINEL not in serialized
    assert "test_hidden.py" not in serialized
    assert "hidden_tests" not in serialized


def test_provider_failure_stops_and_records_error(tmp_path: Path) -> None:
    class BoomGenerator:
        provider = "mock"
        model = "boom"

        def generate(self, request: CandidateGenerationRequest) -> CandidateGeneratorOutput:
            raise RuntimeError("simulated provider failure")

    task_id = _write_toy_task(tmp_path / "tasks")
    orchestrator, _ = _make_orchestrator(
        tmp_path, generator=BoomGenerator(), tasks_root=tmp_path / "tasks"
    )

    metadata = orchestrator.run(task_id=task_id, max_attempts=3, random_seed=1)

    assert metadata.status == "failed"
    assert metadata.stop_reason == "provider_error"
    assert metadata.attempt_count == 0
    assert metadata.error is not None
    assert "simulated provider failure" in metadata.error


def test_artifact_is_written_atomically_with_required_filename(tmp_path: Path) -> None:
    task_id = _write_toy_task(tmp_path / "tasks")
    generator = MockCandidateGenerator(sources=[PASSING_TOY_SOURCE])
    runner = RecordingRunner(results=[_suite_result(passed=True, passed_count=1, failed_count=0)])
    orchestrator, _ = _make_orchestrator(
        tmp_path, generator=generator, runner=runner, tasks_root=tmp_path / "tasks"
    )

    metadata = orchestrator.run(task_id=task_id, max_attempts=1, random_seed=42)
    artifact_dir = orchestrator.artifact_dir(task_id, metadata.candidate_id)

    assert (artifact_dir / "toy.py").is_file()
    assert (artifact_dir / "metadata.json").is_file()
    assert {p.name for p in artifact_dir.iterdir()} == {"toy.py", "metadata.json"}
    assert (artifact_dir / "toy.py").read_text(encoding="utf-8") == PASSING_TOY_SOURCE

    reloaded = CandidateArtifactMetadata.model_validate_json(
        (artifact_dir / "metadata.json").read_text(encoding="utf-8")
    )
    assert reloaded.candidate_id == metadata.candidate_id
    assert reloaded.final_source_sha256 == hashlib.sha256(PASSING_TOY_SOURCE.encode()).hexdigest()
    assert reloaded.random_seed == 42
    assert "does not make provider model sampling deterministic" in reloaded.seed_semantics.lower() or (
        "does not make" in reloaded.seed_semantics
    )


def test_real_workspace_runner_passes_visible_tests_for_toy_task(tmp_path: Path) -> None:
    task_id = _write_toy_task(tmp_path / "tasks")
    generator = MockCandidateGenerator(sources=[PASSING_TOY_SOURCE])
    orchestrator = CandidateGenerationOrchestrator(
        generator_factory=lambda: generator,
        tasks_root=tmp_path / "tasks",
        output_dir=tmp_path / "candidates",
        runner=PytestRunner(),
    )

    metadata = orchestrator.run(task_id=task_id, max_attempts=2, random_seed=3)

    assert metadata.visible_tests_passed is True
    assert metadata.stop_reason == "visible_tests_passed"
    assert metadata.attempt_count == 1
    assert metadata.visible_passed_count == 1


# --------------------------------------------------------------------------
# E. Hidden-test blindness (structural)
# --------------------------------------------------------------------------


_GENERATION_MODULES = [
    "candidate_models.py",
    "candidate_prompts.py",
    "candidate_generator.py",
    "openai_candidate_generator.py",
    "candidate_orchestrator.py",
]


def _attribute_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


@pytest.mark.parametrize("filename", _GENERATION_MODULES)
def test_generation_modules_never_access_hidden_tests_path_or_run_hidden(filename: str) -> None:
    names = _attribute_names(EXPERIMENT_DIR / filename)
    assert "hidden_tests_path" not in names, filename
    assert "run_hidden" not in names, filename


def test_generation_context_excludes_hidden_path_content_and_sentinel() -> None:
    task = TaskLoader().load(JSON_PARSER)
    hidden_files = sorted(task.hidden_tests_path.glob("*.py"))
    assert hidden_files

    context = build_candidate_generation_context(task)
    serialized = str(context.to_dict())
    prompt = build_generation_prompt(
        CandidateGenerationRequest(
            task_id=context.task_id,
            specification=context.specification,
            starter_source=context.starter_source,
            visible_tests_source=dict(context.visible_tests_source),
            required_module_filename=context.required_module_filename,
            attempt_number=1,
            max_attempts=3,
            random_seed=42,
        )
    )

    for blob in (serialized, prompt.text):
        assert task.hidden_tests_path.name not in blob
        assert JSON_PARSER_HIDDEN_SENTINEL not in blob
        for hidden_file in hidden_files:
            assert hidden_file.name not in blob
            hidden_source = hidden_file.read_text(encoding="utf-8")
            assert hidden_source not in blob

    assert context.starter_source == task.starter_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert context.specification == task.specification_path.read_text(encoding="utf-8")
    assert "json_parser.py" == context.required_module_filename
    # Starter, not the tracked reference, is what the generator sees.
    assert context.starter_source != task.candidate_path.read_text(encoding="utf-8")


def test_workspace_contains_only_candidate_and_visible_tests(tmp_path: Path) -> None:
    task_id = _write_toy_task(tmp_path / "tasks")
    generator = MockCandidateGenerator()
    runner = RecordingRunner()
    orchestrator, _ = _make_orchestrator(
        tmp_path, generator=generator, runner=runner, tasks_root=tmp_path / "tasks"
    )

    orchestrator.run(task_id=task_id, max_attempts=1, random_seed=1)

    assert runner.run_hidden_called is False
    assert runner.run_visible_on_task_called is False
    assert runner.workspace_snapshots
    snapshot = runner.workspace_snapshots[0]
    files = snapshot["files"]
    dirs = snapshot["dirs"]
    assert "toy.py" in files
    assert "visible_tests/test_visible.py" in files
    assert "hidden_tests" not in dirs
    assert "hidden_tests/test_hidden.py" not in files
    assert "test_hidden.py" not in files
    assert "specification.md" not in files
    assert "manifest.json" not in files
    blob = "".join(files.values())
    assert TOY_HIDDEN_SENTINEL not in blob
    assert "test_hidden" not in blob


def test_hidden_runner_is_never_called_during_json_parser_generation(tmp_path: Path) -> None:
    generator = MockCandidateGenerator()
    runner = RecordingRunner()
    orchestrator, _ = _make_orchestrator(tmp_path, generator=generator, runner=runner)

    orchestrator.run(task_id=JSON_PARSER, max_attempts=1, random_seed=99)

    assert runner.run_hidden_called is False
    assert runner.run_visible_on_task_called is False
    snapshot = runner.workspace_snapshots[0]
    files = snapshot["files"]
    assert "json_parser.py" in files
    assert "visible_tests/test_visible.py" in files
    assert all("hidden" not in name for name in files)
    blob = "".join(files.values())
    assert JSON_PARSER_HIDDEN_SENTINEL not in blob
    assert "test_hidden.py" not in blob


def test_fake_generator_cannot_choose_an_output_path(tmp_path: Path) -> None:
    class PathTryingGenerator:
        provider = "evil"
        model = "evil-v1"

        def generate(self, request: CandidateGenerationRequest) -> CandidateGeneratorOutput:
            # Source text may mention other paths; the harness must still
            # write only the required module filename.
            return CandidateGeneratorOutput(
                source="# intended for /tmp/evil.py\ndef add(a, b):\n    return a + b\n",
                summary="trying to write elsewhere",
            )

    task_id = _write_toy_task(tmp_path / "tasks")
    runner = RecordingRunner(results=[_suite_result(passed=True, passed_count=1, failed_count=0)])
    orchestrator, _ = _make_orchestrator(
        tmp_path, generator=PathTryingGenerator(), runner=runner, tasks_root=tmp_path / "tasks"
    )

    metadata = orchestrator.run(task_id=task_id, max_attempts=1, random_seed=1)
    artifact_dir = orchestrator.artifact_dir(task_id, metadata.candidate_id)

    assert (artifact_dir / "toy.py").is_file()
    assert not (artifact_dir / "evil.py").exists()
    assert not list(artifact_dir.glob("tmp*"))
    workspace_files = runner.workspace_snapshots[0]["files"]
    assert set(workspace_files) == {"toy.py", "visible_tests/test_visible.py"}


def test_tracked_json_parser_reference_is_not_overwritten(tmp_path: Path) -> None:
    task = TaskLoader().load(JSON_PARSER)
    before = task.candidate_path.read_text(encoding="utf-8")
    before_hash = hashlib.sha256(before.encode()).hexdigest()

    generator = MockCandidateGenerator()
    runner = RecordingRunner()
    orchestrator, _ = _make_orchestrator(tmp_path, generator=generator, runner=runner)
    orchestrator.run(task_id=JSON_PARSER, max_attempts=1, random_seed=1)

    after = task.candidate_path.read_text(encoding="utf-8")
    assert after == before
    assert hashlib.sha256(after.encode()).hexdigest() == before_hash
    # Generated source lives under the artifact dir, not the task dir.
    metadata_files = list((tmp_path / "candidates" / JSON_PARSER).glob("*/json_parser.py"))
    assert metadata_files
    assert metadata_files[0].read_text(encoding="utf-8") != before


def test_generated_artifacts_are_git_ignored(tmp_path: Path) -> None:
    import subprocess

    candidates_dir = default_candidates_dir()
    probe = candidates_dir / "probe-task" / str(uuid.uuid4()) / "metadata.json"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("{}", encoding="utf-8")
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", str(probe)],
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, "candidate artifacts must be git-ignored"
    finally:
        probe.unlink(missing_ok=True)


def test_gitignore_covers_backend_data() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "backend/data/*" in gitignore


# --------------------------------------------------------------------------
# F. CLI
# --------------------------------------------------------------------------


def test_cli_generate_candidate_mock_summary(tmp_path: Path) -> None:
    task_id = _write_toy_task(tmp_path / "tasks")
    generator = MockCandidateGenerator(sources=[PASSING_TOY_SOURCE])
    runner = RecordingRunner(results=[_suite_result(passed=True, passed_count=1, failed_count=0)])

    original_orch = cli.CandidateGenerationOrchestrator

    def _factory(**kwargs):
        kwargs["tasks_root"] = tmp_path / "tasks"
        kwargs["output_dir"] = tmp_path / "candidates"
        kwargs["runner"] = runner
        kwargs["generator_factory"] = lambda: generator
        return original_orch(**kwargs)

    # Patch via the cli module's imported name.
    cli.CandidateGenerationOrchestrator = _factory  # type: ignore[misc,assignment]
    try:
        summary = cli.run_generate_candidate(
            task_id, "mock", 3, 42, output_dir=tmp_path / "candidates"
        )
    finally:
        cli.CandidateGenerationOrchestrator = original_orch

    assert summary["status"] == "completed"
    assert summary["visible_tests_passed"] is True
    assert summary["provider"] == "mock"
    assert summary["model"] == "mock-deterministic-v1"
    assert summary["attempt_count"] == 1
    assert summary["final_source_sha256"]
    assert summary["candidate_id"]
    assert summary["artifact_path"]
    assert summary["random_seed"] == 42
    assert summary["model_sampling_deterministic"] is True


def test_cli_rejects_model_override_for_mock() -> None:
    with pytest.raises(ValueError, match="--model"):
        cli.run_generate_candidate(JSON_PARSER, "mock", 1, 1, model="gpt-should-not-be-allowed")


def test_cli_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        cli.run_generate_candidate(JSON_PARSER, "not-a-provider", 1, 1)


def test_cli_generate_candidate_requires_starter(tmp_path: Path) -> None:
    with pytest.raises(CandidateGenerationError, match="no starter"):
        cli.run_generate_candidate(
            EXPRESSION_EVALUATOR, "mock", 1, 1, output_dir=tmp_path / "unused"
        )


def test_cli_main_generate_candidate_mock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    task_id = _write_toy_task(tmp_path / "tasks")
    original_orch = cli.CandidateGenerationOrchestrator
    generator = MockCandidateGenerator(sources=[FAILING_TOY_SOURCE])
    runner = RecordingRunner(
        results=[_suite_result(passed=False, passed_count=0, failed_count=1)]
    )

    def _factory(**kwargs):
        kwargs["tasks_root"] = tmp_path / "tasks"
        kwargs["output_dir"] = tmp_path / "candidates"
        kwargs["runner"] = runner
        kwargs["generator_factory"] = lambda: generator
        return original_orch(**kwargs)

    cli.CandidateGenerationOrchestrator = _factory  # type: ignore[misc,assignment]
    try:
        exit_code = cli.main(
            [
                "generate-candidate",
                "--task",
                task_id,
                "--provider",
                "mock",
                "--max-attempts",
                "2",
                "--seed",
                "42",
                "--output-dir",
                str(tmp_path / "candidates"),
            ]
        )
    finally:
        cli.CandidateGenerationOrchestrator = original_orch

    captured = capsys.readouterr()
    assert exit_code == 1  # visible tests did not pass
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert payload["stop_reason"] == "max_attempts_reached"
    assert payload["attempt_count"] == 2
    assert payload["visible_tests_passed"] is False
    assert payload["provider"] == "mock"
    assert JSON_PARSER_HIDDEN_SENTINEL not in captured.out
    assert TOY_HIDDEN_SENTINEL not in captured.out
    assert "hidden_tests" not in captured.out


# --------------------------------------------------------------------------
# Reference baseline still intact (not part of candidate generation)
# --------------------------------------------------------------------------


def test_json_parser_reference_still_passes_visible_and_hidden_suites() -> None:
    task = TaskLoader().load(JSON_PARSER)
    runner = PytestRunner()
    visible = runner.run_visible(task)
    hidden = runner.run_hidden(task)
    assert visible.passed is True
    assert visible.passed_count == 45
    assert visible.failed_count == 0
    assert hidden.passed is True
    assert hidden.passed_count == 178
    assert hidden.failed_count == 0
