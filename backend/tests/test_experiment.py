"""Tests for the experiment harness: task loading, running, and reviewer context.

Uses the real ``expression_evaluator`` task as its primary fixture, since
that task's candidate is deliberately built to pass its visible suite and
fail at least one hidden test.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from experiment.context import build_reviewer_context
from experiment.loader import LoadedTask, TaskLoadError, TaskLoader
from experiment.runner import PytestRunner

TASK_ID = "expression_evaluator"
HIDDEN_SENTINEL = "VERIGATE_HIDDEN_TEST_SENTINEL_7f3c1a"


@pytest.fixture(scope="module")
def task() -> LoadedTask:
    return TaskLoader().load(TASK_ID)


# 1. The task manifest loads correctly.
def test_manifest_loads_correctly(task: LoadedTask) -> None:
    assert task.manifest.task_id == TASK_ID
    assert task.manifest.language == "python"
    assert task.manifest.timeout_seconds > 0
    assert task.specification_path.is_file()
    assert task.candidate_path.is_file()
    assert task.visible_tests_path.is_dir()
    assert task.hidden_tests_path.is_dir()


def test_loader_raises_clean_error_for_missing_task(tmp_path: Path) -> None:
    with pytest.raises(TaskLoadError):
        TaskLoader(tasks_root=tmp_path).load("does_not_exist")


# 2. The candidate passes the visible suite.
def test_candidate_passes_visible_suite(task: LoadedTask) -> None:
    result = PytestRunner().run_visible(task)
    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.passed is True
    assert result.failed_count == 0
    assert result.passed_count > 0


# 3. The same candidate fails at least one hidden test.
def test_candidate_fails_hidden_suite(task: LoadedTask) -> None:
    result = PytestRunner().run_hidden(task)
    assert result.timed_out is False
    assert result.passed is False
    assert result.exit_code == 1
    assert result.failed_count >= 1


# 4. Reviewer context includes the specification, candidate code, and visible tests.
def test_reviewer_context_includes_allowed_content(task: LoadedTask) -> None:
    context = build_reviewer_context(task)
    assert context.task_id == TASK_ID
    assert "evaluate" in context.specification
    assert "def evaluate" in context.candidate_source
    assert context.visible_tests_source
    assert all(name.endswith(".py") for name in context.visible_tests_source)
    assert "test_addition" in context.visible_tests_source["test_visible.py"]


# 5. Reviewer context contains no hidden-test content, filename, path, result, or sentinel.
def test_reviewer_context_excludes_hidden_content(task: LoadedTask) -> None:
    context = build_reviewer_context(task)
    hidden_filenames = {p.name for p in task.hidden_tests_path.glob("*.py")}
    assert hidden_filenames  # sanity: hidden tests actually exist on disk

    serialized = str(context.to_dict())

    for filename in hidden_filenames:
        assert filename not in serialized
    assert "hidden_tests" not in serialized
    assert HIDDEN_SENTINEL not in serialized
    assert context.visible_tests_source.keys().isdisjoint(hidden_filenames)


# 6. Timeout/error results are represented cleanly.
def test_timeout_is_reported_cleanly(tmp_path: Path) -> None:
    _write_slow_task(tmp_path)
    slow_task = TaskLoader(tasks_root=tmp_path).load("slow_task")

    result = PytestRunner().run_hidden(slow_task)

    assert result.timed_out is True
    assert result.passed is False
    assert result.exit_code is None
    assert result.failed_count == 0
    assert result.duration_seconds < 10  # well under the test's 30s sleep


def test_missing_manifest_file_raises_clean_error(tmp_path: Path) -> None:
    empty_task_dir = tmp_path / "no_manifest"
    empty_task_dir.mkdir()
    with pytest.raises(TaskLoadError):
        TaskLoader(tasks_root=tmp_path).load("no_manifest")


def _write_slow_task(tasks_root: Path) -> None:
    """Build a minimal throwaway task on disk whose hidden test never finishes."""

    task_dir = tasks_root / "slow_task"
    (task_dir / "visible_tests").mkdir(parents=True)
    (task_dir / "hidden_tests").mkdir(parents=True)

    (task_dir / "specification.md").write_text("# Slow task\n", encoding="utf-8")
    (task_dir / "candidate.py").write_text(
        "def evaluate(x):\n    return x\n", encoding="utf-8"
    )
    (task_dir / "visible_tests" / "test_visible.py").write_text(
        "def test_noop():\n    assert True\n", encoding="utf-8"
    )
    (task_dir / "hidden_tests" / "test_hidden.py").write_text(
        textwrap.dedent(
            """
            import time


            def test_sleeps_forever():
                time.sleep(30)
            """
        ),
        encoding="utf-8",
    )
    (task_dir / "manifest.json").write_text(
        textwrap.dedent(
            """
            {
              "task_id": "slow_task",
              "title": "Slow Task",
              "language": "python",
              "timeout_seconds": 0.5,
              "paths": {
                "specification": "specification.md",
                "candidate": "candidate.py",
                "visible_tests": "visible_tests",
                "hidden_tests": "hidden_tests"
              }
            }
            """
        ),
        encoding="utf-8",
    )
