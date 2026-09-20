"""Tests for Milestone 10: durable preregistered campaign runner.

Every campaign, candidate, and experiment artifact here is synthetic and
lives under ``tmp_path``. Generators, reviewers, and pytest runners are
fakes. No test initializes a real OpenAI client, makes a network/paid
call, loads the real ``json_parser`` candidate
``3d51301c-b572-4891-9db4-84171b5b1b7c``, or executes hidden tests
against a real candidate artifact.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pytest
from pydantic import ValidationError

from experiment import cli
from experiment.campaign_models import (
    CAMPAIGN_SCHEMA_VERSION,
    CampaignArtifact,
    CampaignDirtyWorktreeError,
    CampaignLockError,
)
from experiment.campaign_orchestrator import (
    CampaignOrchestrator,
    campaign_status_summary,
    create_campaign,
    hash_protocol_file,
    inspect_repository,
    parse_cutoff,
    select_next_task,
)
from experiment.campaign_store import campaign_lock_path, load_campaign, save_campaign
from experiment.candidate_generator import CandidateGenerator
from experiment.candidate_models import CandidateGenerationRequest, CandidateGeneratorOutput
from experiment.candidate_orchestrator import CandidateGenerationOrchestrator
from experiment.conditions import Condition
from experiment.experiment_models import ExperimentArtifact
from experiment.models import ReviewerAssessment
from experiment.models import TestSuiteResult as PytestSuiteResult
from experiment.orchestrator import ExperimentOrchestrator
from experiment.reviewer import MockReviewer, Reviewer

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO_ROOT / "docs" / "research_protocol.md"
HIDDEN_SENTINEL = "CAMPAIGN_SYNTHETIC_HIDDEN_SENTINEL_e8a1c2"
JSON_PARSER = "json_parser"
PACKAGE_RESOLVER = "package_resolver"
PASSING_SOURCE = "VALUE = 1  # CAMPAIGN_PASS\n"
FAILING_SOURCE = "VALUE = 0  # CAMPAIGN_FAIL\n"
HIDDEN_FAIL_SOURCE = "VALUE = 1  # CAMPAIGN_PASS CAMPAIGN_HIDDEN_FAIL\n"
CUTOFF = "2026-09-21T12:00:00-07:00"
CUTOFF_UTC = datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Synthetic tasks, fakes, and campaign helpers
# --------------------------------------------------------------------------


def _write_synthetic_task(tasks_root: Path, task_id: str, filename: str) -> None:
    task_dir = tasks_root / task_id
    (task_dir / "visible_tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "hidden_tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "starter").mkdir(exist_ok=True)
    (task_dir / "specification.md").write_text(f"# Synthetic {task_id}\n", encoding="utf-8")
    (task_dir / filename).write_text(PASSING_SOURCE, encoding="utf-8")
    (task_dir / "starter" / filename).write_text(FAILING_SOURCE, encoding="utf-8")
    (task_dir / "visible_tests" / "test_visible.py").write_text(
        f"def test_visible_{task_id}():\n    assert True\n",
        encoding="utf-8",
    )
    (task_dir / "hidden_tests" / "test_hidden.py").write_text(
        textwrap.dedent(
            f"""
            # Sentinel for automated leakage checks: {HIDDEN_SENTINEL}
            def test_hidden_{task_id}():
                assert True
            """
        ),
        encoding="utf-8",
    )
    (task_dir / "manifest.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "title": f"Synthetic {task_id}",
                "language": "python",
                "timeout_seconds": 5,
                "paths": {
                    "specification": "specification.md",
                    "candidate": filename,
                    "starter": f"starter/{filename}",
                    "visible_tests": "visible_tests",
                    "hidden_tests": "hidden_tests",
                },
            }
        ),
        encoding="utf-8",
    )


def _write_campaign_tasks(tasks_root: Path) -> None:
    _write_synthetic_task(tasks_root, JSON_PARSER, "json_parser.py")
    _write_synthetic_task(tasks_root, PACKAGE_RESOLVER, "resolver.py")


def _suite(suite: str, *, passed: bool) -> PytestSuiteResult:
    return PytestSuiteResult(
        suite=suite,
        passed=passed,
        timed_out=False,
        exit_code=0 if passed else 1,
        duration_seconds=0.01,
        stdout="ok" if passed else "failed",
        stderr="",
        passed_count=1 if passed else 0,
        failed_count=0 if passed else 1,
    )


def _workspace_blob(workspace: Path) -> str:
    parts = [
        path.read_text(encoding="utf-8")
        for path in Path(workspace).rglob("*")
        if path.is_file()
    ]
    return "\n".join(parts)


class FakeCampaignRunner:
    """Fake pytest runner for isolated workspaces only. Never runs real tests."""

    def __init__(self) -> None:
        self.visible_workspace_calls = 0
        self.hidden_workspace_calls = 0
        self.tracked_visible_calls = 0
        self.tracked_hidden_calls = 0
        self.hidden_passed_log: list[bool] = []

    def run_visible(self, task) -> PytestSuiteResult:
        self.tracked_visible_calls += 1
        raise AssertionError("campaign must not run visible tests against a tracked task")

    def run_hidden(self, task) -> PytestSuiteResult:
        self.tracked_hidden_calls += 1
        raise AssertionError("campaign must not run hidden tests against a tracked task")

    def run_visible_workspace(self, tests_dir: Path, timeout_seconds: float, *, workspace: Path):
        self.visible_workspace_calls += 1
        blob = _workspace_blob(workspace)
        return _suite("visible", passed="CAMPAIGN_FAIL" not in blob)

    def run_hidden_workspace(self, tests_dir: Path, timeout_seconds: float, *, workspace: Path):
        self.hidden_workspace_calls += 1
        blob = _workspace_blob(workspace)
        passed = "CAMPAIGN_HIDDEN_FAIL" not in blob
        self.hidden_passed_log.append(passed)
        return _suite("hidden", passed=passed)


class ScriptedGenerator:
    provider = "mock"
    model = "mock-deterministic-v1"

    def __init__(
        self,
        fail_seeds: set[int] | frozenset[int] = frozenset(),
        hidden_fail_seeds: set[int] | frozenset[int] = frozenset(),
        on_generate: Optional[Callable[[CandidateGenerationRequest], None]] = None,
    ) -> None:
        self.fail_seeds = set(fail_seeds)
        self.hidden_fail_seeds = set(hidden_fail_seeds)
        self.on_generate = on_generate
        self.requests: list[CandidateGenerationRequest] = []

    def generate(self, request: CandidateGenerationRequest) -> CandidateGeneratorOutput:
        self.requests.append(request)
        if self.on_generate is not None:
            self.on_generate(request)
        if request.random_seed in self.fail_seeds:
            source = FAILING_SOURCE
        elif request.random_seed in self.hidden_fail_seeds:
            source = HIDDEN_FAIL_SOURCE
        else:
            source = PASSING_SOURCE
        return CandidateGeneratorOutput(source=source, summary=f"seed {request.random_seed}")


class VaryingConfidenceReviewer:
    provider = "mock"
    model = "mock-deterministic-v1"

    def __init__(self, counter: dict[str, int]) -> None:
        self._counter = counter

    def review(self, condition: Condition, prompt) -> ReviewerAssessment:
        self._counter["n"] += 1
        confidence = 10 + (self._counter["n"] % 81)
        return ReviewerAssessment(
            condition=condition,
            predicted_pass=confidence >= 50,
            confidence=confidence,
            suspected_issues=[],
            rationale=f"synthetic confidence {confidence}",
        )


class FailingReviewer:
    provider = "mock"
    model = "mock-deterministic-v1"

    def __init__(self, counter: dict[str, int], fail_at: int) -> None:
        self._counter = counter
        self._fail_at = fail_at

    def review(self, condition: Condition, prompt) -> ReviewerAssessment:
        self._counter["n"] += 1
        if self._counter["n"] == self._fail_at:
            raise RuntimeError(f"simulated reviewer failure on call #{self._counter['n']}")
        return MockReviewer().review(condition, prompt)


def _clean_inspect(_repo: Path) -> tuple[Optional[str], bool]:
    return "deadbeefcafebabe000000000000000000000000", False


def _create(tmp_path: Path, **overrides: object) -> CampaignArtifact:
    _write_campaign_tasks(tmp_path / "tasks")
    kwargs: dict[str, object] = dict(
        generator_provider="mock",
        generator_model="mock-deterministic-v1",
        reviewer_provider="mock",
        reviewer_model="mock-deterministic-v1",
        cutoff=CUTOFF,
        campaigns_dir=tmp_path / "campaigns",
        protocol_path=PROTOCOL_PATH,
        inspect_repo=_clean_inspect,
    )
    kwargs.update(overrides)
    return create_campaign(**kwargs)  # type: ignore[arg-type]


def _make_runner(
    tmp_path: Path,
    artifact: CampaignArtifact,
    *,
    generator: Optional[CandidateGenerator] = None,
    reviewer_factory: Optional[Callable[[], Reviewer]] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    runner: Optional[FakeCampaignRunner] = None,
) -> tuple[CampaignOrchestrator, FakeCampaignRunner, CandidateGenerator]:
    generator = generator if generator is not None else ScriptedGenerator()
    runner = runner if runner is not None else FakeCampaignRunner()
    counter = {"n": 0}

    def default_reviewer_factory() -> Reviewer:
        return VaryingConfidenceReviewer(counter)

    orchestrator = CampaignOrchestrator(
        generator_factory=lambda: generator,
        reviewer_factory=reviewer_factory or default_reviewer_factory,
        tasks_root=tmp_path / "tasks",
        candidates_dir=tmp_path / "candidates",
        experiments_dir=tmp_path / "experiments",
        campaigns_dir=tmp_path / "campaigns",
        runner=runner,  # type: ignore[arg-type]
        now_fn=now_fn or (lambda: datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)),
    )
    return orchestrator, runner, generator


def _campaign_json(tmp_path: Path, campaign_id: uuid.UUID) -> str:
    return (tmp_path / "campaigns" / f"{campaign_id}.json").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 1-4. Creation, dirty worktree, provenance, initial task
# --------------------------------------------------------------------------


def test_campaign_create_makes_no_provider_or_api_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("campaign-create must not initialize a provider or API client")

    import experiment.openai_candidate_generator as openai_candidate_module
    import experiment.openai_reviewer as openai_reviewer_module

    monkeypatch.setattr(openai_reviewer_module.OpenAIReviewer, "__init__", boom)
    monkeypatch.setattr(openai_candidate_module.OpenAICandidateGenerator, "__init__", boom)
    monkeypatch.setattr(openai_reviewer_module, "OpenAI", boom)
    monkeypatch.setattr(openai_candidate_module, "OpenAI", boom)

    artifact = _create(
        tmp_path,
        generator_provider="openai",
        generator_model="gpt-5.6-luna",
        reviewer_provider="openai",
        reviewer_model="gpt-5.6-luna",
    )
    assert artifact.status == "created"
    assert artifact.config.generator_provider == "openai"
    assert artifact.config.generator_model == "gpt-5.6-luna"
    assert artifact.config.reviewer_provider == "openai"
    assert artifact.config.reviewer_model == "gpt-5.6-luna"
    assert artifact.generation_attempts == []
    assert artifact.qualifying_candidates == []


def test_dirty_tracked_worktree_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CampaignDirtyWorktreeError, match="dirty"):
        _create(tmp_path, inspect_repo=lambda _repo: ("abc", True))
    assert list((tmp_path / "campaigns").glob("*.json")) == []


def test_inspect_repository_ignores_untracked_and_gitignored_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        commands.append(list(args))
        completed = subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if "rev-parse" in args:
            completed.stdout = "abc123def456\n"
        return completed

    monkeypatch.setattr(subprocess, "run", fake_run)
    commit, dirty = inspect_repository(REPO_ROOT)
    assert commit == "abc123def456"
    assert dirty is False
    status_cmd = [cmd for cmd in commands if "status" in cmd][0]
    assert "--untracked-files=no" in status_cmd
    assert "--porcelain" in status_cmd


def test_protocol_hash_and_repository_commit_are_recorded(tmp_path: Path) -> None:
    artifact = _create(tmp_path)
    assert artifact.provenance.research_protocol_sha256 == hash_protocol_file(PROTOCOL_PATH)
    assert artifact.provenance.repository_commit == "deadbeefcafebabe000000000000000000000000"
    assert artifact.provenance.repository_dirty is False
    assert artifact.provenance.protocol_path == "docs/research_protocol.md"
    assert artifact.schema_version == CAMPAIGN_SCHEMA_VERSION


def test_initial_next_task_is_json_parser(tmp_path: Path) -> None:
    artifact = _create(tmp_path)
    next_task = select_next_task(
        task_ids=artifact.config.task_ids,
        qualifying_counts=dict(artifact.qualifying_counts),
        target_per_task=artifact.config.target_per_task,
        generation_attempt_count=0,
    )
    assert next_task == JSON_PARSER
    summary = campaign_status_summary(artifact)
    assert summary["next_scheduled_task"] == JSON_PARSER
    assert artifact.next_generation_seed == 42
    assert artifact.next_reviewer_seed == 1000


def test_cutoff_stores_original_pacific_meaning_and_utc_equivalent() -> None:
    parsed = parse_cutoff(CUTOFF)
    assert parsed.timezone == "America/Los_Angeles"
    assert parsed.utc == CUTOFF_UTC
    naive = parse_cutoff("2026-09-21T12:00:00")
    assert naive.utc == CUTOFF_UTC


# --------------------------------------------------------------------------
# 5-11. Scheduling, seeds, attrition, quotas
# --------------------------------------------------------------------------


def test_tasks_alternate_after_successful_and_failed_generations(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=2, max_generation_attempts=8)
    generator = ScriptedGenerator(fail_seeds={43})
    orchestrator, _, _ = _make_runner(tmp_path, artifact, generator=generator)
    result = orchestrator.run(artifact.campaign_id)

    tasks = [attempt.task_id for attempt in result.generation_attempts]
    assert tasks[:3] == [JSON_PARSER, PACKAGE_RESOLVER, JSON_PARSER]
    assert result.generation_attempts[0].status == "qualified"
    assert result.generation_attempts[1].status == "attrition"
    assert result.generation_attempts[1].generation_seed == 43
    assert result.qualifying_candidates[0].task_id == JSON_PARSER
    assert result.attrition[0].generation_seed == 43
    assert result.attrition[0].task_id == PACKAGE_RESOLVER


def test_every_generation_attempt_consumes_exactly_one_sequential_seed(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=2, max_generation_attempts=8)
    generator = ScriptedGenerator(fail_seeds={43, 45})
    orchestrator, _, gen = _make_runner(tmp_path, artifact, generator=generator)
    result = orchestrator.run(artifact.campaign_id)

    seeds = [attempt.generation_seed for attempt in result.generation_attempts]
    assert seeds == list(range(42, 42 + len(seeds)))
    assert result.next_generation_seed == 42 + len(seeds)
    request_seeds = [request.random_seed for request in gen.requests]
    assert set(request_seeds) <= set(seeds)
    assert 42 in request_seeds


def test_failed_visible_generation_is_attrition_with_zero_reviews(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=1, max_generation_attempts=4)
    generator = ScriptedGenerator(fail_seeds={42})
    review_counter = {"n": 0}

    def reviewer_factory() -> Reviewer:
        return VaryingConfidenceReviewer(review_counter)

    orchestrator, runner, _ = _make_runner(
        tmp_path, artifact, generator=generator, reviewer_factory=reviewer_factory
    )
    result = orchestrator.run(artifact.campaign_id)

    first = result.generation_attempts[0]
    assert first.task_id == JSON_PARSER
    assert first.status == "attrition"
    assert first.visible_tests_passed is False
    assert result.attrition[0].generation_seed == 42
    assert "no reviewer calls" in result.attrition[0].reason
    experiments = list((tmp_path / "experiments").glob("*.json"))
    # First attempt produced no experiment; later qualifying package_resolver may have.
    first_candidate = first.candidate_id
    for path in experiments:
        loaded = ExperimentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        assert loaded.metadata.candidate_id != first_candidate
    assert review_counter["n"] == 9 * sum(
        1 for record in result.qualifying_candidates if record.experiment_status == "completed"
    )


def test_qualifying_candidates_receive_sequential_reviewer_seeds(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=2)
    orchestrator, _, _ = _make_runner(tmp_path, artifact)
    result = orchestrator.run(artifact.campaign_id)

    reviewer_seeds = [record.reviewer_seed for record in result.qualifying_candidates]
    assert reviewer_seeds == [1000, 1001, 1002, 1003]
    assert result.next_reviewer_seed == 1004
    for index, record in enumerate(result.qualifying_candidates):
        assert record.qualification_index == index
        assert record.experiment_status == "completed"
        assert record.reviewer_observation_count == 9


def test_qualifying_counts_are_maintained_independently_per_task(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=2, max_generation_attempts=8)
    generator = ScriptedGenerator(fail_seeds={43, 45})
    orchestrator, _, _ = _make_runner(tmp_path, artifact, generator=generator)
    result = orchestrator.run(artifact.campaign_id)

    assert result.qualifying_counts[JSON_PARSER] == 2
    assert result.qualifying_counts[PACKAGE_RESOLVER] == 2
    json_n = sum(1 for record in result.qualifying_candidates if record.task_id == JSON_PARSER)
    pkg_n = sum(1 for record in result.qualifying_candidates if record.task_id == PACKAGE_RESOLVER)
    assert json_n == 2
    assert pkg_n == 2
    assert len(result.attrition) == 2


def test_full_synthetic_campaign_reaches_six_candidates_per_task(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=6, repetitions=3)
    generator = ScriptedGenerator(hidden_fail_seeds={44, 47, 52})
    orchestrator, runner, _ = _make_runner(tmp_path, artifact, generator=generator)
    result = orchestrator.run(artifact.campaign_id)

    assert result.status == "completed"
    assert result.qualifying_counts == {JSON_PARSER: 6, PACKAGE_RESOLVER: 6}
    assert len(result.qualifying_candidates) == 12
    assert len(result.generation_attempts) == 12
    assert [attempt.generation_seed for attempt in result.generation_attempts] == list(range(42, 54))
    assert [record.reviewer_seed for record in result.qualifying_candidates] == list(range(1000, 1012))
    assert [attempt.task_id for attempt in result.generation_attempts] == [
        JSON_PARSER,
        PACKAGE_RESOLVER,
    ] * 6
    assert all(record.experiment_status == "completed" for record in result.qualifying_candidates)
    assert all(record.reviewer_observation_count == 9 for record in result.qualifying_candidates)
    assert runner.hidden_workspace_calls == 12
    assert runner.tracked_visible_calls == 0
    assert runner.tracked_hidden_calls == 0
    assert False in runner.hidden_passed_log and True in runner.hidden_passed_log


def test_after_one_task_fills_only_the_underfilled_task_is_scheduled(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=2, max_generation_attempts=10)
    generator = ScriptedGenerator(fail_seeds={43, 45})
    orchestrator, _, _ = _make_runner(tmp_path, artifact, generator=generator)
    result = orchestrator.run(artifact.campaign_id)

    tasks = [attempt.task_id for attempt in result.generation_attempts]
    assert tasks[0] == JSON_PARSER
    assert tasks[1] == PACKAGE_RESOLVER
    assert tasks[2] == JSON_PARSER
    assert JSON_PARSER not in tasks[3:]
    assert set(tasks[3:]) == {PACKAGE_RESOLVER}
    assert result.qualifying_counts[JSON_PARSER] == 2
    assert result.qualifying_counts[PACKAGE_RESOLVER] == 2


# --------------------------------------------------------------------------
# 12-14. Cutoff and maximum-generation guard
# --------------------------------------------------------------------------


def test_cutoff_prevents_starting_a_new_generation(tmp_path: Path) -> None:
    artifact = _create(tmp_path)
    orchestrator, runner, generator = _make_runner(
        tmp_path,
        artifact,
        now_fn=lambda: CUTOFF_UTC + timedelta(seconds=1),
    )
    result = orchestrator.run(artifact.campaign_id)

    assert result.status == "stopped_cutoff"
    assert result.generation_attempts == []
    assert result.qualifying_candidates == []
    assert isinstance(generator, ScriptedGenerator)
    assert generator.requests == []
    assert runner.visible_workspace_calls == 0
    assert runner.hidden_workspace_calls == 0


def test_operation_already_underway_may_finish_after_cutoff(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=6)
    clock = {"now": CUTOFF_UTC - timedelta(minutes=1)}

    def now() -> datetime:
        return clock["now"]

    def advance(_request: CandidateGenerationRequest) -> None:
        clock["now"] = CUTOFF_UTC + timedelta(minutes=1)

    generator = ScriptedGenerator(on_generate=advance)
    orchestrator, runner, _ = _make_runner(tmp_path, artifact, generator=generator, now_fn=now)
    result = orchestrator.run(artifact.campaign_id)

    assert result.status == "stopped_cutoff"
    assert len(result.generation_attempts) == 1
    assert result.generation_attempts[0].status == "qualified"
    assert len(result.qualifying_candidates) == 1
    assert result.qualifying_candidates[0].experiment_status == "completed"
    assert result.qualifying_candidates[0].reviewer_observation_count == 9
    assert runner.hidden_workspace_calls == 1
    assert result.qualifying_counts[JSON_PARSER] == 1
    assert result.qualifying_counts[PACKAGE_RESOLVER] == 0


def test_maximum_generation_guard_blocks_without_changing_the_target(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=6, max_generation_attempts=4)
    generator = ScriptedGenerator(fail_seeds={42, 43, 44, 45})
    orchestrator, runner, _ = _make_runner(tmp_path, artifact, generator=generator)
    result = orchestrator.run(artifact.campaign_id)

    assert result.status == "blocked"
    assert result.error is not None
    assert result.error.category == "max_generation_attempts"
    assert result.config.target_per_task == 6
    assert len(result.generation_attempts) == 4
    assert len(result.attrition) == 4
    assert result.qualifying_counts == {JSON_PARSER: 0, PACKAGE_RESOLVER: 0}
    assert result.qualifying_candidates == []
    assert runner.hidden_workspace_calls == 0
    assert list((tmp_path / "experiments").glob("*.json")) == []


# --------------------------------------------------------------------------
# 15-19. Resume, partial experiments, atomic checkpoint, lock
# --------------------------------------------------------------------------


def test_resume_never_repeats_completed_generation_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _create(tmp_path, target_per_task=3)
    generator = ScriptedGenerator()
    orchestrator, _, _ = _make_runner(tmp_path, artifact, generator=generator)

    real_save = save_campaign

    def crash_after_two_complete(saved: CampaignArtifact, campaigns_dir=None):
        path = real_save(saved, campaigns_dir)
        completed = [attempt for attempt in saved.generation_attempts if attempt.status != "in_progress"]
        experiments_done = [
            record
            for record in saved.qualifying_candidates
            if record.experiment_status == "completed"
        ]
        if (
            saved.status == "running"
            and len(completed) == 2
            and len(experiments_done) == 2
        ):
            raise RuntimeError("simulated crash after two complete cycles")
        return path

    monkeypatch.setattr("experiment.campaign_orchestrator.save_campaign", crash_after_two_complete)
    with pytest.raises(RuntimeError, match="simulated crash"):
        orchestrator.run(artifact.campaign_id)

    paused = load_campaign(artifact.campaign_id, tmp_path / "campaigns")
    first_ids = [attempt.candidate_id for attempt in paused.generation_attempts]
    first_seeds = [attempt.generation_seed for attempt in paused.generation_attempts]
    assert first_seeds == [42, 43]
    assert paused.next_generation_seed == 44

    monkeypatch.setattr("experiment.campaign_orchestrator.save_campaign", real_save)
    resumed = orchestrator.run(artifact.campaign_id)
    assert [attempt.generation_seed for attempt in resumed.generation_attempts[:2]] == [42, 43]
    assert [attempt.candidate_id for attempt in resumed.generation_attempts[:2]] == first_ids
    assert resumed.generation_attempts[2].generation_seed == 44
    assert resumed.status == "completed"


def test_resume_never_reruns_completed_reviewer_experiments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _create(tmp_path, target_per_task=3)
    generator = ScriptedGenerator()
    orchestrator, _, _ = _make_runner(tmp_path, artifact, generator=generator)

    real_save = save_campaign

    def crash_after_two_complete(saved: CampaignArtifact, campaigns_dir=None):
        path = real_save(saved, campaigns_dir)
        experiments_done = [
            record
            for record in saved.qualifying_candidates
            if record.experiment_status == "completed"
        ]
        if saved.status == "running" and len(experiments_done) == 2:
            raise RuntimeError("simulated crash after two experiments")
        return path

    monkeypatch.setattr("experiment.campaign_orchestrator.save_campaign", crash_after_two_complete)
    with pytest.raises(RuntimeError, match="simulated crash"):
        orchestrator.run(artifact.campaign_id)

    paused = load_campaign(artifact.campaign_id, tmp_path / "campaigns")
    original_experiment_ids = [record.experiment_id for record in paused.qualifying_candidates]
    assert len(original_experiment_ids) == 2
    original_files = {path.name for path in (tmp_path / "experiments").glob("*.json")}

    monkeypatch.setattr("experiment.campaign_orchestrator.save_campaign", real_save)
    resumed = orchestrator.run(artifact.campaign_id)
    assert [record.experiment_id for record in resumed.qualifying_candidates[:2]] == original_experiment_ids
    # Original experiment files still exist and were not replaced by a rerun.
    assert original_files <= {path.name for path in (tmp_path / "experiments").glob("*.json")}
    reloaded = [
        ExperimentArtifact.model_validate_json(
            (tmp_path / "experiments" / f"{experiment_id}.json").read_text(encoding="utf-8")
        )
        for experiment_id in original_experiment_ids
        if experiment_id is not None
    ]
    assert all(item.metadata.status == "completed" for item in reloaded)
    assert resumed.status == "completed"


def test_partial_reviewer_experiment_blocks_the_campaign_and_is_not_rerun(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=3)
    counter = {"n": 0}

    def reviewer_factory() -> Reviewer:
        return FailingReviewer(counter, fail_at=10)

    orchestrator, runner, _ = _make_runner(
        tmp_path, artifact, reviewer_factory=reviewer_factory
    )
    result = orchestrator.run(artifact.campaign_id)

    assert result.status == "blocked"
    assert result.error is not None
    assert result.error.category == "partial_experiment"
    assert len(result.qualifying_candidates) == 2
    assert result.qualifying_candidates[0].experiment_status == "completed"
    assert result.qualifying_candidates[1].experiment_status == "partial"
    assert result.qualifying_candidates[1].experiment_id is not None
    assert len(result.generation_attempts) == 2
    assert result.next_generation_seed == 44
    # No third candidate was generated after the partial experiment.
    later = orchestrator.run(artifact.campaign_id)
    assert later.status == "blocked"
    assert len(later.generation_attempts) == 2
    assert later.qualifying_candidates[1].experiment_id == result.qualifying_candidates[1].experiment_id
    assert runner.hidden_workspace_calls == 1


def test_atomic_checkpoint_remains_valid_after_simulated_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _create(tmp_path, target_per_task=2)
    generator = ScriptedGenerator()
    orchestrator, _, _ = _make_runner(tmp_path, artifact, generator=generator)
    real_save = save_campaign

    def crash_mid_checkpoint(saved: CampaignArtifact, campaigns_dir=None):
        path = real_save(saved, campaigns_dir)
        if (
            saved.generation_attempts
            and saved.generation_attempts[-1].status == "qualified"
            and not saved.qualifying_candidates
        ):
            raise RuntimeError("simulated crash after generation checkpoint")
        return path

    monkeypatch.setattr("experiment.campaign_orchestrator.save_campaign", crash_mid_checkpoint)
    with pytest.raises(RuntimeError, match="simulated crash after generation checkpoint"):
        orchestrator.run(artifact.campaign_id)

    leftover = list((tmp_path / "campaigns").glob("*.tmp"))
    assert leftover == []
    paused = load_campaign(artifact.campaign_id, tmp_path / "campaigns")
    CampaignArtifact.model_validate_json(
        (tmp_path / "campaigns" / f"{artifact.campaign_id}.json").read_text(encoding="utf-8")
    )
    assert paused.generation_attempts[0].status == "qualified"
    assert paused.generation_attempts[0].generation_seed == 42
    assert paused.next_generation_seed == 43
    assert paused.qualifying_candidates == []

    monkeypatch.setattr("experiment.campaign_orchestrator.save_campaign", real_save)
    resumed = orchestrator.run(artifact.campaign_id)
    assert resumed.status == "completed"
    assert resumed.qualifying_candidates[0].generation_seed == 42
    assert resumed.qualifying_candidates[0].reviewer_seed == 1000


def test_concurrent_runner_lock_attempt_is_rejected(tmp_path: Path) -> None:
    artifact = _create(tmp_path)
    lock_path = campaign_lock_path(artifact.campaign_id, tmp_path / "campaigns")
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, os, sys, time\n"
                "path = sys.argv[1]\n"
                "fd = os.open(path, os.O_CREAT | os.O_RDWR)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "sys.stdout.write('locked\\n')\n"
                "sys.stdout.flush()\n"
                "time.sleep(30)\n"
            ),
            str(lock_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        line = holder.stdout.readline()
        assert line.strip() == "locked"
        orchestrator, _, _ = _make_runner(tmp_path, artifact)
        with pytest.raises(CampaignLockError, match="already running"):
            orchestrator.run(artifact.campaign_id)
    finally:
        holder.kill()
        holder.wait()


# --------------------------------------------------------------------------
# 20-22. Scheduling blindness, relative paths, existing behavior
# --------------------------------------------------------------------------


def test_hidden_result_and_confidence_do_not_affect_scheduling(tmp_path: Path) -> None:
    source = inspect.getsource(select_next_task)
    assert "confidence" not in source
    assert "hidden" not in source

    artifact = _create(tmp_path, target_per_task=2)
    generator = ScriptedGenerator(hidden_fail_seeds={42, 44})
    orchestrator, runner, _ = _make_runner(tmp_path, artifact, generator=generator)
    result = orchestrator.run(artifact.campaign_id)

    blob = _campaign_json(tmp_path, result.campaign_id)
    assert "hidden_passed" not in blob
    assert "confidence" not in blob
    assert HIDDEN_SENTINEL not in blob
    assert result.generation_attempts[0].task_id == JSON_PARSER
    assert result.generation_attempts[1].task_id == PACKAGE_RESOLVER
    assert result.generation_attempts[2].task_id == JSON_PARSER
    assert result.generation_attempts[3].task_id == PACKAGE_RESOLVER
    assert False in runner.hidden_passed_log
    assert result.status == "completed"
    assert len(result.qualifying_candidates) == 4


def test_campaign_artifact_uses_project_relative_paths_only(tmp_path: Path) -> None:
    artifact = _create(tmp_path, target_per_task=1)
    orchestrator, _, _ = _make_runner(tmp_path, artifact)
    result = orchestrator.run(artifact.campaign_id)
    blob = _campaign_json(tmp_path, result.campaign_id)
    assert str(tmp_path) not in blob
    assert "/Users/" not in blob
    assert os.path.expanduser("~") not in blob
    loaded = json.loads(blob)
    for attempt in loaded["generation_attempts"]:
        path = attempt["generation_artifact_path"]
        assert path is None or not path.startswith("/")
    for record in loaded["qualifying_candidates"]:
        assert not record["generation_artifact_path"].startswith("/")
        assert record["experiment_artifact_path"] is None or not record["experiment_artifact_path"].startswith("/")
    assert loaded["artifact_path"] is None or not loaded["artifact_path"].startswith("/")
    assert loaded["provenance"]["protocol_path"] == "docs/research_protocol.md"

    with pytest.raises(ValidationError):
        CampaignArtifact.model_validate(
            {
                **loaded,
                "artifact_path": str(tmp_path / "campaigns" / f"{result.campaign_id}.json"),
            }
        )


def test_existing_candidate_generation_and_tracked_experiment_behavior_unchanged(
    tmp_path: Path,
) -> None:
    assert CandidateGenerationOrchestrator.run.__qualname__ == "CandidateGenerationOrchestrator.run"
    assert ExperimentOrchestrator.run.__qualname__ == "ExperimentOrchestrator.run"

    from experiment.orchestrator import ExperimentOrchestrator as LiveOrchestrator
    from experiment.reviewer import MockReviewer as LiveMock

    call_log: list[str] = []

    class _TrackedRunner:
        def run_visible(self, task):
            call_log.append("visible")
            return _suite("visible", passed=True)

        def run_hidden(self, task):
            call_log.append("hidden")
            return _suite("hidden", passed=True)

    tracked = LiveOrchestrator(
        reviewer_factory=LiveMock,
        output_dir=tmp_path / "tracked-experiments",
        runner=_TrackedRunner(),  # type: ignore[arg-type]
    )
    tracked_artifact = tracked.run(task_id="expression_evaluator", repetitions=1, random_seed=7)
    assert tracked_artifact.metadata.candidate_id is None
    assert tracked_artifact.metadata.status == "completed"
    assert call_log == ["visible", "hidden"] or call_log[0] == "visible" and call_log[-1] == "hidden"
    assert len(tracked_artifact.observations) == 3


# --------------------------------------------------------------------------
# CLI: create/status/run without paid calls
# --------------------------------------------------------------------------


def test_cli_campaign_create_prints_immutable_config_without_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("CLI campaign-create must not initialize providers")

    import experiment.openai_candidate_generator as openai_candidate_module
    import experiment.openai_reviewer as openai_reviewer_module

    monkeypatch.setattr(openai_reviewer_module.OpenAIReviewer, "__init__", boom)
    monkeypatch.setattr(openai_candidate_module.OpenAICandidateGenerator, "__init__", boom)
    monkeypatch.setattr("experiment.campaign_orchestrator.inspect_repository", _clean_inspect)
    monkeypatch.setattr("experiment.campaign_store.default_campaigns_dir", lambda: tmp_path / "campaigns")
    monkeypatch.setattr("experiment.cli.default_campaigns_dir", lambda: tmp_path / "campaigns")

    exit_code = cli.main(
        [
            "campaign-create",
            "--generator-provider",
            "openai",
            "--generator-model",
            "gpt-5.6-luna",
            "--reviewer-provider",
            "openai",
            "--reviewer-model",
            "gpt-5.6-luna",
            "--target-per-task",
            "6",
            "--max-candidate-attempts",
            "3",
            "--generation-seed-start",
            "42",
            "--reviewer-seed-start",
            "1000",
            "--repetitions",
            "3",
            "--cutoff",
            CUTOFF,
            "--max-generation-attempts",
            "36",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "created"
    assert payload["next_scheduled_task"] == JSON_PARSER
    assert payload["config"]["generator_provider"] == "openai"
    assert payload["config"]["generator_model"] == "gpt-5.6-luna"
    assert payload["config"]["reviewer_provider"] == "openai"
    assert payload["config"]["reviewer_model"] == "gpt-5.6-luna"
    assert payload["config"]["target_per_task"] == 6
    assert payload["config"]["max_candidate_attempts"] == 3
    assert payload["config"]["generation_seed_start"] == 42
    assert payload["config"]["reviewer_seed_start"] == 1000
    assert payload["config"]["repetitions"] == 3
    assert payload["config"]["max_generation_attempts"] == 36
    assert payload["config"]["cutoff"]["timezone"] == "America/Los_Angeles"
    assert payload["provenance"]["research_protocol_sha256"] == hash_protocol_file(PROTOCOL_PATH)
    assert payload["artifact_path"] is None or not payload["artifact_path"].startswith("/")


def test_cli_campaign_status_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = _create(tmp_path, target_per_task=1)
    monkeypatch.setattr("experiment.cli.default_campaigns_dir", lambda: tmp_path / "campaigns")
    monkeypatch.setattr("experiment.campaign_store.default_campaigns_dir", lambda: tmp_path / "campaigns")

    exit_code = cli.main(["campaign-status", "--campaign-id", str(artifact.campaign_id)])
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "created"
    assert payload["qualifying_counts"][JSON_PARSER] == 0
    assert payload["total_generation_attempts"] == 0
    assert payload["attrition_count"] == 0
    assert payload["completed_experiments"] == 0
    assert payload["next_generation_seed"] == 42
    assert payload["next_reviewer_seed"] == 1000
    assert payload["next_scheduled_task"] == JSON_PARSER
    assert payload["cutoff"]["original"] == CUTOFF


def test_cli_campaign_run_respects_cutoff_with_mock_providers_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = _create(
        tmp_path,
        cutoff="2020-01-01T00:00:00-08:00",
        generator_provider="mock",
        generator_model="mock-deterministic-v1",
        reviewer_provider="mock",
        reviewer_model="mock-deterministic-v1",
    )
    monkeypatch.setattr("experiment.cli.default_campaigns_dir", lambda: tmp_path / "campaigns")
    monkeypatch.setattr("experiment.campaign_store.default_campaigns_dir", lambda: tmp_path / "campaigns")

    exit_code = cli.main(["campaign-run", "--campaign-id", str(artifact.campaign_id)])
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "stopped_cutoff"
    assert payload["total_generation_attempts"] == 0


def test_absolute_user_paths_are_rejected_on_generation_attempt_records() -> None:
    from experiment.campaign_models import GenerationAttemptRecord

    with pytest.raises(ValidationError):
        GenerationAttemptRecord(
            attempt_index=0,
            task_id="json_parser",
            generation_seed=42,
            status="qualified",
            generation_artifact_path="/Users/varunmohanraj/secret/candidate",
            started_at=datetime.now(timezone.utc),
        )
