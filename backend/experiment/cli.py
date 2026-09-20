"""Command-line entry point for VeriGate's local experiment harness.

Usage (run from ``backend/``, with the virtualenv active)::

    python -m experiment.cli run --task expression_evaluator
    python -m experiment.cli prompts --task expression_evaluator
    python -m experiment.cli review --task expression_evaluator --condition A_NO_RESULT --provider mock
    python -m experiment.cli review --task expression_evaluator --condition A_NO_RESULT --provider openai
    python -m experiment.cli review --task expression_evaluator --condition A_NO_RESULT --provider openai --model gpt-5.6-luna
    python -m experiment.cli experiment --task expression_evaluator --provider mock --repetitions 1 --seed 42
    python -m experiment.cli experiment --task json_parser --candidate-id <UUID> --provider mock --repetitions 3 --seed 42
    python -m experiment.cli generate-candidate --task json_parser --provider mock --max-attempts 3 --seed 42
    python -m experiment.cli campaign-create --generator-provider openai --generator-model gpt-5.6-luna --reviewer-provider openai --reviewer-model gpt-5.6-luna --target-per-task 6 --max-candidate-attempts 3 --generation-seed-start 42 --reviewer-seed-start 1000 --repetitions 3 --cutoff 2026-09-21T12:00:00-07:00 --max-generation-attempts 36
    python -m experiment.cli campaign-status --campaign-id <UUID>
    python -m experiment.cli campaign-run --campaign-id <UUID>
    python -m experiment.cli campaign-analyze --campaign-id <UUID>

``run`` executes the visible/hidden suites and prints their results plus
the reviewer context. ``prompts`` prints the fully constructed reviewer
prompt for all three conditions (A/B/C) so their differences can be
inspected. ``review`` runs a reviewer backend (``mock`` or ``openai``)
against one condition and prints a validated, structured result -- or,
on failure, a concise, secret-free JSON error on stderr. ``experiment``
runs every condition (A/B/C) some number of ``--repetitions``, in a
reproducible seeded-random per-repetition order, saving a durable,
checkpointed artifact under ``backend/data/experiments/<experiment_id>.json``
and printing a concise summary (never the full prompts). Passing
``--candidate-id <UUID>`` makes the experiment candidate-aware: the
reviewer prompts, the re-run visible tests, and (only after every
reviewer call completes) the hidden tests are all built from that exact
saved, previously generated candidate artifact
(``backend/data/candidates/<task>/<candidate_id>/``) instead of from the
task's tracked reference implementation. ``--candidate-id`` is optional;
omitting it reproduces the exact prior tracked-candidate behavior.
``generate-candidate`` runs a bounded, hidden-blind coding-agent loop
(specification + starter + visible tests + visible-test execution
feedback only) and saves the candidate under
``backend/data/candidates/<task_id>/<candidate_id>/``. ``campaign-create``
records a durable preregistered campaign under
``backend/data/campaigns/<campaign_id>.json`` with no provider
initialization and no API calls. ``campaign-status`` is a read-only
summary of that artifact. ``campaign-run`` is the only campaign command
that may initialize real providers; it generates, reviews, and
checkpoint-resumes sequentially until both task quotas are filled, the
Pacific cutoff is reached, or the campaign is blocked. None of these
commands ever read or print hidden-test source, paths, or results, and
none ever print the value of ``OPENAI_API_KEY``. Using ``--provider
openai`` makes one real, billed API call per condition per repetition
(i.e. ``repetitions x 3`` calls for ``experiment``) or one billed call
per generation attempt for ``generate-candidate``. ``campaign-run`` with
openai providers makes those billed calls for each generation attempt
and each qualifying candidate's reviewer observations. ``campaign-analyze``
validates a *completed* campaign against its referenced candidate/experiment
artifacts (duplicate ids/seeds, hash/provenance mismatches, condition and
quota counts, adjudication-policy application, and more -- see
``experiment.campaign_analyzer``), then writes a deterministic, sanitized
analysis (JSON + CSV + an adjudication queue + a README) under
``research/results/<campaign_id>/``. It never initializes a provider, never
makes a network/API call, and never writes to any raw campaign, candidate,
or experiment artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .campaign_models import (
    DEFAULT_CUTOFF_LOCAL,
    DEFAULT_GENERATION_SEED_START,
    DEFAULT_MAX_CANDIDATE_ATTEMPTS,
    DEFAULT_MAX_GENERATION_ATTEMPTS,
    DEFAULT_REPETITIONS,
    DEFAULT_REVIEWER_SEED_START,
    DEFAULT_TARGET_PER_TASK,
    CampaignDirtyWorktreeError,
    CampaignLoadError,
    CampaignLockError,
    CampaignOperationError,
)
from .campaign_analyzer import (
    CampaignAnalysisError,
    CampaignAnalyzer,
    default_results_dir,
)
from .campaign_orchestrator import (
    CampaignOrchestrator,
    campaign_status_summary,
    create_campaign,
    select_next_task,
)
from .campaign_store import default_campaigns_dir, load_campaign, relative_artifact_identifier
from .candidate_generator import CandidateGenerator, MockCandidateGenerator
from .candidate_loader import CandidateArtifactLoader, CandidateArtifactLoadError, LoadedCandidate
from .candidate_models import CandidateGenerationError
from .candidate_orchestrator import (
    DEFAULT_MAX_ATTEMPTS,
    CandidateGenerationOrchestrator,
    default_candidates_dir,
)
from .conditions import Condition
from .context import build_reviewer_context
from .loader import TaskLoadError, TaskLoader
from .models import ReviewerResult
from .openai_candidate_generator import (
    DEFAULT_MODEL as DEFAULT_CANDIDATE_MODEL,
    OpenAICandidateGenerator,
    OpenAICandidateGeneratorError,
)
from .openai_reviewer import DEFAULT_MODEL, OpenAIReviewer, OpenAIReviewerError
from .orchestrator import ExperimentOrchestrator, default_experiments_dir
from .prompts import PROMPT_VERSION, build_all_prompts, build_prompt
from .reviewer import MockReviewer, ProvidesResponseMetadata, Reviewer
from .runner import PytestRunner


def _make_mock_reviewer(model: Optional[str]) -> Reviewer:
    if model is not None:
        raise ValueError(
            "--model is only supported with --provider openai (mock uses a "
            "fixed, deterministic model id)."
        )
    return MockReviewer()


def _make_openai_reviewer(model: Optional[str]) -> Reviewer:
    return OpenAIReviewer(model=model)


# Provider name -> factory accepting an optional `--model` override.
_PROVIDER_FACTORIES: dict[str, Callable[[Optional[str]], Reviewer]] = {
    "mock": _make_mock_reviewer,
    "openai": _make_openai_reviewer,
}


def _make_mock_candidate_generator(model: Optional[str]) -> CandidateGenerator:
    if model is not None:
        raise ValueError(
            "--model is only supported with --provider openai (mock uses a "
            "fixed, deterministic model id)."
        )
    return MockCandidateGenerator()


def _make_openai_candidate_generator(model: Optional[str]) -> CandidateGenerator:
    return OpenAICandidateGenerator(model=model)


_CANDIDATE_PROVIDER_FACTORIES: dict[str, Callable[[Optional[str]], CandidateGenerator]] = {
    "mock": _make_mock_candidate_generator,
    "openai": _make_openai_candidate_generator,
}


def run_task(task_id: str, tasks_root: Optional[Path] = None) -> dict[str, Any]:
    """Load, run, and build reviewer context for a single task."""

    task = TaskLoader(tasks_root=tasks_root).load(task_id)

    runner = PytestRunner()
    visible_result = runner.run_visible(task)
    hidden_result = runner.run_hidden(task)
    reviewer_context = build_reviewer_context(task)

    return {
        "task_id": task.manifest.task_id,
        "title": task.manifest.title,
        "visible": visible_result.model_dump(),
        "hidden": hidden_result.model_dump(),
        "reviewer_context": reviewer_context.to_dict(),
    }


def run_prompts(task_id: str, tasks_root: Optional[Path] = None) -> dict[str, Any]:
    """Build all three conditions' prompts and return them as structured data.

    Shared content (identical across all conditions) is reported once;
    each condition then only reports the two fields that can vary
    (``visible_result_statement`` and ``reviewer_strategy_instruction``)
    plus its fully rendered prompt text, making differences easy to spot.
    """

    task = TaskLoader(tasks_root=tasks_root).load(task_id)
    prompts = build_all_prompts(task)

    any_prompt = next(iter(prompts.values()))
    shared = {
        "instructions": any_prompt.sections.instructions,
        "specification": any_prompt.sections.specification,
        "candidate_source": any_prompt.sections.candidate_source,
        "visible_tests_source": any_prompt.sections.visible_tests_source,
        "question": any_prompt.sections.question,
    }

    conditions = {
        condition.value: {
            "visible_result_statement": prompt.sections.visible_result_statement,
            "reviewer_strategy_instruction": prompt.sections.reviewer_strategy_instruction,
            "text": prompt.text,
        }
        for condition, prompt in prompts.items()
    }

    return {
        "task_id": task.manifest.task_id,
        "prompt_version": PROMPT_VERSION,
        "shared": shared,
        "conditions": conditions,
    }


def run_review(
    task_id: str,
    condition: Condition,
    provider: str,
    model: Optional[str] = None,
    tasks_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Build the prompt for one condition and run it through a reviewer backend.

    ``model`` is an optional explicit override (e.g. a CLI ``--model``
    flag); providers that support model selection (currently only
    ``openai``) resolve their own default/environment fallback when it is
    ``None``. Passing ``model`` for a provider that doesn't support it
    (currently ``mock``) is a clean, explicit error rather than being
    silently ignored.
    """

    if provider not in _PROVIDER_FACTORIES:
        raise ValueError(f"Unknown provider {provider!r}. Available: {sorted(_PROVIDER_FACTORIES)}")

    task = TaskLoader(tasks_root=tasks_root).load(task_id)
    prompt = build_prompt(task, condition)
    reviewer = _PROVIDER_FACTORIES[provider](model)

    start = time.perf_counter()
    assessment = reviewer.review(condition, prompt)
    latency_seconds = time.perf_counter() - start

    result_kwargs: dict[str, Any] = dict(
        task_id=task.manifest.task_id,
        condition=condition,
        assessment=assessment,
        prompt_version=PROMPT_VERSION,
        provider=reviewer.provider,
        model=reviewer.model,
        timestamp=datetime.now(timezone.utc),
        latency_seconds=latency_seconds,
    )
    if isinstance(reviewer, ProvidesResponseMetadata):
        result_kwargs["response_id"] = reviewer.last_response_id
        result_kwargs["input_tokens"] = reviewer.last_input_tokens
        result_kwargs["output_tokens"] = reviewer.last_output_tokens

    result = ReviewerResult(**result_kwargs)
    return result.model_dump(mode="json")


