"""Tests for two narrowly scoped Milestone 7 hardening changes:

1. Bounded, deterministic visible-test feedback formatting for prompts
   (``experiment.candidate_prompts.format_visible_feedback_for_prompt``).
2. Secret-free, minimal subprocess environments for generated-candidate
   test execution (``experiment.runner.PytestRunner.run_visible_workspace``).

No test here makes a network or paid API call. No test changes the
experiment protocol, hidden-test boundary, model behavior, frontend, or
database.
"""

from __future__ import annotations

import textwrap

import pytest

from experiment.candidate_models import VisibleTestFeedback
from experiment.candidate_prompts import (
    MAX_VISIBLE_FEEDBACK_CHARS,
    _bound_text_block,
    format_visible_feedback_for_prompt,
)
from experiment.runner import PytestRunner, _minimal_subprocess_env

# --------------------------------------------------------------------------
# 1. Bounded visible-test feedback
# --------------------------------------------------------------------------


def _feedback(*, stdout: str, stderr: str = "", passed_count: int = 0, failed_count: int = 1) -> VisibleTestFeedback:
    return VisibleTestFeedback(
        passed=failed_count == 0,
        timed_out=False,
        exit_code=0 if failed_count == 0 else 1,
        duration_seconds=0.42,
        stdout=stdout,
        stderr=stderr,
        passed_count=passed_count,
        failed_count=failed_count,
    )


def test_ordinary_output_is_reproduced_unchanged() -> None:
    stdout = "collected 3 items\n\ntest_visible.py::test_a PASSED\n1 passed in 0.01s\n"
    stderr = ""
    feedback = _feedback(stdout=stdout, stderr=stderr, passed_count=1, failed_count=0)

    rendered = format_visible_feedback_for_prompt(feedback)

    assert "passed: True" in rendered
    assert "passed_count: 1" in rendered
    assert "failed_count: 0" in rendered
    assert stdout in rendered
    assert "TRUNCATED" not in rendered
    assert len(rendered) <= MAX_VISIBLE_FEEDBACK_CHARS


def test_oversized_stdout_is_bounded_and_marked() -> None:
    head_marker = "BEGIN_OUTPUT_MARKER_123"
    tail_marker = "END_OUTPUT_MARKER_456"
    huge_middle = "F" * 50_000
    stdout = f"{head_marker}\n{huge_middle}\n{tail_marker}\n"
    feedback = _feedback(stdout=stdout, passed_count=0, failed_count=45)

    rendered = format_visible_feedback_for_prompt(feedback)

    # Bounded: never exceeds the documented maximum.
    assert len(rendered) <= MAX_VISIBLE_FEEDBACK_CHARS
    # Much smaller than the raw input -- actual truncation occurred.
    assert len(rendered) < len(stdout)
    # Marked: an explicit truncation marker is present.
    assert "TRUNCATED" in rendered
    # Pass/fail counts are always preserved verbatim.
    assert "passed_count: 0" in rendered
    assert "failed_count: 45" in rendered
    # Useful beginning/end information is preserved even though the huge
    # middle was removed.
    assert head_marker in rendered
    assert tail_marker in rendered


def test_oversized_stderr_is_also_bounded_and_marked() -> None:
    stderr = "E" * 50_000
    feedback = _feedback(stdout="ok", stderr=stderr, passed_count=1, failed_count=0)

    rendered = format_visible_feedback_for_prompt(feedback)

    assert len(rendered) <= MAX_VISIBLE_FEEDBACK_CHARS
    assert len(rendered) < len(stderr)
    assert "TRUNCATED" in rendered


def test_custom_max_chars_is_honored() -> None:
    stdout = "x" * 10_000
    feedback = _feedback(stdout=stdout, passed_count=0, failed_count=1)

    rendered = format_visible_feedback_for_prompt(feedback, max_chars=500)

    assert len(rendered) <= 500
    assert "passed_count: 0" in rendered


def test_bound_text_block_is_deterministic_and_never_exceeds_budget() -> None:
    text = "abc123" * 5_000  # 30,000 chars
    budget = 1_000

    first = _bound_text_block(text, budget)
    second = _bound_text_block(text, budget)

    assert first == second  # deterministic
    assert len(first) <= budget
    assert "TRUNCATED" in first
    assert first.startswith(text[:1])  # beginning preserved
    assert first.endswith(text[-1:])  # end preserved


def test_bound_text_block_returns_unchanged_text_under_budget() -> None:
    text = "short and simple output\n"
    assert _bound_text_block(text, 10_000) == text


def test_bound_text_block_handles_zero_or_negative_budget() -> None:
    assert _bound_text_block("anything", 0) == ""
    assert _bound_text_block("anything", -5) == ""


