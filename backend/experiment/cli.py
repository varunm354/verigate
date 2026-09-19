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
``backend/data/candidates/<task_id>/<candidate_id>/``. None of these
commands ever read or print hidden-test source, paths, or results, and
none ever print the value of ``OPENAI_API_KEY``. Using ``--provider
openai`` makes one real, billed API call per condition per repetition
(i.e. ``repetitions x 3`` calls for ``experiment``) or one billed call
per generation attempt for ``generate-candidate``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
