"""Tests for Milestone 11: campaign-aware analysis pipeline.

Every campaign, candidate, experiment, and adjudications artifact here is
synthetic and lives under ``tmp_path``. No test loads the real campaign
``8449581e-4097-4865-bfae-5cd8e9aef83f``, the real ``research/adjudications.json``,
or any real candidate/experiment artifact, and no test makes a network/API
call -- generators and reviewers are fakes, and the pytest runner is a fake
that never shells out.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import textwrap
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pytest

from experiment import cli
from experiment.analysis_models import CandidateAnalysis
from experiment.campaign_analyzer import (
    CampaignAnalysisError,
    CampaignAnalyzer,
    CampaignValidationError,
    PILOT_CANDIDATE_ID,
    PILOT_EXPERIMENT_ID,
)
from experiment.campaign_models import CampaignArtifact
from experiment.campaign_orchestrator import CampaignOrchestrator, create_campaign
from experiment.campaign_store import load_campaign, save_campaign
from experiment.candidate_generator import CandidateGenerator
from experiment.candidate_models import CandidateGenerationRequest, CandidateGeneratorOutput
from experiment.conditions import Condition
from experiment.experiment_models import ExperimentArtifact
from experiment.models import ReviewerAssessment
from experiment.models import TestSuiteResult as PytestSuiteResult
from experiment.reviewer import Reviewer

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO_ROOT / "docs" / "research_protocol.md"
HIDDEN_SENTINEL = "ANALYSIS_SYNTHETIC_HIDDEN_SENTINEL_f37b21"
VISIBLE_SENTINEL = "ANALYSIS_SYNTHETIC_VISIBLE_SENTINEL_a48c19"
JSON_PARSER = "json_parser"
PACKAGE_RESOLVER = "package_resolver"
PASSING_SOURCE = "VALUE = 1  # ANALYSIS_CANDIDATE_PASS_MARKER\n"
FAILING_SOURCE = "VALUE = 0  # ANALYSIS_CANDIDATE_FAIL_MARKER\n"
HIDDEN_FAIL_SOURCE = "VALUE = 1  # ANALYSIS_CANDIDATE_PASS_MARKER ANALYSIS_CANDIDATE_HIDDEN_FAIL_MARKER\n"
CUTOFF = "2026-09-21T12:00:00-07:00"


# --------------------------------------------------------------------------
# Synthetic tasks, fakes, and campaign-building helpers
# --------------------------------------------------------------------------


def _write_synthetic_task(tasks_root: Path, task_id: str, filename: str) -> None:
    task_dir = tasks_root / task_id
    (task_dir / "visible_tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "hidden_tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "starter").mkdir(exist_ok=True)
    (task_dir / "specification.md").write_text(
        f"# Synthetic {task_id} specification\nNo Infinity, NaN, or hex.\n", encoding="utf-8"
    )
    (task_dir / filename).write_text(PASSING_SOURCE, encoding="utf-8")
    (task_dir / "starter" / filename).write_text(FAILING_SOURCE, encoding="utf-8")
    (task_dir / "visible_tests" / "test_visible.py").write_text(
        textwrap.dedent(
            f"""
            # {VISIBLE_SENTINEL}
            def test_visible_{task_id}():
                assert True
            """
        ),
        encoding="utf-8",
    )
    (task_dir / "hidden_tests" / "test_hidden.py").write_text(
        textwrap.dedent(
            f"""
            # {HIDDEN_SENTINEL}
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


def _write_tasks(tasks_root: Path) -> None:
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
    return "\n".join(
        path.read_text(encoding="utf-8") for path in Path(workspace).rglob("*") if path.is_file()
    )


class FakeRunner:
    """Fake pytest runner for isolated workspaces only; never shells out."""

    def run_visible(self, task) -> PytestSuiteResult:
        raise AssertionError("must not run visible tests against a tracked task")

    def run_hidden(self, task) -> PytestSuiteResult:
        raise AssertionError("must not run hidden tests against a tracked task")

    def run_visible_workspace(self, tests_dir: Path, timeout_seconds: float, *, workspace: Path):
        blob = _workspace_blob(workspace)
        return _suite("visible", passed="ANALYSIS_CANDIDATE_FAIL_MARKER" not in blob)

    def run_hidden_workspace(self, tests_dir: Path, timeout_seconds: float, *, workspace: Path):
        blob = _workspace_blob(workspace)
        return _suite("hidden", passed="ANALYSIS_CANDIDATE_HIDDEN_FAIL_MARKER" not in blob)


class ScriptedGenerator:
    provider = "mock"
    model = "mock-deterministic-v1"

    def __init__(self, hidden_fail_seeds: frozenset[int] = frozenset()) -> None:
        self.hidden_fail_seeds = set(hidden_fail_seeds)

    def generate(self, request: CandidateGenerationRequest) -> CandidateGeneratorOutput:
        source = HIDDEN_FAIL_SOURCE if request.random_seed in self.hidden_fail_seeds else PASSING_SOURCE
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


def _clean_inspect(_repo: Path) -> tuple[Optional[str], bool]:
    return "deadbeefcafebabe000000000000000000000000", False


@dataclass(frozen=True)
class Dirs:
    tasks_root: Path
    candidates_dir: Path
    experiments_dir: Path
    campaigns_dir: Path
    adjudications_path: Path


def _dirs(tmp_path: Path) -> Dirs:
    return Dirs(
        tasks_root=tmp_path / "tasks",
        candidates_dir=tmp_path / "candidates",
        experiments_dir=tmp_path / "experiments",
        campaigns_dir=tmp_path / "campaigns",
        adjudications_path=tmp_path / "adjudications.json",
    )


def _write_adjudications(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps({"schema_version": 1, "adjudications": entries}, indent=2), encoding="utf-8"
    )


