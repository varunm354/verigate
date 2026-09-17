"""Command-line entry point for VeriGate's local experiment harness.

Usage (run from ``backend/``, with the virtualenv active)::

    python -m experiment.cli run --task expression_evaluator

Prints structured JSON with the visible-suite result, hidden-suite
result, and reviewer context, kept as separate top-level keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from .context import build_reviewer_context
from .loader import TaskLoader, TaskLoadError
from .runner import PytestRunner


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


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m experiment.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a task's visible and hidden suites")
    run_parser.add_argument("--task", required=True, help="Task ID under backend/tasks/")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        try:
            result = run_task(args.task)
        except TaskLoadError as exc:
            print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
