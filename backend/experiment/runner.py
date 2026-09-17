"""Subprocess-based pytest runner for a task's visible/hidden suites.

Each suite runs in its own ``pytest`` subprocess so that a crashing or
hanging candidate implementation can never take down the harness process
itself; on timeout the subprocess is killed and the result is reported as
a clean, structured failure rather than an unhandled exception.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

from .loader import LoadedTask
from .models import TestSuiteResult

_PASSED_RE = re.compile(r"(\d+) passed")
_FAILED_RE = re.compile(r"(\d+) failed")
_ERROR_RE = re.compile(r"(\d+) error")


class PytestRunner:
    """Runs a task's visible or hidden pytest suite in an isolated subprocess."""

    def run_visible(self, task: LoadedTask) -> TestSuiteResult:
        return self._run(task, task.visible_tests_path, "visible")

    def run_hidden(self, task: LoadedTask) -> TestSuiteResult:
        return self._run(task, task.hidden_tests_path, "hidden")

    def _run(self, task: LoadedTask, test_path: Path, suite: str) -> TestSuiteResult:
        command = [
            sys.executable,
            "-m",
            "pytest",
            str(test_path),
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ]
        timeout = task.manifest.timeout_seconds
        start = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start
            stdout = _as_text(exc.stdout)
            stderr = _as_text(exc.stderr) or f"Timed out after {timeout}s"
            return TestSuiteResult(
                suite=suite,
                passed=False,
                timed_out=True,
                exit_code=None,
                duration_seconds=duration,
                stdout=stdout,
                stderr=stderr,
                passed_count=0,
                failed_count=0,
            )

        duration = time.monotonic() - start
        passed_count, failed_count = _parse_summary(proc.stdout)
        return TestSuiteResult(
            suite=suite,
            passed=proc.returncode == 0,
            timed_out=False,
            exit_code=proc.returncode,
            duration_seconds=duration,
            stdout=proc.stdout,
            stderr=proc.stderr,
            passed_count=passed_count,
            failed_count=failed_count,
        )


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_summary(stdout: str) -> tuple[int, int]:
    """Best-effort parse of pytest's final summary line for convenience counts.

    ``exit_code`` / ``passed`` on :class:`TestSuiteResult` remain the
    authoritative pass/fail signal; these counts are additional structured
    detail only.
    """

    passed_match = _PASSED_RE.search(stdout)
    failed_match = _FAILED_RE.search(stdout)
    error_match = _ERROR_RE.search(stdout)
    passed = int(passed_match.group(1)) if passed_match else 0
    failed = int(failed_match.group(1)) if failed_match else 0
    errored = int(error_match.group(1)) if error_match else 0
    return passed, failed + errored