def _build_campaign(
    tmp_path: Path,
    *,
    target_per_task: int = 1,
    hidden_fail_seeds: frozenset[int] = frozenset(),
    protocol_path: Optional[Path] = None,
    inspect_repo: Callable[[Path], tuple[Optional[str], bool]] = _clean_inspect,
) -> tuple[CampaignArtifact, Dirs]:
    dirs = _dirs(tmp_path)
    _write_tasks(dirs.tasks_root)
    _write_adjudications(dirs.adjudications_path, [])

    artifact = create_campaign(
        generator_provider="mock",
        generator_model="mock-deterministic-v1",
        reviewer_provider="mock",
        reviewer_model="mock-deterministic-v1",
        target_per_task=target_per_task,
        cutoff=CUTOFF,
        campaigns_dir=dirs.campaigns_dir,
        protocol_path=protocol_path if protocol_path is not None else PROTOCOL_PATH,
        inspect_repo=inspect_repo,
    )

    generator = ScriptedGenerator(hidden_fail_seeds=hidden_fail_seeds)
    counter = {"n": 0}

    def reviewer_factory() -> Reviewer:
        return VaryingConfidenceReviewer(counter)

    orchestrator = CampaignOrchestrator(
        generator_factory=lambda: generator,
        reviewer_factory=reviewer_factory,
        tasks_root=dirs.tasks_root,
        candidates_dir=dirs.candidates_dir,
        experiments_dir=dirs.experiments_dir,
        campaigns_dir=dirs.campaigns_dir,
        runner=FakeRunner(),  # type: ignore[arg-type]
        now_fn=lambda: datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
    )
    result = orchestrator.run(artifact.campaign_id)
    assert result.status == "completed"
    return result, dirs


def _analyzer(dirs: Dirs) -> CampaignAnalyzer:
    return CampaignAnalyzer(
        campaigns_dir=dirs.campaigns_dir,
        candidates_dir=dirs.candidates_dir,
        experiments_dir=dirs.experiments_dir,
        tasks_root=dirs.tasks_root,
        protocol_path=PROTOCOL_PATH,
        adjudications_path=dirs.adjudications_path,
        inspect_repo=_clean_inspect,
    )


def _analyzer_with(
    dirs: Dirs,
    *,
    protocol_path: Path,
    repo: Optional[Path] = None,
    inspect_repo: Callable[[Path], tuple[Optional[str], bool]] = _clean_inspect,
) -> CampaignAnalyzer:
    """Build a :class:`CampaignAnalyzer` with an explicit ``protocol_path``/``repo``.

    Used only by the protocol-provenance tests below, which need a
    synthetic ``repo`` (never the real VeriGate repository) so that
    ``git show <commit>:docs/research_protocol.md`` resolves against a
    throwaway git history under ``tmp_path``.
    """

    return CampaignAnalyzer(
        campaigns_dir=dirs.campaigns_dir,
        candidates_dir=dirs.candidates_dir,
        experiments_dir=dirs.experiments_dir,
        tasks_root=dirs.tasks_root,
        protocol_path=protocol_path,
        adjudications_path=dirs.adjudications_path,
        repo=repo,
        inspect_repo=inspect_repo,
    )


def _run_git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=True,
        capture_output=True,
        timeout=15,
    )


