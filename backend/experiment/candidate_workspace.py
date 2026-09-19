"""Isolated temporary workspaces for evaluating a candidate's source.

Materializes a candidate source plus exactly one test suite (visible OR
hidden, never both) into a fresh temporary directory, then runs that
suite against it via :class:`experiment.runner.PytestRunner`.

Used by candidate-aware reviewer experiments
(``experiment.orchestrator.ExperimentOrchestrator``) to re-run visible
tests and (only after every reviewer observation has completed) hidden
tests against a supplied, already-verified candidate source -- without
ever touching the tracked task directory (e.g.
``backend/tasks/json_parser/json_parser.py``).

Guarantees:

- A fresh temporary directory is used for every call; nothing persists
  after the call returns.
- Visible and hidden tests are never placed in the same workspace: each
  call writes exactly one suite's test files, under a directory named
  for that suite (``visible_tests/`` or ``hidden_tests/``).
- The tracked task directory is never read from or written to here --
  callers must pass in already-frozen candidate/test source text.
- Test/candidate filenames are validated as bare filenames (no path
  separators, no ``.``/``..``) before being written, so nothing can
  escape the workspace.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from .candidate_models import CandidateGenerationError
from .models import TestSuiteResult
from .runner import PytestRunner

Suite = Literal["visible", "hidden"]


def _require_bare_filename(name: str, *, what: str) -> None:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise CandidateGenerationError(
            f"Refusing to write {what} with non-bare filename {name!r}."
        )


def _write_candidate_source(directory: Path, filename: str, source: str) -> Path:
    """Write ``source`` only to ``directory / filename`` (a bare filename)."""

    _require_bare_filename(filename, what="candidate source")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    resolved = target.resolve()
    if resolved.parent != directory.resolve():
        raise CandidateGenerationError(
            "Refusing to write candidate source outside the target directory."
        )
    target.write_text(source, encoding="utf-8")
    return target


def _write_test_files(tests_dir: Path, tests_source: dict[str, str]) -> None:
    tests_dir.mkdir(parents=True, exist_ok=True)
    for name, source in tests_source.items():
        _require_bare_filename(name, what="test file")
        (tests_dir / name).write_text(source, encoding="utf-8")


def run_candidate_against_suite(
    *,
    candidate_source: str,
    required_module_filename: str,
    tests_source: dict[str, str],
    suite: Suite,
    timeout_seconds: float,
    runner: PytestRunner,
) -> TestSuiteResult:
    """Materialize ``candidate_source`` + exactly one suite's tests, then run it.

    Builds a fresh temporary workspace containing only the candidate
    source (written to ``required_module_filename``) and the test files
    in ``tests_source``, under a ``<suite>_tests/`` subdirectory. Never
    writes visible and hidden tests into the same workspace call, and
    never touches the tracked task directory.
    """

    with tempfile.TemporaryDirectory(prefix=f"verigate-candidate-{suite}-") as tmp:
        workspace = Path(tmp)
        _write_candidate_source(workspace, required_module_filename, candidate_source)

        tests_dir = workspace / f"{suite}_tests"
        _write_test_files(tests_dir, tests_source)

        if suite == "visible":
            return runner.run_visible_workspace(tests_dir, timeout_seconds, workspace=workspace)
        return runner.run_hidden_workspace(tests_dir, timeout_seconds, workspace=workspace)
