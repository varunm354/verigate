"""Builds the reviewer-visible context for a task.

This module is the single choke point that determines what an AI
reviewer is allowed to see. It must never include hidden-test source,
hidden-test paths/filenames, or hidden-test results -- those exist only
for ground-truth evaluation via :class:`experiment.runner.PytestRunner`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .loader import LoadedTask


@dataclass(frozen=True)
class ReviewerContext:
    """The complete -- and only -- information shown to an AI reviewer."""

    task_id: str
    specification: str
    candidate_source: str
    visible_tests_source: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "specification": self.specification,
            "candidate_source": self.candidate_source,
            "visible_tests_source": dict(self.visible_tests_source),
        }


def build_reviewer_context(task: LoadedTask) -> ReviewerContext:
    """Assemble a :class:`ReviewerContext` from only spec + candidate + visible tests.

    Deliberately never reads from ``task.hidden_tests_path``.
    """

    visible_tests_source = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(task.visible_tests_path.glob("*.py"))
    }

    return ReviewerContext(
        task_id=task.manifest.task_id,
        specification=task.specification_path.read_text(encoding="utf-8"),
        candidate_source=task.candidate_path.read_text(encoding="utf-8"),
        visible_tests_source=visible_tests_source,
    )
