"""Safe loading and verification of saved candidate-generation artifacts.

Loads a previously generated candidate (see
``experiment.candidate_orchestrator.CandidateGenerationOrchestrator``) from
disk for use in a candidate-aware reviewer experiment
(``experiment.orchestrator.ExperimentOrchestrator``). This is a read-only,
defense-in-depth loader:

1. ``candidate_id`` is validated as a UUID -- never treated as an
   arbitrary path fragment.
2. The expected directory is resolved strictly under the configured
   candidate root (``backend/data/candidates/<task_id>/<candidate_id>/``
   by default).
3. Path traversal, unexpected symlinks, missing files, malformed
   metadata, and task-ID mismatches are all rejected with a safe,
   secret-free error.
4. Metadata is validated through the existing
   :class:`experiment.candidate_models.CandidateArtifactMetadata` model.
5. The candidate source filename must match the task manifest's
   candidate filename.
6. The candidate source file's SHA-256 must match the hash recorded in
   metadata (protects against tampering/corruption).
7. Only artifacts with ``status="completed"``,
   ``stop_reason="visible_tests_passed"``, and ``visible_tests_passed``
   true are considered usable for a reviewer experiment.
8. No path supplied *by* metadata is ever trusted as a filesystem
   location to open -- the source file location is always computed by
   this loader from the harness-controlled artifact directory and the
   task manifest's own candidate filename.

Never reads ``LoadedTask.hidden_tests_path`` and never runs any tests.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from pydantic import ValidationError

from .candidate_models import CandidateArtifactMetadata
from .candidate_orchestrator import default_candidates_dir
from .loader import LoadedTask


class CandidateArtifactLoadError(RuntimeError):
    """Raised when a saved candidate artifact cannot be safely loaded/verified.

    Every message on this type is safe to print to a CLI caller: none of
    them ever include a secret, and none of them echo an untrusted path
    string directly (only well-formed diagnostic text derived from a
    validated UUID, task id, or bare filename).
    """


@dataclass(frozen=True)
class LoadedCandidate:
    """A verified, loaded candidate ready to be used in a reviewer experiment."""

    candidate_id: uuid.UUID
    task_id: str
    metadata: CandidateArtifactMetadata
    source: str
    required_module_filename: str
    artifact_dir: Path


class CandidateArtifactLoader:
    """Loads and verifies one candidate artifact under a configured root."""

    def __init__(self, candidates_root: Optional[Path] = None) -> None:
        self._candidates_root = (
            Path(candidates_root) if candidates_root is not None else default_candidates_dir()
        )

    def load(self, task: LoadedTask, candidate_id: Union[str, uuid.UUID]) -> LoadedCandidate:
        task_id = task.manifest.task_id

        # 1. Validate candidate_id as a UUID, not an arbitrary path.
        validated_id = self._validate_uuid(candidate_id)

        # 2. Resolve the expected directory strictly under the configured
        # candidate root.
        candidates_root = self._candidates_root.resolve()
        task_dir = candidates_root / task_id
        raw_candidate_dir = task_dir / str(validated_id)

        # 3a. The candidate directory itself must not be a symlink (checked
        # before resolving, since `.resolve()` would otherwise silently
        # follow it).
        if raw_candidate_dir.is_symlink():
            raise CandidateArtifactLoadError(
                "Refusing to load candidate artifact: artifact directory is a symlink."
            )

        if not raw_candidate_dir.is_dir():
            raise CandidateArtifactLoadError(
                f"No candidate artifact found for task {task_id!r} with id {validated_id}."
            )

        # 3b. Reject traversal: if any ancestor component (e.g. a symlinked
        # `task_dir`) caused resolution to land somewhere other than the
        # exact expected child of the resolved `task_dir`, refuse it.
        expected_dir = raw_candidate_dir.resolve()
        if expected_dir.parent != task_dir.resolve():
            raise CandidateArtifactLoadError(
                "Refusing to load candidate artifact: resolved path escapes "
                "the expected candidate directory."
            )

        metadata_path = expected_dir / "metadata.json"
        self._reject_symlink(metadata_path)
        if not metadata_path.is_file():
            raise CandidateArtifactLoadError(
                f"Missing metadata.json for candidate {validated_id} under task {task_id!r}."
            )

        # 4. Validate metadata through the existing Pydantic model.
        try:
            raw_metadata = metadata_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CandidateArtifactLoadError(
                f"Could not read metadata.json for candidate {validated_id}."
            ) from exc

        try:
            metadata = CandidateArtifactMetadata.model_validate_json(raw_metadata)
        except ValidationError as exc:
            raise CandidateArtifactLoadError(
                f"Malformed candidate metadata for {validated_id}: {exc}"
            ) from exc

        if metadata.candidate_id != validated_id:
            raise CandidateArtifactLoadError(
                f"Candidate metadata candidate_id ({metadata.candidate_id}) does not "
                f"match the requested id ({validated_id})."
            )
        if metadata.task_id != task_id:
            raise CandidateArtifactLoadError(
                f"Candidate {validated_id} was generated for task "
                f"{metadata.task_id!r}, not {task_id!r}."
            )

        # 5. Verify the candidate source filename matches the task manifest's.
        expected_filename = Path(task.manifest.paths.candidate).name
        if metadata.required_module_filename != expected_filename:
            raise CandidateArtifactLoadError(
                "Candidate metadata's required_module_filename "
                f"({metadata.required_module_filename!r}) does not match the task "
                f"manifest's candidate filename ({expected_filename!r})."
            )

        # 8. Never trust a path supplied by metadata: the source file's
        # location is always computed from the harness-controlled
        # artifact directory plus the task manifest's own filename, never
        # from a path-like value read out of metadata.json.
        source_path = expected_dir / expected_filename
        self._reject_symlink(source_path)
        if not source_path.is_file():
            raise CandidateArtifactLoadError(
                f"Missing candidate source file {expected_filename!r} for "
                f"candidate {validated_id}."
            )

        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CandidateArtifactLoadError(
                f"Could not read candidate source for {validated_id}."
            ) from exc

        # 6. Recompute the candidate source SHA-256 and require it to match.
        actual_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if metadata.final_source_sha256 is None:
            raise CandidateArtifactLoadError(
                f"Candidate {validated_id} metadata has no final_source_sha256 recorded."
            )
        if actual_sha256 != metadata.final_source_sha256:
            raise CandidateArtifactLoadError(
                f"Candidate {validated_id} source file does not match its recorded "
                "SHA-256 hash; refusing to use a tampered or corrupted artifact."
            )

        # 7. Require generation status/stop_reason/visible_tests_passed.
        if metadata.status != "completed":
            raise CandidateArtifactLoadError(
                f"Candidate {validated_id} generation status is {metadata.status!r}, "
                "not 'completed'; not usable for a reviewer experiment."
            )
        if metadata.stop_reason != "visible_tests_passed":
            raise CandidateArtifactLoadError(
                f"Candidate {validated_id} stop_reason is {metadata.stop_reason!r}, "
                "not 'visible_tests_passed'; not usable for a reviewer experiment."
            )
        if not metadata.visible_tests_passed:
            raise CandidateArtifactLoadError(
                f"Candidate {validated_id} did not pass its visible tests; "
                "not usable for a reviewer experiment."
            )

        return LoadedCandidate(
            candidate_id=validated_id,
            task_id=task_id,
            metadata=metadata,
            source=source,
            required_module_filename=expected_filename,
            artifact_dir=expected_dir,
        )

    @staticmethod
    def _validate_uuid(candidate_id: Union[str, uuid.UUID]) -> uuid.UUID:
        if isinstance(candidate_id, uuid.UUID):
            return candidate_id
        if not isinstance(candidate_id, str):
            raise CandidateArtifactLoadError("candidate_id must be a UUID string.")
        try:
            return uuid.UUID(candidate_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise CandidateArtifactLoadError(
                f"candidate_id is not a valid UUID: {candidate_id!r}"
            ) from exc

    @staticmethod
    def _reject_symlink(path: Path) -> None:
        if path.is_symlink():
            raise CandidateArtifactLoadError(
                f"Refusing to load candidate artifact: {path.name} is a symlink."
            )
