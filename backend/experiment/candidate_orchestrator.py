"""Bounded, hidden-blind coding-agent candidate generation.

Exact step order:

    1. Load the task and freeze specification, starter, and visible-test
       source. Hidden tests are never read.
    2. Build the initial generator prompt from only those frozen inputs.
    3. Ask the generator for a complete implementation.
    4. Materialize that implementation in a temporary isolated workspace.
    5. Write only the frozen visible tests into that workspace.
    6. Run visible tests against the workspace candidate.
    7. If visible tests fail and attempts remain: send the prior source
       plus visible-test stdout/stderr and pass/fail counts, and ask for
       a corrected complete implementation.
    8. Stop when all visible tests pass, max attempts is reached, or a
       timeout / provider / validation failure occurs.
    9. Persist every attempt's source hash, visible result, timing, token
       metadata, and summary.
   10. Persist the final candidate source and metadata atomically.

This module never calls ``PytestRunner.run_hidden`` and never reads
``LoadedTask.hidden_tests_path``. The workspace is never a copy of the
task directory. Provider output cannot choose a filesystem path: source
is always written to the task's required module filename.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from .candidate_generator import CandidateGenerator, ProvidesResponseMetadata
from .candidate_models import (
    DEFAULT_SEED_SEMANTICS,
    CandidateArtifactMetadata,
    CandidateAttempt,
    CandidateGenerationError,
    CandidateGenerationRequest,
    StopReason,
    VisibleTestFeedback,
)
from .candidate_prompts import (
    CANDIDATE_PROMPT_VERSION,
    CandidateGenerationContext,
    build_candidate_generation_context,
)
from .loader import TaskLoader
from .models import TestSuiteResult
from .runner import PytestRunner

DEFAULT_MAX_ATTEMPTS = 3


def default_candidates_dir() -> Path:
    """``backend/data/candidates``, resolved relative to this file."""

    return Path(__file__).resolve().parent.parent / "data" / "candidates"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_visible_tests(visible_tests_source: dict[str, str]) -> str:
    hasher = hashlib.sha256()
    for name in sorted(visible_tests_source):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(visible_tests_source[name].encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _feedback_from_suite(result: TestSuiteResult) -> VisibleTestFeedback:
    return VisibleTestFeedback(
        passed=result.passed,
        timed_out=result.timed_out,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        stdout=result.stdout,
        stderr=result.stderr,
        passed_count=result.passed_count,
        failed_count=result.failed_count,
    )


def _write_candidate_source(directory: Path, filename: str, source: str) -> Path:
    """Write ``source`` only to ``directory / filename`` (a bare filename).

    Rejects any filename that could escape ``directory``. Provider output
    is never used as a path.
    """

    if "/" in filename or "\\" in filename or filename in {".", ".."} or not filename:
        raise CandidateGenerationError(
            f"Refusing to write candidate source to non-bare filename {filename!r}."
        )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    resolved = target.resolve()
    if resolved.parent != directory.resolve():
        raise CandidateGenerationError(
            "Refusing to write candidate source outside the target directory."
        )
    target.write_text(source, encoding="utf-8")
    return target


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _stop_reason_for_exception(exc: Exception) -> StopReason:
    name = type(exc).__name__
    if "Timeout" in name:
        return "timeout"
    if "Output" in name or isinstance(exc, (ValidationError, ValueError)):
        return "validation_error"
    return "provider_error"


class CandidateGenerationOrchestrator:
    """Runs the bounded generate / visible-test / revise loop.

    ``generator_factory`` must be a zero-argument callable that returns a
    :class:`CandidateGenerator`. It is invoked once per attempt so OpenAI
    calls never share conversation state. A mock that returns a sequence
    of sources should be returned by a factory that reuses one instance.
    """

    def __init__(
        self,
        *,
        generator_factory: Callable[[], CandidateGenerator],
        tasks_root: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        runner: Optional[PytestRunner] = None,
    ) -> None:
        self._generator_factory = generator_factory
        self._tasks_root = tasks_root
        self._output_dir = Path(output_dir) if output_dir is not None else default_candidates_dir()
        self._runner = runner or PytestRunner()

    def artifact_dir(self, task_id: str, candidate_id: uuid.UUID) -> Path:
        return self._output_dir / task_id / str(candidate_id)

    def run(
        self,
        *,
        task_id: str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        random_seed: int,
    ) -> CandidateArtifactMetadata:
        if max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")

        candidate_id = uuid.uuid4()
        artifact_dir = self.artifact_dir(task_id, candidate_id)
        started_at = _utcnow()

        task = TaskLoader(tasks_root=self._tasks_root).load(task_id)
        context = build_candidate_generation_context(task)
        # ``task`` is not used after this. Generation continues from the
        # frozen context only -- never from hidden-test paths.

        probe = self._generator_factory()
        metadata = CandidateArtifactMetadata(
            candidate_id=candidate_id,
            task_id=context.task_id,
            provider=probe.provider,
            model=probe.model,
            prompt_version=CANDIDATE_PROMPT_VERSION,
            random_seed=random_seed,
            max_attempts=max_attempts,
            status="failed",
            stop_reason="provider_error",
            required_module_filename=context.required_module_filename,
            specification_sha256=_sha256_text(context.specification),
            starter_sha256=_sha256_text(context.starter_source),
            visible_tests_sha256=_sha256_visible_tests(context.visible_tests_source),
            visible_tests_passed=False,
            attempt_count=0,
            model_sampling_deterministic=(probe.provider == "mock"),
            seed_semantics=DEFAULT_SEED_SEMANTICS,
            started_at=started_at,
        )
        self._persist(artifact_dir, context.required_module_filename, None, metadata)

        attempts: list[CandidateAttempt] = []
        last_source: Optional[str] = None
        last_feedback: Optional[VisibleTestFeedback] = None
        error_message: Optional[str] = None
        stop_reason: StopReason = "max_attempts_reached"

        for attempt_number in range(1, max_attempts + 1):
            request = CandidateGenerationRequest(
                task_id=context.task_id,
                specification=context.specification,
                starter_source=context.starter_source,
                visible_tests_source=dict(context.visible_tests_source),
                required_module_filename=context.required_module_filename,
                attempt_number=attempt_number,
                max_attempts=max_attempts,
                random_seed=random_seed,
                previous_source=last_source,
                visible_feedback=last_feedback,
            )

            generator = self._generator_factory()
            start = time.perf_counter()
            try:
                output = generator.generate(request)
            except Exception as exc:  # noqa: BLE001 -- convert to a recorded stop
                error_message = str(exc)
                stop_reason = _stop_reason_for_exception(exc)
                break
            latency_seconds = time.perf_counter() - start

            last_source = output.source
            try:
                visible_result = self._run_visible_in_isolated_workspace(context, output.source)
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc)
                stop_reason = _stop_reason_for_exception(exc)
                break

            feedback = _feedback_from_suite(visible_result)
            last_feedback = feedback

            attempt_kwargs: dict[str, object] = dict(
                attempt_number=attempt_number,
                source_sha256=_sha256_text(output.source),
                summary=output.summary,
                visible_result=feedback,
                latency_seconds=latency_seconds,
                timestamp=_utcnow(),
            )
            if isinstance(generator, ProvidesResponseMetadata):
                attempt_kwargs["response_id"] = generator.last_response_id
                attempt_kwargs["input_tokens"] = generator.last_input_tokens
                attempt_kwargs["output_tokens"] = generator.last_output_tokens

            attempts.append(CandidateAttempt(**attempt_kwargs))
            metadata = self._metadata_from_state(
                base=metadata,
                attempts=attempts,
                last_source=last_source,
                last_feedback=last_feedback,
                status="failed",
                stop_reason="max_attempts_reached",
                completed_at=None,
                error=None,
            )
            self._persist(artifact_dir, context.required_module_filename, last_source, metadata)

            if feedback.passed:
                stop_reason = "visible_tests_passed"
                break
            if feedback.timed_out and attempt_number == max_attempts:
                stop_reason = "timeout"
                break
            if attempt_number == max_attempts:
                stop_reason = "timeout" if feedback.timed_out else "max_attempts_reached"
                break

        completed_at = _utcnow()
        status = "completed" if stop_reason == "visible_tests_passed" else "failed"
        metadata = self._metadata_from_state(
            base=metadata,
            attempts=attempts,
            last_source=last_source,
            last_feedback=last_feedback,
            status=status,
            stop_reason=stop_reason,
            completed_at=completed_at,
            error=error_message,
        )
        self._persist(artifact_dir, context.required_module_filename, last_source, metadata)
        return metadata

    def _run_visible_in_isolated_workspace(
        self, context: CandidateGenerationContext, source: str
    ) -> TestSuiteResult:
        """Materialize candidate + visible tests only, then run visible tests.

        Never copies the task directory (which would include hidden tests).
        Never writes outside the temporary workspace.
        """

        with tempfile.TemporaryDirectory(prefix="verigate-candidate-") as tmp:
            workspace = Path(tmp)
            _write_candidate_source(workspace, context.required_module_filename, source)

            tests_dir = workspace / "visible_tests"
            tests_dir.mkdir()
            for name, test_source in context.visible_tests_source.items():
                # Visible-test filenames come from glob("*.py") of the
                # visible-tests directory -- already bare names.
                if "/" in name or "\\" in name or name in {".", ".."}:
                    raise CandidateGenerationError(
                        f"Refusing to write visible test with non-bare name {name!r}."
                    )
                (tests_dir / name).write_text(test_source, encoding="utf-8")

            return self._runner.run_visible_workspace(
                tests_dir,
                context.timeout_seconds,
                workspace=workspace,
            )

    def _metadata_from_state(
        self,
        *,
        base: CandidateArtifactMetadata,
        attempts: list[CandidateAttempt],
        last_source: Optional[str],
        last_feedback: Optional[VisibleTestFeedback],
        status: str,
        stop_reason: StopReason,
        completed_at: Optional[datetime],
        error: Optional[str],
    ) -> CandidateArtifactMetadata:
        input_tokens = [a.input_tokens for a in attempts if a.input_tokens is not None]
        output_tokens = [a.output_tokens for a in attempts if a.output_tokens is not None]
        latencies = [a.latency_seconds for a in attempts if a.latency_seconds is not None]
        return base.model_copy(
            update={
                "status": status,
                "stop_reason": stop_reason,
                "final_source_sha256": _sha256_text(last_source) if last_source is not None else None,
                "visible_tests_passed": bool(last_feedback.passed) if last_feedback else False,
                "visible_passed_count": last_feedback.passed_count if last_feedback else None,
                "visible_failed_count": last_feedback.failed_count if last_feedback else None,
                "attempt_count": len(attempts),
                "attempts": list(attempts),
                "total_input_tokens": sum(input_tokens) if input_tokens else None,
                "total_output_tokens": sum(output_tokens) if output_tokens else None,
                "total_latency_seconds": sum(latencies) if latencies else None,
                "completed_at": completed_at,
                "error": error,
            }
        )

    def _persist(
        self,
        artifact_dir: Path,
        filename: str,
        source: Optional[str],
        metadata: CandidateArtifactMetadata,
    ) -> None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if source is not None:
            _write_candidate_source(artifact_dir, filename, source)
        payload = json.dumps(metadata.model_dump(mode="json"), indent=2)
        _atomic_write_text(artifact_dir / "metadata.json", payload)