def _init_synthetic_protocol_repo(repo_dir: Path, protocol_bytes: bytes) -> str:
    """Create a throwaway git repo under ``repo_dir`` with a committed protocol file.

    This is a synthetic repository/file used only for testing the
    append-only provenance check -- it is never the real VeriGate
    repository, and no test in this module ever points ``repo=`` at the
    real repository root. Returns the new commit's full hash.
    """

    (repo_dir / "docs").mkdir(parents=True, exist_ok=True)
    (repo_dir / "docs" / "research_protocol.md").write_bytes(protocol_bytes)

    _run_git(repo_dir, "init", "-q")
    _run_git(repo_dir, "config", "user.email", "verigate-test@example.invalid")
    _run_git(repo_dir, "config", "user.name", "VeriGate Test Fixture")
    _run_git(repo_dir, "add", "-A")
    _run_git(repo_dir, "commit", "-q", "-m", "synthetic protocol snapshot")

    commit = _run_git(repo_dir, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    return commit


def _experiment_path(dirs: Dirs, experiment_id: uuid.UUID) -> Path:
    return dirs.experiments_dir / f"{experiment_id}.json"


def _load_experiment_json(dirs: Dirs, experiment_id: uuid.UUID) -> dict:
    return json.loads(_experiment_path(dirs, experiment_id).read_text(encoding="utf-8"))


def _save_experiment_json(dirs: Dirs, experiment_id: uuid.UUID, payload: dict) -> None:
    _experiment_path(dirs, experiment_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Validate our mutation still produces syntactically-valid JSON matching the model
    # shape, so tests exercise the analyzer's own semantic checks, not a JSON parse error.
    ExperimentArtifact.model_validate(payload)


# --------------------------------------------------------------------------
# 1. Happy path, calculations, candidate-level arithmetic
# --------------------------------------------------------------------------


def test_analyze_succeeds_with_expected_totals_and_categories(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    report = _analyzer(dirs).analyze(result.campaign_id)

    assert report.campaign_status == "completed"
    assert report.candidate_count == 2
    assert report.totals.reviewer_observation_count == 2 * 9
    assert report.eligible_count == 2  # both benchmark_pass=True -> auto valid_pass
    assert report.excluded_count == 0
    assert report.pending_count == 0
    assert all(c.adjudication_source == "auto_valid_pass" for c in report.candidates)
    assert all(c.adjudication_status == "valid_pass" for c in report.candidates)
    assert len(report.validation.checks) > 0
    assert len(report.adjudication_queue) == 0


def test_candidate_arithmetic_matches_independent_recomputation(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    report = _analyzer(dirs).analyze(result.campaign_id)

    for record in result.qualifying_candidates:
        experiment = ExperimentArtifact.model_validate_json(
            _experiment_path(dirs, record.experiment_id).read_text(encoding="utf-8")
        )
        analyzed = next(c for c in report.candidates if c.candidate_id == record.candidate_id)

        for condition in Condition:
            obs = sorted(
                (o for o in experiment.observations if o.condition == condition),
                key=lambda o: o.repetition_index,
            )
            expected_raw = [o.result.assessment.confidence for o in obs]
            expected_mean = sum(expected_raw) / len(expected_raw)
            expected_predicted = sum(1 for o in obs if o.result.assessment.predicted_pass)

            stats = analyzed.conditions[condition.value]
            assert stats.raw_confidence == expected_raw
            assert stats.mean_confidence == pytest.approx(expected_mean)
            assert stats.predicted_pass_count == expected_predicted
            assert stats.observation_count == 3

        assert analyzed.mean_confidence_a == pytest.approx(analyzed.conditions["A_NO_RESULT"].mean_confidence)
        assert analyzed.b_minus_a == pytest.approx(analyzed.mean_confidence_b - analyzed.mean_confidence_a)
        assert analyzed.c_minus_b == pytest.approx(analyzed.mean_confidence_c - analyzed.mean_confidence_b)
        assert analyzed.visible_passed_count == experiment.ground_truth.visible_passed_count
        assert analyzed.hidden_passed_count == experiment.ground_truth.hidden_passed_count
        assert analyzed.benchmark_pass == experiment.ground_truth.hidden_passed


def test_cohort_overall_mean_is_average_of_candidate_means_not_pooled(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=2)
    report = _analyzer(dirs).analyze(result.campaign_id)

    overall = next(s for s in report.full_cohort_summaries if s.task_id is None)
    for cond_summary in overall.conditions:
        per_candidate_means = [
            c.conditions[cond_summary.condition].mean_confidence for c in report.candidates
        ]
        assert cond_summary.mean_confidence == pytest.approx(
            sum(per_candidate_means) / len(per_candidate_means)
        )
    per_candidate_b_minus_a = [c.b_minus_a for c in report.candidates]
    assert overall.mean_b_minus_a == pytest.approx(
        sum(per_candidate_b_minus_a) / len(per_candidate_b_minus_a)
    )


def test_raw_confidence_is_ordered_by_repetition_index(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    report = _analyzer(dirs).analyze(result.campaign_id)
    for candidate in report.candidates:
        for condition in Condition:
            assert len(candidate.conditions[condition.value].raw_confidence) == 3


# --------------------------------------------------------------------------
# 2. Task stratification and adjudication categories
# --------------------------------------------------------------------------


def _build_mixed_adjudication_campaign(tmp_path: Path) -> tuple[CampaignArtifact, Dirs]:
    """4 candidates (2 json_parser, 2 package_resolver): pass / pending / valid_failure / benchmark_mismatch."""

    result, dirs = _build_campaign(tmp_path, target_per_task=2, hidden_fail_seeds=frozenset({43, 44, 45}))
    ordered = sorted(result.qualifying_candidates, key=lambda r: r.qualification_index)
    assert [r.task_id for r in ordered] == [JSON_PARSER, PACKAGE_RESOLVER, JSON_PARSER, PACKAGE_RESOLVER]
    pass_candidate, pending_candidate, valid_failure_candidate, mismatch_candidate = ordered

    entries = [
        {
            "task_id": valid_failure_candidate.task_id,
            "candidate_id": str(valid_failure_candidate.candidate_id),
            "candidate_source_sha256": valid_failure_candidate.source_sha256,
            "benchmark_pass": False,
            "specification_correct": False,
            "adjudication": "valid_failure",
            "excluded_from_primary_analysis": False,
            "exclusion_reason": None,
        },
        {
            "task_id": mismatch_candidate.task_id,
            "candidate_id": str(mismatch_candidate.candidate_id),
            "candidate_source_sha256": mismatch_candidate.source_sha256,
            "benchmark_pass": False,
            "specification_correct": True,
            "adjudication": "benchmark_mismatch",
            "excluded_from_primary_analysis": True,
            "exclusion_reason": "synthetic: contradicts specification.md per test fixture",
        },
    ]
    _write_adjudications(dirs.adjudications_path, entries)
    return result, dirs


def test_mixed_adjudication_categories_are_classified_correctly(tmp_path: Path) -> None:
    result, dirs = _build_mixed_adjudication_campaign(tmp_path)
    report = _analyzer(dirs).analyze(result.campaign_id)

    assert report.candidate_count == 4
    assert report.eligible_count == 2
    assert report.excluded_count == 1
    assert report.pending_count == 1

    by_id = {c.candidate_id: c for c in report.candidates}
    ordered = sorted(result.qualifying_candidates, key=lambda r: r.qualification_index)
    pass_c, pending_c, valid_failure_c, mismatch_c = ordered

    assert by_id[pass_c.candidate_id].adjudication_status == "valid_pass"
    assert by_id[pass_c.candidate_id].adjudication_source == "auto_valid_pass"
    assert by_id[pass_c.candidate_id].eligible_for_primary_analysis is True

    assert by_id[pending_c.candidate_id].adjudication_status == "pending"
    assert by_id[pending_c.candidate_id].eligible_for_primary_analysis is False
    assert by_id[pending_c.candidate_id].exclusion_reason is not None

    assert by_id[valid_failure_c.candidate_id].adjudication_status == "valid_failure"
    assert by_id[valid_failure_c.candidate_id].adjudication_source == "recorded_adjudication"
    assert by_id[valid_failure_c.candidate_id].eligible_for_primary_analysis is True

    assert by_id[mismatch_c.candidate_id].adjudication_status == "benchmark_mismatch"
    assert by_id[mismatch_c.candidate_id].eligible_for_primary_analysis is False
    assert "contradicts specification" in (by_id[mismatch_c.candidate_id].exclusion_reason or "")


def test_per_task_primary_eligible_summaries_differ(tmp_path: Path) -> None:
    result, dirs = _build_mixed_adjudication_campaign(tmp_path)
    report = _analyzer(dirs).analyze(result.campaign_id)

    full_json = next(s for s in report.full_cohort_summaries if s.task_id == JSON_PARSER)
    full_pkg = next(s for s in report.full_cohort_summaries if s.task_id == PACKAGE_RESOLVER)
    assert full_json.candidate_count == 2
    assert full_pkg.candidate_count == 2

    eligible_json = next(s for s in report.primary_eligible_summaries if s.task_id == JSON_PARSER)
    eligible_pkg = next(s for s in report.primary_eligible_summaries if s.task_id == PACKAGE_RESOLVER)
    # json_parser: pass (eligible) + valid_failure (eligible) -> both eligible.
    assert eligible_json.candidate_count == 2
    # package_resolver: pending (excluded) + benchmark_mismatch (excluded) -> none eligible.
    assert eligible_pkg.candidate_count == 0
    for cond in eligible_pkg.conditions:
        assert cond.mean_confidence is None
        assert cond.predicted_pass_rate is None
    assert eligible_pkg.mean_b_minus_a is None
    assert eligible_pkg.mean_c_minus_b is None


def test_adjudication_queue_contains_only_pending_with_minimum_information(tmp_path: Path) -> None:
    result, dirs = _build_mixed_adjudication_campaign(tmp_path)
    report = _analyzer(dirs).analyze(result.campaign_id)

    assert len(report.adjudication_queue) == 1
    item = report.adjudication_queue[0]
    ordered = sorted(result.qualifying_candidates, key=lambda r: r.qualification_index)
    pending_c = ordered[1]
    assert item.candidate_id == pending_c.candidate_id
    assert item.task_id == PACKAGE_RESOLVER
    assert item.failing_test_identifiers is None
    assert "never" in item.failing_test_identifiers_note or "Not available" in item.failing_test_identifiers_note
    assert len(item.specification_reference_notes) >= 1
    assert HIDDEN_SENTINEL not in json.dumps(item.model_dump(mode="json"))


def test_recorded_adjudication_benchmark_pass_mismatch_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1, hidden_fail_seeds=frozenset({43}))
    failing = next(r for r in result.qualifying_candidates if r.task_id == PACKAGE_RESOLVER)
    _write_adjudications(
        dirs.adjudications_path,
        [
            {
                "task_id": failing.task_id,
                "candidate_id": str(failing.candidate_id),
                "candidate_source_sha256": failing.source_sha256,
                "benchmark_pass": True,  # wrong: this candidate's real hidden result is False
                "adjudication": "valid_pass",
                "excluded_from_primary_analysis": False,
            }
        ],
    )
    with pytest.raises(CampaignValidationError, match="benchmark_pass"):
        _analyzer(dirs).analyze(result.campaign_id)


# --------------------------------------------------------------------------
# 3. Validation failures
# --------------------------------------------------------------------------


def test_campaign_not_completed_is_rejected(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    campaign = load_campaign(result.campaign_id, dirs.campaigns_dir)
    save_campaign(campaign.model_copy(update={"status": "running"}), dirs.campaigns_dir)

    with pytest.raises(CampaignValidationError, match="completed"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_missing_candidate_artifact_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    victim = result.qualifying_candidates[0]
    shutil.rmtree(dirs.candidates_dir / victim.task_id / str(victim.candidate_id))

    with pytest.raises(CampaignValidationError, match="No candidate artifact found|verification"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_missing_experiment_artifact_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    victim = result.qualifying_candidates[0]
    _experiment_path(dirs, victim.experiment_id).unlink()

    with pytest.raises(CampaignValidationError, match="missing experiment artifact"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_candidate_source_hash_mismatch_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    victim = result.qualifying_candidates[0]
    source_path = dirs.candidates_dir / victim.task_id / str(victim.candidate_id) / (
        "json_parser.py" if victim.task_id == JSON_PARSER else "resolver.py"
    )
    source_path.write_text(source_path.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

    with pytest.raises(CampaignValidationError, match="hash|tampered|verification"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_duplicate_candidate_id_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    campaign = load_campaign(result.campaign_id, dirs.campaigns_dir)
    qualifying = list(campaign.qualifying_candidates)
    qualifying[1] = qualifying[1].model_copy(update={"candidate_id": qualifying[0].candidate_id})
    save_campaign(campaign.model_copy(update={"qualifying_candidates": qualifying}), dirs.campaigns_dir)

    with pytest.raises(CampaignValidationError, match="duplicate candidate_id"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_duplicate_experiment_id_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    campaign = load_campaign(result.campaign_id, dirs.campaigns_dir)
    qualifying = list(campaign.qualifying_candidates)
    qualifying[1] = qualifying[1].model_copy(update={"experiment_id": qualifying[0].experiment_id})
    save_campaign(campaign.model_copy(update={"qualifying_candidates": qualifying}), dirs.campaigns_dir)

    with pytest.raises(CampaignValidationError, match="duplicate.*experiment_id"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_duplicate_reviewer_seed_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    campaign = load_campaign(result.campaign_id, dirs.campaigns_dir)
    qualifying = list(campaign.qualifying_candidates)
    qualifying[1] = qualifying[1].model_copy(update={"reviewer_seed": qualifying[0].reviewer_seed})
    save_campaign(campaign.model_copy(update={"qualifying_candidates": qualifying}), dirs.campaigns_dir)

    with pytest.raises(CampaignValidationError, match="duplicate reviewer_seed"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_duplicate_generation_seed_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    campaign = load_campaign(result.campaign_id, dirs.campaigns_dir)
    attempts = list(campaign.generation_attempts)
    attempts[1] = attempts[1].model_copy(update={"generation_seed": attempts[0].generation_seed})
    save_campaign(campaign.model_copy(update={"generation_attempts": attempts}), dirs.campaigns_dir)

    with pytest.raises(CampaignValidationError, match="duplicate generation_seed"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_incorrect_task_quota_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=2)
    campaign = load_campaign(result.campaign_id, dirs.campaigns_dir)
    qualifying = [r for r in campaign.qualifying_candidates if r.task_id != JSON_PARSER or r.qualification_index != 0]
    counts = {JSON_PARSER: 0, PACKAGE_RESOLVER: 0}
    for r in qualifying:
        counts[r.task_id] += 1
    save_campaign(
        campaign.model_copy(update={"qualifying_candidates": qualifying, "qualifying_counts": counts}),
        dirs.campaigns_dir,
    )

    with pytest.raises(CampaignValidationError, match="quota"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_condition_count_other_than_three_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    victim = result.qualifying_candidates[0]
    payload = _load_experiment_json(dirs, victim.experiment_id)
    observations = payload["observations"]
    first_a_index = next(i for i, o in enumerate(observations) if o["condition"] == "A_NO_RESULT")
    del observations[first_a_index]
    _save_experiment_json(dirs, victim.experiment_id, payload)

    with pytest.raises(CampaignValidationError, match="expected 9 observations"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_mismatched_task_id_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    victim = result.qualifying_candidates[0]
    other_task = PACKAGE_RESOLVER if victim.task_id == JSON_PARSER else JSON_PARSER
    payload = _load_experiment_json(dirs, victim.experiment_id)
    payload["metadata"]["task_id"] = other_task
    _save_experiment_json(dirs, victim.experiment_id, payload)

    with pytest.raises(CampaignValidationError, match="task_id"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_observations_associated_with_wrong_candidate_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    victim, other = result.qualifying_candidates[0], result.qualifying_candidates[1]
    payload = _load_experiment_json(dirs, victim.experiment_id)
    payload["metadata"]["candidate_id"] = str(other.candidate_id)
    _save_experiment_json(dirs, victim.experiment_id, payload)

    with pytest.raises(CampaignValidationError, match="different candidate_id|candidate_id"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_mismatched_model_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    victim = result.qualifying_candidates[0]
    payload = _load_experiment_json(dirs, victim.experiment_id)
    payload["metadata"]["model"] = "some-other-model"
    _save_experiment_json(dirs, victim.experiment_id, payload)

    with pytest.raises(CampaignValidationError, match="provider/model"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_mismatched_prompt_version_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    victim = result.qualifying_candidates[0]
    payload = _load_experiment_json(dirs, victim.experiment_id)
    payload["metadata"]["prompt_version"] = "v999-different"
    _save_experiment_json(dirs, victim.experiment_id, payload)

    with pytest.raises(CampaignValidationError, match="prompt_version"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_mismatched_specification_hash_within_task_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=2)
    json_parser_records = [r for r in result.qualifying_candidates if r.task_id == JSON_PARSER]
    assert len(json_parser_records) == 2
    victim = json_parser_records[0]
    payload = _load_experiment_json(dirs, victim.experiment_id)
    payload["metadata"]["specification_sha256"] = "0" * 64
    _save_experiment_json(dirs, victim.experiment_id, payload)

    with pytest.raises(CampaignValidationError, match="specification_sha256"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_hidden_evaluation_before_reviews_ordering_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    victim = result.qualifying_candidates[0]
    payload = _load_experiment_json(dirs, victim.experiment_id)
    payload["metadata"]["completed_at"] = payload["metadata"]["started_at"]
    _save_experiment_json(dirs, victim.experiment_id, payload)

    with pytest.raises(CampaignValidationError, match="hidden evaluation"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_pilot_candidate_inclusion_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    campaign = load_campaign(result.campaign_id, dirs.campaigns_dir)
    qualifying = list(campaign.qualifying_candidates)
    qualifying[0] = qualifying[0].model_copy(update={"candidate_id": PILOT_CANDIDATE_ID})
    save_campaign(campaign.model_copy(update={"qualifying_candidates": qualifying}), dirs.campaigns_dir)

    with pytest.raises(CampaignValidationError, match="pilot candidate"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_pilot_experiment_inclusion_raises(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    campaign = load_campaign(result.campaign_id, dirs.campaigns_dir)
    qualifying = list(campaign.qualifying_candidates)
    qualifying[0] = qualifying[0].model_copy(update={"experiment_id": PILOT_EXPERIMENT_ID})
    save_campaign(campaign.model_copy(update={"qualifying_candidates": qualifying}), dirs.campaigns_dir)

    with pytest.raises(CampaignValidationError, match="pilot experiment"):
        _analyzer(dirs).analyze(result.campaign_id)


def test_protocol_hash_mismatch_raises(tmp_path: Path) -> None:
    protocol_copy = tmp_path / "protocol.md"
    protocol_copy.write_text(PROTOCOL_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    dirs = _dirs(tmp_path)
    _write_tasks(dirs.tasks_root)
    _write_adjudications(dirs.adjudications_path, [])
    artifact = create_campaign(
        generator_provider="mock",
        generator_model="mock-deterministic-v1",
        reviewer_provider="mock",
        reviewer_model="mock-deterministic-v1",
        target_per_task=1,
        cutoff=CUTOFF,
        campaigns_dir=dirs.campaigns_dir,
        protocol_path=protocol_copy,
        inspect_repo=_clean_inspect,
    )
    generator = ScriptedGenerator()
    counter = {"n": 0}
    orchestrator = CampaignOrchestrator(
        generator_factory=lambda: generator,
        reviewer_factory=lambda: VaryingConfidenceReviewer(counter),
        tasks_root=dirs.tasks_root,
        candidates_dir=dirs.candidates_dir,
        experiments_dir=dirs.experiments_dir,
        campaigns_dir=dirs.campaigns_dir,
        runner=FakeRunner(),  # type: ignore[arg-type]
        now_fn=lambda: datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
    )
    result = orchestrator.run(artifact.campaign_id)
    assert result.status == "completed"

    # Protocol document changes after campaign creation.
    protocol_copy.write_text(protocol_copy.read_text(encoding="utf-8") + "\nAmended.\n", encoding="utf-8")

    analyzer = CampaignAnalyzer(
        campaigns_dir=dirs.campaigns_dir,
        candidates_dir=dirs.candidates_dir,
        experiments_dir=dirs.experiments_dir,
        tasks_root=dirs.tasks_root,
        protocol_path=protocol_copy,
        adjudications_path=dirs.adjudications_path,
        inspect_repo=_clean_inspect,
    )
    with pytest.raises(CampaignValidationError, match="protocol"):
        analyzer.analyze(result.campaign_id)


# --------------------------------------------------------------------------
# 3b. Append-only protocol-amendment provenance (synthetic git repos only)
#
# Every test below builds its own throwaway git repository under
# ``tmp_path`` via ``_init_synthetic_protocol_repo`` and points
# ``CampaignAnalyzer(repo=...)`` at it. None of them ever touch, read, or
# reference the real VeriGate repository or the real campaign
# ``8449581e-4097-4865-bfae-5cd8e9aef83f``.
# --------------------------------------------------------------------------

SYNTHETIC_PROTOCOL_V1 = (
    b"# Synthetic protocol\n\n"
    b"## Estimand\n\nSome preregistered estimand text that must never change.\n\n"
    b"## Conditions\n\nA/B/C, 3 repetitions each.\n\n"
    b"## Amendments\n\n"
    b"None yet. Any future change will be appended below this line.\n"
)


def _build_campaign_with_synthetic_protocol(
    tmp_path: Path,
    protocol_bytes: bytes = SYNTHETIC_PROTOCOL_V1,
    *,
    target_per_task: int = 1,
) -> tuple[CampaignArtifact, Dirs, Path, str]:
    """Build a completed campaign whose provenance points at a synthetic git repo.

    Returns ``(result, dirs, repo_dir, commit)``. The campaign's recorded
    ``research_protocol_sha256`` is ``sha256(protocol_bytes)`` and its
    ``repository_commit`` is the real commit hash of the synthetic repo
    (verifiable via ``git show``), unlike the fixed placeholder hash
    ``_clean_inspect`` returns elsewhere in this file.
    """

    repo_dir = tmp_path / "synthetic_repo"
    commit = _init_synthetic_protocol_repo(repo_dir, protocol_bytes)
    protocol_path = repo_dir / "docs" / "research_protocol.md"

    result, dirs = _build_campaign(
        tmp_path,
        target_per_task=target_per_task,
        protocol_path=protocol_path,
        inspect_repo=lambda _repo: (commit, False),
    )
    return result, dirs, repo_dir, commit


def test_protocol_provenance_unchanged_document_is_accepted(tmp_path: Path) -> None:
    result, dirs, repo_dir, _commit = _build_campaign_with_synthetic_protocol(tmp_path)
    protocol_path = repo_dir / "docs" / "research_protocol.md"

    report = _analyzer_with(dirs, protocol_path=protocol_path, repo=repo_dir).analyze(
        result.campaign_id
    )

    expected_hash = hashlib.sha256(SYNTHETIC_PROTOCOL_V1).hexdigest()
    assert report.provenance.campaign_protocol_sha256 == expected_hash
    assert report.provenance.analyzed_protocol_sha256 == expected_hash
    assert report.provenance.protocol_changed_after_collection is False
    assert report.provenance.protocol_change_verified_append_only is False


def test_protocol_provenance_exact_pure_append_is_accepted(tmp_path: Path) -> None:
    result, dirs, repo_dir, _commit = _build_campaign_with_synthetic_protocol(tmp_path)

    appended_bytes = SYNTHETIC_PROTOCOL_V1 + b"\n### Amendment 1 -- synthetic, post-data.\n"
    amended_path = tmp_path / "amended_protocol.md"
    amended_path.write_bytes(appended_bytes)

    report = _analyzer_with(dirs, protocol_path=amended_path, repo=repo_dir).analyze(
        result.campaign_id
    )

    campaign_hash = hashlib.sha256(SYNTHETIC_PROTOCOL_V1).hexdigest()
    analyzed_hash = hashlib.sha256(appended_bytes).hexdigest()
    assert campaign_hash != analyzed_hash
    # Both hashes are recorded explicitly, and the original is untouched.
    assert report.provenance.campaign_protocol_sha256 == campaign_hash
    assert report.provenance.analyzed_protocol_sha256 == analyzed_hash
    assert report.provenance.protocol_changed_after_collection is True
    assert report.provenance.protocol_change_verified_append_only is True


def test_protocol_provenance_repeated_analysis_is_deterministic(tmp_path: Path) -> None:
    result, dirs, repo_dir, _commit = _build_campaign_with_synthetic_protocol(tmp_path)
    appended_bytes = SYNTHETIC_PROTOCOL_V1 + b"\n### Amendment 1 -- synthetic, post-data.\n"
    amended_path = tmp_path / "amended_protocol.md"
    amended_path.write_bytes(appended_bytes)

    report_1 = _analyzer_with(dirs, protocol_path=amended_path, repo=repo_dir).analyze(
        result.campaign_id
    )
    report_2 = _analyzer_with(dirs, protocol_path=amended_path, repo=repo_dir).analyze(
        result.campaign_id
    )
    assert report_1.model_dump(mode="json") == report_2.model_dump(mode="json")

    hashes_1 = _analyzer_with(dirs, protocol_path=amended_path, repo=repo_dir).write_outputs(
        report_1, tmp_path / "out1"
    )
    hashes_2 = _analyzer_with(dirs, protocol_path=amended_path, repo=repo_dir).write_outputs(
        report_2, tmp_path / "out2"
    )
    assert hashes_1 == hashes_2
    for name in hashes_1:
        assert (tmp_path / "out1" / name).read_bytes() == (tmp_path / "out2" / name).read_bytes()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda b: b[:40] + b"X" + b[41:], id="modify_one_byte"),
        pytest.param(lambda b: b[:40] + b[41:], id="delete_one_byte"),
        pytest.param(lambda b: b[:40] + b"INSERTED_MID_DOCUMENT" + b[40:], id="insert_into_middle"),
        pytest.param(lambda b: b[len(b) // 2 :] + b[: len(b) // 2], id="reorder_halves"),
        pytest.param(
            lambda b: b.replace(b"Some preregistered estimand text", b"A different estimand text"),
            id="replace_pre_existing_text",
        ),
    ],
)
def test_protocol_provenance_rejects_any_non_append_change(
    tmp_path: Path, mutate: Callable[[bytes], bytes]
) -> None:
    result, dirs, repo_dir, _commit = _build_campaign_with_synthetic_protocol(tmp_path)

    mutated_bytes = mutate(SYNTHETIC_PROTOCOL_V1)
    assert mutated_bytes != SYNTHETIC_PROTOCOL_V1  # the mutation must actually change something
    mutated_path = tmp_path / "mutated_protocol.md"
    mutated_path.write_bytes(mutated_bytes)

    with pytest.raises(
        CampaignValidationError,
        match="protocol_hash_matches_current_document_or_is_a_verified_pure_append_amendment",
    ):
        _analyzer_with(dirs, protocol_path=mutated_path, repo=repo_dir).analyze(result.campaign_id)


def test_protocol_provenance_fails_closed_when_repo_is_not_a_git_repository(tmp_path: Path) -> None:
    result, dirs, _repo_dir, _commit = _build_campaign_with_synthetic_protocol(tmp_path)

    # A genuinely valid pure append...
    appended_bytes = SYNTHETIC_PROTOCOL_V1 + b"\n### Amendment 1 -- synthetic, post-data.\n"
    amended_path = tmp_path / "amended_protocol.md"
    amended_path.write_bytes(appended_bytes)

    # ...must still be rejected if it cannot be independently verified: here
    # ``repo`` is an ordinary directory with no ``.git`` at all, so `git show`
    # necessarily fails and the analyzer must fail closed, not assume append.
    not_a_repo = tmp_path / "not_a_git_repo"
    not_a_repo.mkdir()

    with pytest.raises(
        CampaignValidationError,
        match="protocol_hash_matches_current_document_or_is_a_verified_pure_append_amendment",
    ):
        _analyzer_with(dirs, protocol_path=amended_path, repo=not_a_repo).analyze(result.campaign_id)


def test_protocol_provenance_fails_closed_when_commit_is_unresolvable(tmp_path: Path) -> None:
    repo_dir = tmp_path / "synthetic_repo"
    bogus_commit = "0" * 40  # syntactically valid, but never committed

    protocol_path = repo_dir / "docs" / "research_protocol.md"
    _init_synthetic_protocol_repo(repo_dir, SYNTHETIC_PROTOCOL_V1)

    result, dirs = _build_campaign(
        tmp_path,
        target_per_task=1,
        protocol_path=protocol_path,
        inspect_repo=lambda _repo: (bogus_commit, False),
    )

    appended_bytes = SYNTHETIC_PROTOCOL_V1 + b"\n### Amendment 1 -- synthetic, post-data.\n"
    amended_path = tmp_path / "amended_protocol.md"
    amended_path.write_bytes(appended_bytes)

    with pytest.raises(
        CampaignValidationError,
        match="protocol_hash_matches_current_document_or_is_a_verified_pure_append_amendment",
    ):
        _analyzer_with(dirs, protocol_path=amended_path, repo=repo_dir).analyze(result.campaign_id)


# --------------------------------------------------------------------------
# 4. Sanitization and determinism
# --------------------------------------------------------------------------


def test_output_files_contain_no_leaked_or_secret_content(tmp_path: Path) -> None:
    result, dirs = _build_mixed_adjudication_campaign(tmp_path)
    analyzer = _analyzer(dirs)
    report = analyzer.analyze(result.campaign_id)
    output_dir = tmp_path / "results"
    analyzer.write_outputs(report, output_dir)

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in output_dir.iterdir() if path.is_file()
    )
    assert HIDDEN_SENTINEL not in combined
    assert VISIBLE_SENTINEL not in combined
    assert "ANALYSIS_CANDIDATE_PASS_MARKER" not in combined
    assert "ANALYSIS_CANDIDATE_FAIL_MARKER" not in combined
    assert "ANALYSIS_CANDIDATE_HIDDEN_FAIL_MARKER" not in combined
    assert str(tmp_path) not in combined
    assert "/Users/" not in combined
    assert "resp_" not in combined
    assert "def test_hidden_" not in combined
    assert "def test_visible_" not in combined
    assert "OPENAI_API_KEY" not in combined or "OPENAI_API_KEY" not in combined.replace(
        "OPENAI_API_KEY", ""
    )


def test_output_paths_are_project_relative_in_cli_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    monkeypatch.setattr("experiment.cli.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_store.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_candidates_dir", lambda: dirs.candidates_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_experiments_dir", lambda: dirs.experiments_dir)
    monkeypatch.setattr(
        "experiment.campaign_analyzer.default_adjudications_path", lambda: dirs.adjudications_path
    )
    monkeypatch.setattr("experiment.campaign_analyzer.default_protocol_path", lambda: PROTOCOL_PATH)

    output_dir = tmp_path / "cli-results"
    from experiment.cli import run_campaign_analyze

    summary = run_campaign_analyze(str(result.campaign_id), output_dir=output_dir)
    assert not summary["output_dir"].startswith("/")
    assert str(tmp_path) not in json.dumps(summary)


def test_deterministic_repeated_analysis_produces_identical_output_hashes(tmp_path: Path) -> None:
    result, dirs = _build_mixed_adjudication_campaign(tmp_path)
    analyzer = _analyzer(dirs)

    report_1 = analyzer.analyze(result.campaign_id)
    hashes_1 = analyzer.write_outputs(report_1, tmp_path / "run1")

    report_2 = analyzer.analyze(result.campaign_id)
    hashes_2 = analyzer.write_outputs(report_2, tmp_path / "run2")

    assert report_1.model_dump(mode="json") == report_2.model_dump(mode="json")
    assert hashes_1 == hashes_2
    assert set(hashes_1) == {
        "analysis.json",
        "candidate_results.csv",
        "condition_summary.csv",
        "paired_differences.csv",
        "adjudication_queue.json",
        "README.md",
    }
    for name in hashes_1:
        content_1 = (tmp_path / "run1" / name).read_bytes()
        content_2 = (tmp_path / "run2" / name).read_bytes()
        assert content_1 == content_2


def test_rerunning_write_outputs_overwrites_deterministically(tmp_path: Path) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    analyzer = _analyzer(dirs)
    report = analyzer.analyze(result.campaign_id)
    output_dir = tmp_path / "results"
    hashes_first = analyzer.write_outputs(report, output_dir)
    hashes_second = analyzer.write_outputs(report, output_dir)
    assert hashes_first == hashes_second
    assert list(output_dir.glob("*.tmp")) == []


# --------------------------------------------------------------------------
# 5. Never modifies raw artifacts
# --------------------------------------------------------------------------


def test_analysis_never_modifies_raw_artifacts(tmp_path: Path) -> None:
    result, dirs = _build_mixed_adjudication_campaign(tmp_path)

    def snapshot() -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        for root in (dirs.campaigns_dir, dirs.candidates_dir, dirs.experiments_dir, dirs.tasks_root):
            for path in root.rglob("*"):
                if path.is_file():
                    files[str(path)] = path.read_bytes()
        return files

    before = snapshot()
    analyzer = _analyzer(dirs)
    report = analyzer.analyze(result.campaign_id)
    analyzer.write_outputs(report, tmp_path / "results")
    after = snapshot()

    assert before == after


# --------------------------------------------------------------------------
# 6. CLI wiring
# --------------------------------------------------------------------------


def test_cli_campaign_analyze_writes_outputs_and_prints_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    monkeypatch.setattr("experiment.cli.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_store.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_candidates_dir", lambda: dirs.candidates_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_experiments_dir", lambda: dirs.experiments_dir)
    monkeypatch.setattr(
        "experiment.campaign_analyzer.default_adjudications_path", lambda: dirs.adjudications_path
    )
    monkeypatch.setattr("experiment.campaign_analyzer.default_protocol_path", lambda: PROTOCOL_PATH)
    output_dir = tmp_path / "cli-results"
    monkeypatch.setattr("experiment.campaign_analyzer.default_results_dir", lambda: tmp_path / "unused")

    exit_code = cli.main(
        ["campaign-analyze", "--campaign-id", str(result.campaign_id), "--output-dir", str(output_dir)]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["campaign_id"] == str(result.campaign_id)
    assert payload["candidate_count"] == 2
    assert (output_dir / "analysis.json").is_file()
    assert (output_dir / "candidate_results.csv").is_file()
    assert (output_dir / "condition_summary.csv").is_file()
    assert (output_dir / "adjudication_queue.json").is_file()
    assert (output_dir / "README.md").is_file()


def test_cli_campaign_analyze_exits_nonzero_on_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)
    campaign = load_campaign(result.campaign_id, dirs.campaigns_dir)
    save_campaign(campaign.model_copy(update={"status": "blocked"}), dirs.campaigns_dir)

    monkeypatch.setattr("experiment.cli.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_store.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_candidates_dir", lambda: dirs.candidates_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_experiments_dir", lambda: dirs.experiments_dir)
    monkeypatch.setattr(
        "experiment.campaign_analyzer.default_adjudications_path", lambda: dirs.adjudications_path
    )
    monkeypatch.setattr("experiment.campaign_analyzer.default_protocol_path", lambda: PROTOCOL_PATH)

    exit_code = cli.main(["campaign-analyze", "--campaign-id", str(result.campaign_id)])
    captured = capsys.readouterr()
    assert exit_code == 1
    error_payload = json.loads(captured.err)
    assert "completed" in error_payload["error"]


def test_cli_campaign_analyze_makes_no_provider_or_api_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, dirs = _build_campaign(tmp_path, target_per_task=1)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("campaign-analyze must not initialize a provider or API client")

    import experiment.openai_candidate_generator as openai_candidate_module
    import experiment.openai_reviewer as openai_reviewer_module

    monkeypatch.setattr(openai_reviewer_module.OpenAIReviewer, "__init__", boom)
    monkeypatch.setattr(openai_candidate_module.OpenAICandidateGenerator, "__init__", boom)
    monkeypatch.setattr(openai_reviewer_module, "OpenAI", boom)
    monkeypatch.setattr(openai_candidate_module, "OpenAI", boom)
    monkeypatch.setattr("experiment.cli.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_store.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_campaigns_dir", lambda: dirs.campaigns_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_candidates_dir", lambda: dirs.candidates_dir)
    monkeypatch.setattr("experiment.campaign_analyzer.default_experiments_dir", lambda: dirs.experiments_dir)
    monkeypatch.setattr(
        "experiment.campaign_analyzer.default_adjudications_path", lambda: dirs.adjudications_path
    )
    monkeypatch.setattr("experiment.campaign_analyzer.default_protocol_path", lambda: PROTOCOL_PATH)

    exit_code = cli.main(
        ["campaign-analyze", "--campaign-id", str(result.campaign_id), "--output-dir", str(tmp_path / "out")]
    )
    assert exit_code == 0
