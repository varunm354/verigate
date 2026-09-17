"""Typed data models for VeriGate's experiment harness.

These describe a task's manifest (as stored in ``manifest.json``) and the
structured results produced by running its visible/hidden test suites.
They intentionally contain no reviewer- or LLM-specific logic; that lives
in ``experiment.context`` (added in a later milestone).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class TaskPaths(BaseModel):
    """Paths, relative to the task's root directory, to its components."""

    specification: str
    candidate: str
    visible_tests: str
    hidden_tests: str


class TaskManifest(BaseModel):
    """Schema for a task's ``manifest.json`` file."""

    task_id: str
    title: str
    language: Literal["python"]
    timeout_seconds: float = Field(gt=0)
    paths: TaskPaths


class TestSuiteResult(BaseModel):
    """Outcome of running one task's visible or hidden pytest suite."""

    suite: Literal["visible", "hidden"]
    passed: bool
    timed_out: bool
    exit_code: Optional[int]
    duration_seconds: float
    stdout: str
    stderr: str
    passed_count: int
    failed_count: int
