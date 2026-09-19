"""Generic hidden-test-content leakage regression test.

Unlike ``test_experiment.py`` / ``test_prompts.py`` (which check leakage
for the single ``expression_evaluator`` fixture task), this file
discovers *every* task under ``backend/tasks/`` and checks the same
mechanical guarantee for each of them: ``experiment.context.build_reviewer_context``
may only ever expose a task's specification, candidate source, and
visible-test source -- never anything from its hidden-tests directory.

Deliberately avoids asserting on generic English words (e.g. "private")
that could legitimately appear in a task's specification text. Instead
this checks, per task:

  1. The reviewer context actually contains the specification, the
     candidate source, and the visible tests (and that they match what
     is on disk) -- i.e. the *allowed* content really is present.
  2. None of the hidden suite's exact file contents appear anywhere in
     the serialized reviewer context.
  3. None of the hidden suite's filenames, nor the hidden-tests
     directory's own name, appear anywhere in the serialized reviewer
     context.
  4. If a hidden test file declares a
     ``Sentinel for automated leakage checks: <TOKEN>`` marker (the
     convention already used by ``expression_evaluator``'s hidden
     suite), that exact token does not appear in the context either.

Adding a new task under ``backend/tasks/`` automatically gets covered by
this test with no changes needed here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from experiment.context import build_reviewer_context
from experiment.loader import DEFAULT_TASKS_ROOT, LoadedTask, TaskLoader

_SENTINEL_PATTERN = re.compile(r"Sentinel for automated leakage checks:\s*(\S+)")


def _discover_task_ids(tasks_root: Path) -> list[str]:
    """Every subdirectory of ``tasks_root`` that has a ``manifest.json``."""

    if not tasks_root.is_dir():
        return []
    return sorted(
        child.name
        for child in tasks_root.iterdir()
        if child.is_dir() and (child / "manifest.json").is_file()
    )


_TASK_IDS = _discover_task_ids(DEFAULT_TASKS_ROOT)


def test_at_least_one_task_is_discovered() -> None:
    # Sanity check: if this is ever empty, every other test in this file
    # silently collects zero cases, which would hide real leakage bugs.
    assert _TASK_IDS, f"No tasks with a manifest.json found under {DEFAULT_TASKS_ROOT}"


@pytest.fixture(scope="module", params=_TASK_IDS, ids=_TASK_IDS)
def task(request: pytest.FixtureRequest) -> LoadedTask:
    return TaskLoader().load(request.param)


def test_reviewer_context_includes_allowed_content(task: LoadedTask) -> None:
    """The specification, candidate source, and visible tests are present and correct."""

    context = build_reviewer_context(task)

    assert context.task_id == task.manifest.task_id
    assert context.specification == task.specification_path.read_text(encoding="utf-8")
    assert context.candidate_source == task.candidate_path.read_text(encoding="utf-8")

    assert context.visible_tests_source  # non-empty
    on_disk_visible = {p.name for p in task.visible_tests_path.glob("*.py")}
    assert set(context.visible_tests_source) == on_disk_visible
    for name, source in context.visible_tests_source.items():
        expected = (task.visible_tests_path / name).read_text(encoding="utf-8")
        assert source == expected, (task.manifest.task_id, name)


def test_reviewer_context_excludes_hidden_filenames_and_path(task: LoadedTask) -> None:
    """Hidden filenames and the hidden-tests directory name never appear."""

    hidden_files = sorted(task.hidden_tests_path.glob("*.py"))
    assert hidden_files  # sanity: hidden tests actually exist on disk for this task

    context = build_reviewer_context(task)
    serialized = str(context.to_dict())

    assert task.hidden_tests_path.name not in serialized
    for hidden_file in hidden_files:
        assert hidden_file.name not in serialized, (task.manifest.task_id, hidden_file.name)

    hidden_names = {f.name for f in hidden_files}
    assert set(context.visible_tests_source).isdisjoint(hidden_names)


def test_reviewer_context_excludes_hidden_source_content(task: LoadedTask) -> None:
    """No hidden test file's exact source, nor its declared sentinel, ever leaks."""

    hidden_files = sorted(task.hidden_tests_path.glob("*.py"))
    assert hidden_files  # sanity: hidden tests actually exist on disk for this task

    context = build_reviewer_context(task)
    serialized = str(context.to_dict())

    for hidden_file in hidden_files:
        hidden_source = hidden_file.read_text(encoding="utf-8")
        assert hidden_source not in serialized, (task.manifest.task_id, hidden_file.name)

        for match in _SENTINEL_PATTERN.finditer(hidden_source):
            sentinel = match.group(1)
            assert sentinel not in serialized, (task.manifest.task_id, hidden_file.name, sentinel)


def test_generation_context_excludes_hidden_content_for_tasks_with_starter(task: LoadedTask) -> None:
    """If the task has a starter, freeze only spec + starter + visible tests.

    If it has no starter, generation must refuse rather than falling back
    to hidden tests or the tracked candidate.
    """

    from experiment.candidate_models import CandidateGenerationError, CandidateGenerationRequest
    from experiment.candidate_prompts import (
        build_candidate_generation_context,
        build_generation_prompt,
    )

    if task.starter_path is None:
        with pytest.raises(CandidateGenerationError, match="no starter"):
            build_candidate_generation_context(task)
        return

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
            random_seed=0,
        )
    )

    for blob in (serialized, prompt.text):
        assert task.hidden_tests_path.name not in blob
        for hidden_file in hidden_files:
            assert hidden_file.name not in blob, (task.manifest.task_id, hidden_file.name)
            hidden_source = hidden_file.read_text(encoding="utf-8")
            assert hidden_source not in blob
            for match in _SENTINEL_PATTERN.finditer(hidden_source):
                assert match.group(1) not in blob