def test_full_raw_output_is_not_the_bounded_representation_object() -> None:
    """The bounded formatter must not mutate the feedback it was given.

    The full raw stdout/stderr on ``VisibleTestFeedback`` (as stored in a
    saved ``CandidateAttempt`` artifact) is untouched by prompt rendering.
    """

    stdout = "z" * 50_000
    feedback = _feedback(stdout=stdout, passed_count=0, failed_count=1)

    format_visible_feedback_for_prompt(feedback)

    assert feedback.stdout == stdout
    assert len(feedback.stdout) == 50_000


def test_no_markdown_fences_requested_but_feedback_block_is_plain_text() -> None:
    # Sanity check that bounding doesn't itself introduce fenced code
    # blocks around feedback (only the source/starter sections use fences).
    feedback = _feedback(stdout="normal output", passed_count=1, failed_count=0)
    rendered = format_visible_feedback_for_prompt(feedback)
    assert "```" not in rendered


# --------------------------------------------------------------------------
# 2. Sanitized candidate subprocess environment
# --------------------------------------------------------------------------


def test_minimal_subprocess_env_excludes_arbitrary_and_known_secret_vars(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("OPENAI_CANDIDATE_MODEL", "gpt-should-not-leak")
    monkeypatch.setenv("OPENAI_REVIEWER_MODEL", "gpt-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-should-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgres://should-not-leak")
    monkeypatch.setenv("MY_ARBITRARY_SECRET_TOKEN", "top-secret-should-not-leak")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    env = _minimal_subprocess_env(workspace=workspace)

    for name in (
        "OPENAI_API_KEY",
        "OPENAI_CANDIDATE_MODEL",
        "OPENAI_REVIEWER_MODEL",
        "ANTHROPIC_API_KEY",
        "DATABASE_URL",
        "MY_ARBITRARY_SECRET_TOKEN",
    ):
        assert name not in env

    assert env["PYTHONPATH"] == str(workspace)


def test_minimal_subprocess_env_only_copies_the_documented_allowlist(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("SOME_UNRELATED_BUILD_FLAG", "should-not-be-copied")

    env = _minimal_subprocess_env(workspace=tmp_path)

    assert env["PATH"] == "/usr/bin:/bin"
    assert "SOME_UNRELATED_BUILD_FLAG" not in env
    from experiment.runner import _CANDIDATE_SUBPROCESS_ENV_ALLOWLIST

    assert set(env) <= set(_CANDIDATE_SUBPROCESS_ENV_ALLOWLIST) | {"PYTHONPATH"}


def test_candidate_subprocess_environment_excludes_secrets_end_to_end(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a real pytest subprocess run via run_visible_workspace
    must not see secret/unrelated environment variables from this process,
    even though this test explicitly sets them here first."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-should-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgres://should-not-leak")
    monkeypatch.setenv("MY_ARBITRARY_SECRET_TOKEN", "top-secret-should-not-leak")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")

    tests_dir = workspace / "visible_tests"
    tests_dir.mkdir()
    (tests_dir / "test_env_is_clean.py").write_text(
        textwrap.dedent(
            """
            import os

            SECRET_NAMES = [
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "DATABASE_URL",
                "MY_ARBITRARY_SECRET_TOKEN",
            ]


            def test_no_secret_env_vars_are_visible_here():
                leaked = [name for name in SECRET_NAMES if name in os.environ]
                assert leaked == [], f"leaked secret env vars: {leaked}"


            def test_module_is_importable_from_workspace():
                from mod import VALUE
                assert VALUE == 1
            """
        ),
        encoding="utf-8",
    )

    runner = PytestRunner()
    result = runner.run_visible_workspace(tests_dir, timeout_seconds=15, workspace=workspace)

    assert result.timed_out is False, result.stdout + result.stderr
    assert result.passed is True, result.stdout + result.stderr
    assert result.passed_count == 2
    assert result.failed_count == 0
    # The secret values must never appear in captured output either.
    combined = result.stdout + result.stderr
    assert "sk-should-not-leak" not in combined
    assert "anth-should-not-leak" not in combined
    assert "should-not-leak" not in combined
    assert "top-secret-should-not-leak" not in combined


def test_run_visible_workspace_still_finds_pythonpath_module(tmp_path) -> None:
    """Sanity: the minimal environment is still sufficient to run pytest
    and import the candidate module (PYTHONPATH is preserved/set)."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "mod.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    tests_dir = workspace / "visible_tests"
    tests_dir.mkdir()
    (tests_dir / "test_add.py").write_text(
        textwrap.dedent(
            """
            from mod import add


            def test_add():
                assert add(2, 3) == 5
            """
        ),
        encoding="utf-8",
    )

    runner = PytestRunner()
    result = runner.run_visible_workspace(tests_dir, timeout_seconds=15, workspace=workspace)

    assert result.passed is True, result.stdout + result.stderr
    assert result.passed_count == 1
