"""Tests for experiment.candidate_loader.CandidateArtifactLoader.

Every candidate artifact used here is a synthetic, temporary fixture
built under ``tmp_path`` -- never the real generated
``json_parser``/``3d51301c-b572-4891-9db4-84171b5b1b7c`` candidate. No
test here runs any test suite, makes a network call, or evaluates hidden
tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest
from pydantic import ValidationError

from experiment.candidate_loader import (
    CandidateArtifactLoadError,
    CandidateArtifactLoader,
)
from experiment.candidate_models import CandidateArtifactMetadata
from experiment.loader import LoadedTask, TaskLoader

TASK_ID = "toy_task"
CANDIDATE_SOURCE = "def add(a, b):\n    return a + b  # generated candidate\n"
TRACKED_REFERENCE_SOURCE = "def add(a, b):\n    return a + b  # tracked reference\n"


# --------------------------------------------------------------------------
# Fixture helpers -- all synthetic/temporary
# --------------------------------------------------------------------------


def _write_task(tasks_root: Path, task_id: str = TASK_ID) -> LoadedTask:
    task_dir = tasks_root / task_id
    (task_dir / "visible_tests").mkdir(parents=True)
    (task_dir / "hidden_tests").mkdir(parents=True)

    (task_dir / "specification.md").write_text("# Toy spec.\n", encoding="utf-8")
    (task_dir / "toy.py").write_text(TRACKED_REFERENCE_SOURCE, encoding="utf-8")
    (task_dir / "visible_tests" / "test_visible.py").write_text(
        "def test_visible():\n    assert True\n", encoding="utf-8"
    )
    (task_dir / "hidden_tests" / "test_hidden.py").write_text(
        "def test_hidden():\n    assert True\n", encoding="utf-8"
    )
    (task_dir / "manifest.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "title": "Toy",
                "language": "python",
                "timeout_seconds": 10,
                "paths": {
                    "specification": "specification.md",
                    "candidate": "toy.py",
                    "visible_tests": "visible_tests",
                    "hidden_tests": "hidden_tests",
                },
            }
        ),
        encoding="utf-8",
    )
    return TaskLoader(tasks_root=tasks_root).load(task_id)


def _write_candidate_artifact(
    candidates_root: Path,
    *,
    task_id: str = TASK_ID,
    source: str = CANDIDATE_SOURCE,
    candidate_id: Optional[uuid.UUID] = None,
    required_module_filename: str = "toy.py",
    status: str = "completed",
    stop_reason: str = "visible_tests_passed",
    visible_tests_passed: bool = True,
    write_source: bool = True,
    write_metadata: bool = True,
    final_source_sha256: Optional[str] = None,
) -> uuid.UUID:
    """Build one synthetic candidate artifact directory and return its id."""

    candidate_id = candidate_id if candidate_id is not None else uuid.uuid4()
    artifact_dir = candidates_root / task_id / str(candidate_id)
    artifact_dir.mkdir(parents=True)

    if write_source:
        (artifact_dir / required_module_filename).write_text(source, encoding="utf-8")

    if write_metadata:
        now = datetime.now(timezone.utc)
        sha = (
            final_source_sha256
            if final_source_sha256 is not None
            else hashlib.sha256(source.encode("utf-8")).hexdigest()
        )
        metadata = CandidateArtifactMetadata(
            candidate_id=candidate_id,
            task_id=task_id,
            provider="mock",
            model="mock-deterministic-v1",
            prompt_version="v1",
            random_seed=42,
            max_attempts=3,
            status=status,  # type: ignore[arg-type]
            stop_reason=stop_reason,  # type: ignore[arg-type]
            required_module_filename=required_module_filename,
            specification_sha256="0" * 64,
            starter_sha256="1" * 64,
            visible_tests_sha256="2" * 64,
            final_source_sha256=sha,
            visible_tests_passed=visible_tests_passed,
            visible_passed_count=1,
            visible_failed_count=0,
            attempt_count=1,
            attempts=[],
            model_sampling_deterministic=True,
            seed_semantics="test seed semantics",
            started_at=now,
            completed_at=now,
        )
        (artifact_dir / "metadata.json").write_text(
            json.dumps(metadata.model_dump(mode="json"), indent=2), encoding="utf-8"
        )

    return candidate_id


# --------------------------------------------------------------------------
# 1. Valid artifact loads
# --------------------------------------------------------------------------


def test_valid_synthetic_artifact_loads_successfully(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root)

    loaded = CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))

    assert loaded.candidate_id == candidate_id
    assert loaded.task_id == TASK_ID
    assert loaded.source == CANDIDATE_SOURCE
    assert loaded.required_module_filename == "toy.py"
    assert loaded.artifact_dir == (candidates_root / TASK_ID / str(candidate_id)).resolve()
    assert loaded.metadata.candidate_id == candidate_id
    # Must be the generated candidate, not the tracked reference.
    assert loaded.source != TRACKED_REFERENCE_SOURCE


def test_load_accepts_uuid_object_as_well_as_string(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root)

    loaded = CandidateArtifactLoader(candidates_root=candidates_root).load(task, candidate_id)
    assert loaded.candidate_id == candidate_id


# --------------------------------------------------------------------------
# 2. Invalid UUID / path traversal rejected
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "not-a-uuid",
        "../../../etc/passwd",
        "../other-task/some-id",
        "",
        "1234",
        "3d51301c-b572-4891-9db4-84171b5b1b7c/../evil",
    ],
)
def test_invalid_uuid_or_traversal_attempt_is_rejected(tmp_path: Path, bad_id: str) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    _write_candidate_artifact(candidates_root)

    with pytest.raises(CandidateArtifactLoadError):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, bad_id)


def test_non_string_non_uuid_candidate_id_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"

    with pytest.raises(CandidateArtifactLoadError):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, 12345)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 3. Wrong task rejected
# --------------------------------------------------------------------------


def test_candidate_generated_for_a_different_task_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks", task_id=TASK_ID)
    other_task = _write_task(tmp_path / "tasks", task_id="other_task")
    candidates_root = tmp_path / "candidates"

    # Candidate metadata says task_id="other_task", but stored physically
    # under the "other_task" directory too -- request it through `task`
    # (TASK_ID) by manually placing metadata that mismatches its own
    # location is covered separately; here we place it correctly under
    # other_task and then try to load it while asserting for TASK_ID.
    candidate_id = _write_candidate_artifact(candidates_root, task_id="other_task")

    with pytest.raises(CandidateArtifactLoadError):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))

    # Sanity: it does load fine for the task it actually belongs to.
    loaded = CandidateArtifactLoader(candidates_root=candidates_root).load(
        other_task, str(candidate_id)
    )
    assert loaded.task_id == "other_task"


def test_metadata_task_id_mismatch_with_directory_location_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks", task_id=TASK_ID)
    candidates_root = tmp_path / "candidates"
    candidate_id = uuid.uuid4()

    # Physically placed under TASK_ID's directory, but its own metadata
    # claims a different task_id.
    artifact_dir = candidates_root / TASK_ID / str(candidate_id)
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "toy.py").write_text(CANDIDATE_SOURCE, encoding="utf-8")
    sha = hashlib.sha256(CANDIDATE_SOURCE.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    metadata = CandidateArtifactMetadata(
        candidate_id=candidate_id,
        task_id="some_other_task_entirely",
        provider="mock",
        model="mock-deterministic-v1",
        prompt_version="v1",
        random_seed=1,
        max_attempts=1,
        status="completed",
        stop_reason="visible_tests_passed",
        required_module_filename="toy.py",
        specification_sha256="0" * 64,
        starter_sha256="1" * 64,
        visible_tests_sha256="2" * 64,
        final_source_sha256=sha,
        visible_tests_passed=True,
        attempt_count=1,
        model_sampling_deterministic=True,
        seed_semantics="test",
        started_at=now,
        completed_at=now,
    )
    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata.model_dump(mode="json"), indent=2), encoding="utf-8"
    )

    with pytest.raises(CandidateArtifactLoadError, match="some_other_task_entirely"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


# --------------------------------------------------------------------------
# 4. Missing / malformed metadata rejected
# --------------------------------------------------------------------------


def test_missing_candidate_directory_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"

    with pytest.raises(CandidateArtifactLoadError):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(uuid.uuid4()))


def test_missing_metadata_json_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, write_metadata=False)

    with pytest.raises(CandidateArtifactLoadError, match="metadata"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


def test_malformed_metadata_json_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, write_metadata=False)
    artifact_dir = candidates_root / TASK_ID / str(candidate_id)
    (artifact_dir / "metadata.json").write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(CandidateArtifactLoadError):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


def test_metadata_failing_pydantic_schema_validation_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, write_metadata=False)
    artifact_dir = candidates_root / TASK_ID / str(candidate_id)
    # Valid JSON, but missing required fields / wrong types.
    (artifact_dir / "metadata.json").write_text(
        json.dumps({"candidate_id": str(candidate_id), "task_id": TASK_ID}), encoding="utf-8"
    )

    with pytest.raises(CandidateArtifactLoadError):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


def test_candidate_id_mismatch_between_metadata_and_directory_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    real_id = uuid.uuid4()
    # Metadata declares a *different* candidate_id than the directory name.
    different_id = uuid.uuid4()
    candidate_id = _write_candidate_artifact(candidates_root, candidate_id=real_id, write_metadata=False)
    artifact_dir = candidates_root / TASK_ID / str(candidate_id)
    now = datetime.now(timezone.utc)
    sha = hashlib.sha256(CANDIDATE_SOURCE.encode("utf-8")).hexdigest()
    metadata = CandidateArtifactMetadata(
        candidate_id=different_id,
        task_id=TASK_ID,
        provider="mock",
        model="mock-deterministic-v1",
        prompt_version="v1",
        random_seed=1,
        max_attempts=1,
        status="completed",
        stop_reason="visible_tests_passed",
        required_module_filename="toy.py",
        specification_sha256="0" * 64,
        starter_sha256="1" * 64,
        visible_tests_sha256="2" * 64,
        final_source_sha256=sha,
        visible_tests_passed=True,
        attempt_count=1,
        model_sampling_deterministic=True,
        seed_semantics="test",
        started_at=now,
        completed_at=now,
    )
    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata.model_dump(mode="json"), indent=2), encoding="utf-8"
    )

    with pytest.raises(CandidateArtifactLoadError):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(real_id))


# --------------------------------------------------------------------------
# 5. Source hash mismatch rejected
# --------------------------------------------------------------------------


def test_tampered_source_file_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root)
    artifact_dir = candidates_root / TASK_ID / str(candidate_id)
    # Tamper the source *after* metadata (with the original hash) was written.
    (artifact_dir / "toy.py").write_text(CANDIDATE_SOURCE + "\n# tampered!\n", encoding="utf-8")

    with pytest.raises(CandidateArtifactLoadError, match="SHA-256"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


def test_missing_final_source_sha256_in_metadata_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, final_source_sha256=None, write_metadata=False)
    artifact_dir = candidates_root / TASK_ID / str(candidate_id)
    now = datetime.now(timezone.utc)
    metadata = CandidateArtifactMetadata(
        candidate_id=candidate_id,
        task_id=TASK_ID,
        provider="mock",
        model="mock-deterministic-v1",
        prompt_version="v1",
        random_seed=1,
        max_attempts=1,
        status="completed",
        stop_reason="visible_tests_passed",
        required_module_filename="toy.py",
        specification_sha256="0" * 64,
        starter_sha256="1" * 64,
        visible_tests_sha256="2" * 64,
        final_source_sha256=None,
        visible_tests_passed=True,
        attempt_count=1,
        model_sampling_deterministic=True,
        seed_semantics="test",
        started_at=now,
        completed_at=now,
    )
    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata.model_dump(mode="json"), indent=2), encoding="utf-8"
    )

    with pytest.raises(CandidateArtifactLoadError, match="final_source_sha256"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


# --------------------------------------------------------------------------
# 6. Symlink escape rejected
# --------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows")
def test_symlinked_candidate_directory_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    (outside / "toy.py").write_text(CANDIDATE_SOURCE, encoding="utf-8")

    candidate_id = uuid.uuid4()
    task_dir = candidates_root / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / str(candidate_id)).symlink_to(outside, target_is_directory=True)

    with pytest.raises(CandidateArtifactLoadError, match="symlink"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows")
def test_symlinked_metadata_json_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, write_metadata=False)
    artifact_dir = candidates_root / TASK_ID / str(candidate_id)

    outside_metadata = tmp_path / "outside_metadata.json"
    outside_metadata.write_text("{}", encoding="utf-8")
    (artifact_dir / "metadata.json").symlink_to(outside_metadata)

    with pytest.raises(CandidateArtifactLoadError, match="symlink"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows")
def test_symlinked_source_file_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, write_source=False)
    artifact_dir = candidates_root / TASK_ID / str(candidate_id)

    outside_source = tmp_path / "outside_source.py"
    outside_source.write_text(CANDIDATE_SOURCE, encoding="utf-8")
    (artifact_dir / "toy.py").symlink_to(outside_source)

    with pytest.raises(CandidateArtifactLoadError, match="symlink"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


# --------------------------------------------------------------------------
# 7. Non-passing generation artifact rejected
# --------------------------------------------------------------------------


def test_status_not_completed_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, status="failed")

    with pytest.raises(CandidateArtifactLoadError, match="status"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


def test_stop_reason_not_visible_tests_passed_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, stop_reason="max_attempts_reached")

    with pytest.raises(CandidateArtifactLoadError, match="stop_reason"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


def test_visible_tests_not_passed_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, visible_tests_passed=False)

    with pytest.raises(CandidateArtifactLoadError, match="visible tests"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


# --------------------------------------------------------------------------
# Filename / missing-source-file checks
# --------------------------------------------------------------------------


def test_required_module_filename_mismatch_with_task_manifest_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, required_module_filename="wrong_name.py")

    with pytest.raises(CandidateArtifactLoadError, match="required_module_filename"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


def test_missing_source_file_is_rejected(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "tasks")
    candidates_root = tmp_path / "candidates"
    candidate_id = _write_candidate_artifact(candidates_root, write_source=False)

    with pytest.raises(CandidateArtifactLoadError, match="Missing candidate source"):
        CandidateArtifactLoader(candidates_root=candidates_root).load(task, str(candidate_id))


# --------------------------------------------------------------------------
# 8. Never trusts a source path supplied by metadata
# --------------------------------------------------------------------------


def test_loader_never_reads_metadata_supplied_path_fields(tmp_path: Path) -> None:
    """CandidateArtifactMetadata has no path-like field at all -- the model
    itself structurally forbids one (extra='forbid', and
    required_module_filename is validated as a bare filename) -- so no
    value coming out of metadata.json can ever be used as a filesystem
    path by the loader. This test pins that structural guarantee down.
    """

    with pytest.raises(ValidationError):
        CandidateArtifactMetadata.model_validate(
            {
                "candidate_id": str(uuid.uuid4()),
                "task_id": TASK_ID,
                "provider": "mock",
                "model": "mock-deterministic-v1",
                "prompt_version": "v1",
                "random_seed": 1,
                "max_attempts": 1,
                "status": "completed",
                "stop_reason": "visible_tests_passed",
                "required_module_filename": "toy.py",
                "specification_sha256": "0" * 64,
                "starter_sha256": "1" * 64,
                "visible_tests_sha256": "2" * 64,
                "visible_tests_passed": True,
                "attempt_count": 0,
                "model_sampling_deterministic": True,
                "seed_semantics": "test",
                "started_at": datetime.now(timezone.utc).isoformat(),
                # Attempted smuggled path field -- must be rejected by the
                # model's extra="forbid" config.
                "source_path": "/tmp/evil.py",
            }
        )
