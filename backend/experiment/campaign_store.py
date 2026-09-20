"""Atomic persistence and exclusive locking for campaign artifacts.

Campaign files live at ``backend/data/campaigns/<campaign_id>.json`` by
default (already git-ignored via ``backend/data/*``). Writes use
write-temp-then-rename so readers never see a partial JSON document.
A non-blocking exclusive lock file prevents two processes from running
the same campaign at once.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Union

from pydantic import ValidationError

from .campaign_models import (
    CampaignArtifact,
    CampaignLoadError,
    CampaignLockError,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def default_campaigns_dir() -> Path:
    """``backend/data/campaigns``, resolved relative to this file."""

    return Path(__file__).resolve().parent.parent / "data" / "campaigns"


def repo_root() -> Path:
    return _REPO_ROOT


def relative_artifact_identifier(path: Path, *, fallback: str) -> str:
    """A safe project-relative identifier, never an absolute user path.

    If ``path`` is not under this repository (e.g. a temporary test
    directory), ``fallback`` is used instead.
    """

    try:
        return str(path.resolve().relative_to(_REPO_ROOT))
    except ValueError:
        return fallback


def campaign_json_path(campaign_id: uuid.UUID, campaigns_dir: Path) -> Path:
    return Path(campaigns_dir) / f"{campaign_id}.json"


def campaign_lock_path(campaign_id: uuid.UUID, campaigns_dir: Path) -> Path:
    return Path(campaigns_dir) / f"{campaign_id}.lock"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def save_campaign(artifact: CampaignArtifact, campaigns_dir: Optional[Path] = None) -> Path:
    """Atomically persist ``artifact`` under ``campaigns_dir``."""

    campaigns_dir = Path(campaigns_dir) if campaigns_dir is not None else default_campaigns_dir()
    path = campaign_json_path(artifact.campaign_id, campaigns_dir)
    identifier = relative_artifact_identifier(
        path, fallback=f"campaigns/{artifact.campaign_id}.json"
    )
    to_write = artifact.model_copy(update={"artifact_path": identifier})
    payload = json.dumps(to_write.model_dump(mode="json"), indent=2)
    _atomic_write_text(path, payload)
    return path


def load_campaign(
    campaign_id: Union[str, uuid.UUID],
    campaigns_dir: Optional[Path] = None,
) -> CampaignArtifact:
    """Load and validate one campaign artifact by UUID."""

    campaigns_dir = Path(campaigns_dir) if campaigns_dir is not None else default_campaigns_dir()
    validated_id = _validate_uuid(campaign_id)
    campaigns_root = campaigns_dir.resolve()
    raw_path = campaigns_root / f"{validated_id}.json"

    if raw_path.is_symlink():
        raise CampaignLoadError("Refusing to load campaign artifact: path is a symlink.")

    if not raw_path.is_file():
        raise CampaignLoadError(f"No campaign artifact found for id {validated_id}.")

    expected = raw_path.resolve()
    if expected.parent != campaigns_root:
        raise CampaignLoadError(
            "Refusing to load campaign artifact: resolved path escapes the campaigns directory."
        )

    try:
        raw = expected.read_text(encoding="utf-8")
    except OSError as exc:
        raise CampaignLoadError(f"Could not read campaign artifact {validated_id}.") from exc

    try:
        artifact = CampaignArtifact.model_validate_json(raw)
    except ValidationError as exc:
        raise CampaignLoadError(f"Malformed campaign artifact {validated_id}: {exc}") from exc

    if artifact.campaign_id != validated_id:
        raise CampaignLoadError(
            f"Campaign metadata campaign_id ({artifact.campaign_id}) does not "
            f"match the requested id ({validated_id})."
        )
    return artifact


class CampaignLock:
    """Non-blocking exclusive lock for one campaign id.

    Uses ``fcntl.flock`` so two OS processes cannot run the same campaign
    concurrently. The lock is released when :meth:`release` is called or
    when the process exits (the kernel drops the flock).
    """

    def __init__(self, lock_path: Path) -> None:
        self._path = Path(lock_path)
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._fd)
            self._fd = None
            raise CampaignLockError(
                "Campaign is already running in another process; refusing a concurrent run."
            ) from exc

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "CampaignLock":
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


def _validate_uuid(campaign_id: Union[str, uuid.UUID]) -> uuid.UUID:
    if isinstance(campaign_id, uuid.UUID):
        return campaign_id
    if not isinstance(campaign_id, str):
        raise CampaignLoadError("campaign_id must be a UUID string.")
    try:
        return uuid.UUID(campaign_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise CampaignLoadError(f"campaign_id is not a valid UUID: {campaign_id!r}") from exc
