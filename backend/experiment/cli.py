"""Command-line entry point for VeriGate's local experiment harness.

Usage (run from ``backend/``, with the virtualenv active)::

    python -m experiment.cli run --task expression_evaluator
    python -m experiment.cli prompts --task expression_evaluator
    python -m experiment.cli review --task expression_evaluator --condition A_NO_RESULT --provider mock
    python -m experiment.cli review --task expression_evaluator --condition A_NO_RESULT --provider openai
    python -m experiment.cli review --task expression_evaluator --condition A_NO_RESULT --provider openai --model gpt-5.6-luna

``run`` executes the visible/hidden suites and prints their results plus
the reviewer context. ``prompts`` prints the fully constructed reviewer
prompt for all three conditions (A/B/C) so their differences can be
inspected. ``review`` runs a reviewer backend (``mock`` or ``openai``)
against one condition and prints a validated, structured result -- or,
on failure, a concise, secret-free JSON error on stderr. None of these
commands ever read or print hidden-test source, paths, or results, and
none ever print the value of ``OPENAI_API_KEY``. Using ``--provider
openai`` makes a real, billed API call.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .conditions import Condition
from .context import build_reviewer_context
from .loader import TaskLoadError, TaskLoader
from .models import ReviewerResult
from .openai_reviewer import DEFAULT_MODEL, OpenAIReviewer, OpenAIReviewerError
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
        else:  # pragma: no cover - argparse enforces valid subcommands
            parser.print_help()
            return 1
    except (TaskLoadError, ValueError, OpenAIReviewerError) as exc:
        # Every message on these exception types is safe to print: none of
        # them ever include OPENAI_API_KEY or other secrets.
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
