"""Subprocess-based pytest runner for a task's visible/hidden suites.

Each suite runs in its own ``pytest`` subprocess so that a crashing or
hanging candidate implementation can never take down the harness process
itself; on timeout the subprocess is killed and the result is reported as
a clean, structured failure rather than an unhandled exception.

``run_visible``/``run_hidden`` (for the tracked task's own known-correct
candidate) run with this process's full inherited environment, matching
the harness's pre-existing behavior. ``run_visible_workspace`` -- used
only for hidden-blind coding-agent candidate generation, where the
executed code was written by an untrusted generator -- instead runs with
a deliberately minimal, secret-free environment; see
:func:`_minimal_subprocess_env`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .loader import LoadedTask
from .models import TestSuiteResult

_PASSED_RE = re.compile(r"(\d+) passed")
_FAILED_RE = re.compile(r"(\d+) failed")
_ERROR_RE = re.compile(r"(\d+) error")

# Environment variables that are (a) non-secret and (b) sometimes needed
# for ordinary Python/pytest subprocess execution on common platforms.
# Only names in this fixed allowlist -- and only if already present in
# this process's own environment -- are ever copied into a generated
# candidate's test subprocess. Everything else, including
# OPENAI_API_KEY/ANTHROPIC_API_KEY/DATABASE_URL and any other secret or
# unrelated variable, is never inherited.
_CANDIDATE_SUBPROCESS_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "SYSTEMROOT",  # Windows: required by Python's os/socket modules.
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
)


def _minimal_subprocess_env(*, workspace: Path) -> dict[str, str]:
    """Build a minimal, secret-free environment for a candidate test subprocess.

    Starts from nothing (not a copy of this process's environment),
    copies in only the fixed, non-secret variables in
    :data:`_CANDIDATE_SUBPROCESS_ENV_ALLOWLIST` when they are already set,
    then sets ``PYTHONPATH`` to ``workspace`` so the candidate module under
    test is imported only from the isolated workspace.

    This reduces the blast radius of secret leakage into a coding-agent
    generated candidate's own test execution. It is **not** a complete
    OS/container security sandbox: the subprocess still runs as this same
    OS user, with the same filesystem, process, and network visibility as
    this machine -- it is only the *environment variables* that are
    restricted.
    """

    env = {
        name: os.environ[name]
        for name in _CANDIDATE_SUBPROCESS_ENV_ALLOWLIST
        if name in os.environ
    }
    env["PYTHONPATH"] = str(workspace)
    return env


class PytestRunner:
    """Runs a task's visible or hidden pytest suite in an isolated subprocess."""

    def run_visible(self, task: LoadedTask) -> TestSuiteResult:
        return self._run(task.visible_tests_path, "visible", task.manifest.timeout_seconds)

    def run_hidden(self, task: LoadedTask) -> TestSuiteResult:
        return self._run(task.hidden_tests_path, "hidden", task.manifest.timeout_seconds)

    def run_visible_workspace(
        self,
        tests_dir: Path,
        timeout_seconds: float,
        *,
        workspace: Path,
    ) -> TestSuiteResult:
        """Run the visible suite from an isolated workspace.

        ``workspace`` is used as cwd and as ``PYTHONPATH`` so the candidate
        module is imported from that workspace, never from the tracked task
        directory. This method never runs hidden tests.

        The subprocess environment is deliberately minimal (see
        :func:`_minimal_subprocess_env`) rather than inherited wholesale:
        it never contains ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
        ``DATABASE_URL``, or any other secret/unrelated variable from this
        process's environment. This reduces secret exposure to
        generator-written code but is not a full OS/container sandbox --
        the subprocess still shares this machine's filesystem, process
        namespace, and network access.
        """

        env = _minimal_subprocess_env(workspace=workspace)
        return self._run(
            tests_dir,
            "visible",
            timeout_seconds,
            cwd=workspace,
            env=env,
        )

    def _run(
        self,
        test_path: Path,
        suite: str,
        timeout_seconds: float,
        *,
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
    ) -> TestSuiteResult:
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
        timeout = timeout_seconds
        start = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
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
