"""Loads and validates VeriGate task definitions from ``backend/tasks``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .models import TaskManifest

DEFAULT_TASKS_ROOT = Path(__file__).resolve().parent.parent / "tasks"


class TaskLoadError(RuntimeError):
    """Raised when a task manifest or one of its required files/dirs is invalid or missing."""


@dataclass(frozen=True)
class LoadedTask:
    """A validated task manifest resolved against its on-disk root directory."""

    manifest: TaskManifest
    root_dir: Path

    @property
    def specification_path(self) -> Path:
        return self.root_dir / self.manifest.paths.specification

    @property
    def candidate_path(self) -> Path:
        return self.root_dir / self.manifest.paths.candidate

    @property
    def visible_tests_path(self) -> Path:
        return self.root_dir / self.manifest.paths.visible_tests

    @property
    def hidden_tests_path(self) -> Path:
        return self.root_dir / self.manifest.paths.hidden_tests


class TaskLoader:
    """Resolves a task ID under ``backend/tasks`` to a validated :class:`LoadedTask`."""

    def __init__(self, tasks_root: Optional[Path] = None) -> None:
        self.tasks_root = tasks_root or DEFAULT_TASKS_ROOT

    def load(self, task_id: str) -> LoadedTask:
        task_dir = self.tasks_root / task_id
        manifest_path = task_dir / "manifest.json"
        if not manifest_path.is_file():
            raise TaskLoadError(f"No manifest.json found for task {task_id!r} at {manifest_path}")

        try:
            manifest = TaskManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise TaskLoadError(f"Invalid manifest for task {task_id!r}: {exc}") from exc

        if manifest.task_id != task_id:
            raise TaskLoadError(
                f"manifest.json task_id {manifest.task_id!r} does not match "
                f"requested task directory {task_id!r}"
            )

        task = LoadedTask(manifest=manifest, root_dir=task_dir)

        self._require_file(task.specification_path, "specification")
        self._require_file(task.candidate_path, "candidate")
        self._require_dir(task.visible_tests_path, "visible_tests")
        self._require_dir(task.hidden_tests_path, "hidden_tests")

        return task

    @staticmethod
    def _require_file(path: Path, label: str) -> None:
        if not path.is_file():
            raise TaskLoadError(f"Missing required {label} file: {path}")

    @staticmethod
    def _require_dir(path: Path, label: str) -> None:
        if not path.is_dir():
            raise TaskLoadError(f"Missing required {label} directory: {path}")