def run_experiment(
    task_id: str,
    provider: str,
    repetitions: int,
    random_seed: int,
    model: Optional[str] = None,
    tasks_root: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    candidate_id: Optional[str] = None,
    candidates_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Run a full A/B/C x ``repetitions`` experiment and return a concise summary.

    The complete, durable artifact (including every observation's full
    ``ReviewerResult``) is saved to disk by :class:`ExperimentOrchestrator`
    regardless of outcome; this function's return value is only the
    concise summary intended for CLI/human consumption -- it never
    includes full prompt text or any secret.

    If ``candidate_id`` is given, it is loaded and verified (see
    :class:`experiment.candidate_loader.CandidateArtifactLoader`) *before*
    the orchestrator runs, and the resulting :class:`LoadedCandidate` is
    passed through so the entire experiment reviews that exact saved
    candidate source instead of the tracked reference implementation. A
    candidate generated for a different task, or one that fails any
    validation/verification check, raises
    :class:`~experiment.candidate_loader.CandidateArtifactLoadError` (a
    safe, secret-free error) rather than silently falling back.
    """

    if provider not in _PROVIDER_FACTORIES:
        raise ValueError(f"Unknown provider {provider!r}. Available: {sorted(_PROVIDER_FACTORIES)}")
    if repetitions < 1:
        raise ValueError("--repetitions must be a positive integer")

    candidate: Optional[LoadedCandidate] = None
    if candidate_id is not None:
        task = TaskLoader(tasks_root=tasks_root).load(task_id)
        candidate = CandidateArtifactLoader(candidates_root=candidates_root).load(task, candidate_id)

    def reviewer_factory() -> Reviewer:
        return _PROVIDER_FACTORIES[provider](model)

    orchestrator = ExperimentOrchestrator(
        reviewer_factory=reviewer_factory,
        tasks_root=tasks_root,
        output_dir=output_dir,
    )
    artifact = orchestrator.run(
        task_id=task_id, repetitions=repetitions, random_seed=random_seed, candidate=candidate
    )
    artifact_path = orchestrator.artifact_path(artifact.metadata.experiment_id)

    mean_confidence_by_condition: dict[str, Optional[float]] = {}
    for condition in Condition:
        confidences = [
            obs.result.assessment.confidence
            for obs in artifact.observations
            if obs.condition == condition
        ]
        mean_confidence_by_condition[condition.value] = (
            sum(confidences) / len(confidences) if confidences else None
        )

    execution_order = [
        {"execution_order_index": obs.execution_order_index, "repetition_index": obs.repetition_index, "condition": obs.condition.value}
        for obs in sorted(artifact.observations, key=lambda o: o.execution_order_index)
    ]

    ground_truth = artifact.ground_truth
    visible_summary = (
        {
            "passed": ground_truth.visible_passed,
            "passed_count": ground_truth.visible_passed_count,
            "failed_count": ground_truth.visible_failed_count,
        }
        if ground_truth is not None
        else None
    )
    hidden_summary = (
        {
            "passed": ground_truth.hidden_passed,
            "passed_count": ground_truth.hidden_passed_count,
            "failed_count": ground_truth.hidden_failed_count,
        }
        if ground_truth is not None and ground_truth.hidden_passed is not None
        else None
    )

    summary: dict[str, Any] = {
        "status": artifact.metadata.status,
        "experiment_id": str(artifact.metadata.experiment_id),
        "artifact_path": str(artifact_path),
        "task_id": artifact.metadata.task_id,
        "provider": artifact.metadata.provider,
        "model": artifact.metadata.model,
        "candidate_id": str(artifact.metadata.candidate_id) if artifact.metadata.candidate_id else None,
        "candidate_source_sha256": artifact.metadata.candidate_source_sha256,
        "repetitions": artifact.metadata.repetitions,
        "random_seed": artifact.metadata.random_seed,
        "observation_count": len(artifact.observations),
        "execution_order": execution_order,
        "visible": visible_summary,
        "hidden": hidden_summary,
        "mean_confidence_by_condition": mean_confidence_by_condition,
    }
    if artifact.error is not None:
        summary["error"] = {
            "category": artifact.error.category,
            "message": artifact.error.message,
            "condition": artifact.error.condition.value if artifact.error.condition else None,
            "repetition_index": artifact.error.repetition_index,
        }

    return summary


def run_generate_candidate(
    task_id: str,
    provider: str,
    max_attempts: int,
    random_seed: int,
    model: Optional[str] = None,
    tasks_root: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Run hidden-blind candidate generation and return a concise summary.

    The complete artifact (candidate source + ``metadata.json``) is saved
    under ``backend/data/candidates/<task_id>/<candidate_id>/``. This
    return value is only the CLI/human summary -- it never includes full
    prompt text, hidden-test content, or any secret.
    """

    if provider not in _CANDIDATE_PROVIDER_FACTORIES:
        raise ValueError(
            f"Unknown provider {provider!r}. Available: {sorted(_CANDIDATE_PROVIDER_FACTORIES)}"
        )
    if max_attempts < 1:
        raise ValueError("--max-attempts must be a positive integer")

    def generator_factory() -> CandidateGenerator:
        return _CANDIDATE_PROVIDER_FACTORIES[provider](model)

    orchestrator = CandidateGenerationOrchestrator(
        generator_factory=generator_factory,
        tasks_root=tasks_root,
        output_dir=output_dir,
    )
    metadata = orchestrator.run(
        task_id=task_id, max_attempts=max_attempts, random_seed=random_seed
    )
    artifact_path = orchestrator.artifact_dir(task_id, metadata.candidate_id)

    summary: dict[str, Any] = {
        "status": metadata.status,
        "stop_reason": metadata.stop_reason,
        "candidate_id": str(metadata.candidate_id),
        "artifact_path": str(artifact_path),
        "task_id": metadata.task_id,
        "provider": metadata.provider,
        "model": metadata.model,
        "attempt_count": metadata.attempt_count,
        "visible_tests_passed": metadata.visible_tests_passed,
        "visible_passed_count": metadata.visible_passed_count,
        "visible_failed_count": metadata.visible_failed_count,
        "final_source_sha256": metadata.final_source_sha256,
        "total_input_tokens": metadata.total_input_tokens,
        "total_output_tokens": metadata.total_output_tokens,
        "total_latency_seconds": metadata.total_latency_seconds,
        "random_seed": metadata.random_seed,
        "model_sampling_deterministic": metadata.model_sampling_deterministic,
        "seed_semantics": metadata.seed_semantics,
    }
    if metadata.error is not None:
        summary["error"] = metadata.error
    return summary


def run_campaign_create(
    *,
    generator_provider: str,
    generator_model: str,
    reviewer_provider: str,
    reviewer_model: str,
    target_per_task: int,
    max_candidate_attempts: int,
    generation_seed_start: int,
    reviewer_seed_start: int,
    repetitions: int,
    cutoff: str,
    max_generation_attempts: int,
) -> dict[str, Any]:
    """Persist a campaign configuration. Never initializes a provider."""

    artifact = create_campaign(
        generator_provider=generator_provider,
        generator_model=generator_model,
        reviewer_provider=reviewer_provider,
        reviewer_model=reviewer_model,
        target_per_task=target_per_task,
        max_candidate_attempts=max_candidate_attempts,
        generation_seed_start=generation_seed_start,
        reviewer_seed_start=reviewer_seed_start,
        repetitions=repetitions,
        cutoff=cutoff,
        max_generation_attempts=max_generation_attempts,
        campaigns_dir=default_campaigns_dir(),
    )
    next_task = select_next_task(
        task_ids=artifact.config.task_ids,
        qualifying_counts=dict(artifact.qualifying_counts),
        target_per_task=artifact.config.target_per_task,
        generation_attempt_count=len(artifact.generation_attempts),
    )
    return {
        "campaign_id": str(artifact.campaign_id),
        "status": artifact.status,
        "schema_version": artifact.schema_version,
        "config": artifact.config.model_dump(mode="json"),
        "provenance": artifact.provenance.model_dump(mode="json"),
        "created_at": artifact.created_at.isoformat(),
        "next_generation_seed": artifact.next_generation_seed,
        "next_reviewer_seed": artifact.next_reviewer_seed,
        "next_scheduled_task": next_task,
        "qualifying_counts": dict(artifact.qualifying_counts),
        "artifact_path": artifact.artifact_path,
    }


def run_campaign_status(campaign_id: str) -> dict[str, Any]:
    """Read-only campaign summary. Never initializes a provider."""

    artifact = load_campaign(campaign_id, default_campaigns_dir())
    return campaign_status_summary(artifact)


def run_campaign_run(campaign_id: str) -> dict[str, Any]:
    """Run or resume a campaign. The only campaign command that may initialize providers."""

    artifact = load_campaign(campaign_id, default_campaigns_dir())
    generator_provider = artifact.config.generator_provider
    reviewer_provider = artifact.config.reviewer_provider
    generator_model = artifact.config.generator_model
    reviewer_model = artifact.config.reviewer_model

    def generator_factory() -> CandidateGenerator:
        model = None if generator_provider == "mock" else generator_model
        return _CANDIDATE_PROVIDER_FACTORIES[generator_provider](model)

    def reviewer_factory() -> Reviewer:
        model = None if reviewer_provider == "mock" else reviewer_model
        return _PROVIDER_FACTORIES[reviewer_provider](model)

    orchestrator = CampaignOrchestrator(
        generator_factory=generator_factory,
        reviewer_factory=reviewer_factory,
        campaigns_dir=default_campaigns_dir(),
    )
    result = orchestrator.run(campaign_id)
    return campaign_status_summary(result)


def run_campaign_analyze(campaign_id: str, output_dir: Optional[Path] = None) -> dict[str, Any]:
    """Validate a completed campaign and write a sanitized, deterministic analysis.

    Never initializes a reviewer/generator provider and never makes a
    network/API call -- it only reads already-saved campaign, candidate,
    and experiment artifacts (never writing to any of them) and writes
    new, sanitized files under ``research/results/<campaign_id>/``. This
    return value is only the concise CLI/human summary; the complete
    report is ``analysis.json`` in that directory.
    """

    analyzer = CampaignAnalyzer()
    report = analyzer.analyze(campaign_id)
    target_dir = Path(output_dir) if output_dir is not None else default_results_dir() / str(report.campaign_id)
    written = analyzer.write_outputs(report, target_dir)

    overall_full = next(s for s in report.full_cohort_summaries if s.task_id is None)
    overall_eligible = next(s for s in report.primary_eligible_summaries if s.task_id is None)

    return {
        "campaign_id": str(report.campaign_id),
        "campaign_status": report.campaign_status,
        "schema_version": report.schema_version,
        "output_dir": relative_artifact_identifier(target_dir, fallback=target_dir.name),
        "output_file_hashes": written,
        "candidate_count": report.candidate_count,
        "reviewer_observation_count": report.totals.reviewer_observation_count,
        "eligible_count": report.eligible_count,
        "excluded_count": report.excluded_count,
        "pending_count": report.pending_count,
        "validation_checks_passed": len(report.validation.checks),
        "full_cohort_overall": {
            "mean_confidence_by_condition": {
                c.condition: c.mean_confidence for c in overall_full.conditions
            },
            "mean_b_minus_a": overall_full.mean_b_minus_a,
            "mean_c_minus_b": overall_full.mean_c_minus_b,
            "benchmark_pass_rate": overall_full.benchmark_pass_rate,
        },
        "primary_eligible_overall": {
            "candidate_count": overall_eligible.candidate_count,
            "mean_confidence_by_condition": {
                c.condition: c.mean_confidence for c in overall_eligible.conditions
            },
            "mean_b_minus_a": overall_eligible.mean_b_minus_a,
            "mean_c_minus_b": overall_eligible.mean_c_minus_b,
        },
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m experiment.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a task's visible and hidden suites")
    run_parser.add_argument("--task", required=True, help="Task ID under backend/tasks/")

    prompts_parser = subparsers.add_parser(
        "prompts", help="Show the constructed reviewer prompt for all three conditions"
    )
    prompts_parser.add_argument("--task", required=True, help="Task ID under backend/tasks/")

    review_parser = subparsers.add_parser(
        "review", help="Run a reviewer backend against one condition's prompt"
    )
    review_parser.add_argument("--task", required=True, help="Task ID under backend/tasks/")
    review_parser.add_argument(
        "--condition",
        required=True,
        choices=[c.value for c in Condition],
        help="Experimental condition to run",
    )
    review_parser.add_argument(
        "--provider",
        default="mock",
        choices=sorted(_PROVIDER_FACTORIES),
        help="Reviewer backend to use (default: mock)",
    )
    review_parser.add_argument(
        "--model",
        default=None,
        help=(
            "Exact model ID override, only valid with --provider openai "
            f"(otherwise resolved from OPENAI_REVIEWER_MODEL, defaulting to "
            f"'{DEFAULT_MODEL}')"
        ),
    )

    experiment_parser = subparsers.add_parser(
        "experiment",
        help="Run every A/B/C condition across N repetitions and save a durable artifact",
    )
    experiment_parser.add_argument("--task", required=True, help="Task ID under backend/tasks/")
    experiment_parser.add_argument(
        "--candidate-id",
        default=None,
        help=(
            "UUID of a saved candidate artifact under "
            "backend/data/candidates/<task>/<candidate_id>/ to review instead of the "
            "task's tracked reference implementation. Optional; omitting it preserves "
            "the tracked-candidate behavior. Must have been generated for --task, with "
            "status=completed, stop_reason=visible_tests_passed, and all visible tests "
            "passing, or this command exits nonzero with a safe error on stderr."
        ),
    )
    experiment_parser.add_argument(
        "--provider",
        default="mock",
        choices=sorted(_PROVIDER_FACTORIES),
        help="Reviewer backend to use (default: mock)",
    )
    experiment_parser.add_argument(
        "--model",
        default=None,
        help=(
            "Exact model ID override, only valid with --provider openai "
            f"(otherwise resolved from OPENAI_REVIEWER_MODEL, defaulting to '{DEFAULT_MODEL}')"
        ),
    )
    experiment_parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="Number of repetitions of A/B/C to run (positive integer, default: 1)",
    )
    experiment_parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help="Integer random seed controlling each repetition's A/B/C execution order",
    )
    experiment_parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory to save the experiment artifact JSON "
            f"(default: {default_experiments_dir()})"
        ),
    )

    generate_parser = subparsers.add_parser(
        "generate-candidate",
        help="Generate a hidden-blind coding-agent candidate (spec + starter + visible tests only)",
    )
    generate_parser.add_argument("--task", required=True, help="Task ID under backend/tasks/")
    generate_parser.add_argument(
        "--provider",
        default="mock",
        choices=sorted(_CANDIDATE_PROVIDER_FACTORIES),
        help="Candidate generator backend to use (default: mock)",
    )
    generate_parser.add_argument(
        "--model",
        default=None,
        help=(
            "Exact model ID override, only valid with --provider openai "
            f"(otherwise resolved from OPENAI_CANDIDATE_MODEL, defaulting to "
            f"'{DEFAULT_CANDIDATE_MODEL}')"
        ),
    )
    generate_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"Maximum generate/visible-test/revise iterations (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    generate_parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help=(
            "Integer seed recorded for workflow reproducibility. Does not make "
            "API model sampling deterministic."
        ),
    )
    generate_parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Root directory for candidate artifacts "
            f"(default: {default_candidates_dir()})"
        ),
    )

    create_parser = subparsers.add_parser(
        "campaign-create",
        help="Create a durable preregistered campaign without initializing providers or making API calls",
    )
    create_parser.add_argument("--generator-provider", required=True, choices=sorted(_CANDIDATE_PROVIDER_FACTORIES))
    create_parser.add_argument("--generator-model", required=True, help="Exact generator model id to record")
    create_parser.add_argument("--reviewer-provider", required=True, choices=sorted(_PROVIDER_FACTORIES))
    create_parser.add_argument("--reviewer-model", required=True, help="Exact reviewer model id to record")
    create_parser.add_argument(
        "--target-per-task",
        type=int,
        default=DEFAULT_TARGET_PER_TASK,
        help=f"Qualifying-candidate quota per task (default: {DEFAULT_TARGET_PER_TASK})",
    )
    create_parser.add_argument(
        "--max-candidate-attempts",
        type=int,
        default=DEFAULT_MAX_CANDIDATE_ATTEMPTS,
        help=f"Max generate/visible-test/revise iterations per candidate (default: {DEFAULT_MAX_CANDIDATE_ATTEMPTS})",
    )
    create_parser.add_argument(
        "--generation-seed-start",
        type=int,
        default=DEFAULT_GENERATION_SEED_START,
        help=f"First generation-attempt seed (default: {DEFAULT_GENERATION_SEED_START})",
    )
    create_parser.add_argument(
        "--reviewer-seed-start",
        type=int,
        default=DEFAULT_REVIEWER_SEED_START,
        help=f"First reviewer-experiment seed (default: {DEFAULT_REVIEWER_SEED_START})",
    )
    create_parser.add_argument(
        "--repetitions",
        type=int,
        default=DEFAULT_REPETITIONS,
        help=f"A/B/C repetitions per qualifying candidate (default: {DEFAULT_REPETITIONS})",
    )
    create_parser.add_argument(
        "--cutoff",
        default=DEFAULT_CUTOFF_LOCAL,
        help=(
            "Stop starting new generation attempts at this datetime. Naive values are "
            "interpreted as America/Los_Angeles "
            f"(default: {DEFAULT_CUTOFF_LOCAL})"
        ),
    )
    create_parser.add_argument(
        "--max-generation-attempts",
        type=int,
        default=DEFAULT_MAX_GENERATION_ATTEMPTS,
        help=(
            "Safety guard on total generation attempts; reaching it blocks the campaign "
            f"without changing the target (default: {DEFAULT_MAX_GENERATION_ATTEMPTS})"
        ),
    )

    status_parser = subparsers.add_parser(
        "campaign-status",
        help="Show campaign state without initializing providers or making API calls",
    )
    status_parser.add_argument("--campaign-id", required=True, help="Campaign UUID")

    run_parser_campaign = subparsers.add_parser(
        "campaign-run",
        help="Run or resume a campaign (the only campaign command that may initialize providers)",
    )
    run_parser_campaign.add_argument("--campaign-id", required=True, help="Campaign UUID")

    analyze_parser = subparsers.add_parser(
        "campaign-analyze",
        help=(
            "Validate a completed campaign and write a sanitized, deterministic analysis "
            "under research/results/<campaign_id>/. Never initializes a provider or makes "
            "an API call; never modifies any raw artifact."
        ),
    )
    analyze_parser.add_argument("--campaign-id", required=True, help="Campaign UUID")
    analyze_parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory to write analysis outputs "
            f"(default: {default_results_dir()}/<campaign_id>/)"
        ),
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            result = run_task(args.task)
        elif args.command == "prompts":
            result = run_prompts(args.task)
        elif args.command == "review":
            result = run_review(args.task, Condition(args.condition), args.provider, args.model)
        elif args.command == "experiment":
            output_dir = Path(args.output_dir) if args.output_dir else None
            result = run_experiment(
                args.task,
                args.provider,
                args.repetitions,
                args.seed,
                args.model,
                output_dir=output_dir,
                candidate_id=args.candidate_id,
            )
        elif args.command == "generate-candidate":
            output_dir = Path(args.output_dir) if args.output_dir else None
            result = run_generate_candidate(
                args.task,
                args.provider,
                args.max_attempts,
                args.seed,
                args.model,
                output_dir=output_dir,
            )
        elif args.command == "campaign-create":
            result = run_campaign_create(
                generator_provider=args.generator_provider,
                generator_model=args.generator_model,
                reviewer_provider=args.reviewer_provider,
                reviewer_model=args.reviewer_model,
                target_per_task=args.target_per_task,
                max_candidate_attempts=args.max_candidate_attempts,
                generation_seed_start=args.generation_seed_start,
                reviewer_seed_start=args.reviewer_seed_start,
                repetitions=args.repetitions,
                cutoff=args.cutoff,
                max_generation_attempts=args.max_generation_attempts,
            )
        elif args.command == "campaign-status":
            result = run_campaign_status(args.campaign_id)
        elif args.command == "campaign-run":
            result = run_campaign_run(args.campaign_id)
        elif args.command == "campaign-analyze":
            output_dir = Path(args.output_dir) if args.output_dir else None
            result = run_campaign_analyze(args.campaign_id, output_dir=output_dir)
        else:  # pragma: no cover - argparse enforces valid subcommands
            parser.print_help()
            return 1
    except (
        TaskLoadError,
        ValueError,
        OpenAIReviewerError,
        OpenAICandidateGeneratorError,
        CandidateGenerationError,
        CandidateArtifactLoadError,
        CampaignOperationError,
        CampaignDirtyWorktreeError,
        CampaignLockError,
        CampaignLoadError,
        CampaignAnalysisError,
    ) as exc:
        # Every message on these exception types is safe to print: none of
        # them ever include OPENAI_API_KEY or other secrets.
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))

    if args.command == "experiment" and result["status"] != "completed":
        # Completed observations were still saved to disk (see
        # `artifact_path` in the summary above) -- but a reviewer call
        # failed partway through, or visible tests never passed, so this
        # run did not produce a full experiment and the CLI must signal
        # that with a nonzero exit code.
        return 1

    if args.command == "generate-candidate" and not result["visible_tests_passed"]:
        # The candidate artifact was still saved; visible tests did not
        # fully pass (or generation stopped early).
        return 1

    if args.command == "campaign-run" and result["status"] in {"blocked", "failed"}:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
