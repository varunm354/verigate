"""Builds provider-independent prompts for coding-agent candidate generation.

The generator is allowed to see only:

  1. the task specification,
  2. starter code,
  3. visible-test source,
  4. (later attempts) the prior candidate source and visible-test
     execution feedback from that candidate's own attempts.

This module never reads ``LoadedTask.hidden_tests_path``, never mentions
hidden tests, and never includes an output filesystem path. The required
module filename is harness metadata from the task manifest so the model
knows which module it is implementing; the file is always written by the
orchestrator, never by provider-chosen paths.

**Bounded visible-test feedback.** ``VisibleTestFeedback.stdout``/``stderr``
on a :class:`~experiment.candidate_models.CandidateGenerationRequest` may be
arbitrarily large (a candidate's own pytest run can produce unbounded
output). Only :data:`MAX_VISIBLE_FEEDBACK_CHARS` characters of that
feedback -- rendered deterministically by
:func:`format_visible_feedback_for_prompt` -- are ever placed into a
generation prompt. The full, untruncated stdout/stderr is still recorded
on the saved local artifact (``CandidateAttempt.visible_result`` in
``metadata.json``, see ``experiment.candidate_orchestrator``); this bound
applies only to what is transmitted to a provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .candidate_models import (
    CandidateGenerationError,
    CandidateGenerationRequest,
    VisibleTestFeedback,
)
from .loader import LoadedTask

CANDIDATE_PROMPT_VERSION = "v1"

# Documented maximum size, in characters, of the rendered visible-test
# feedback block placed into a generation prompt. Bounds prompt size/cost
# regardless of how much stdout/stderr a candidate's own test run
# produced. This is a hard cap on the *prompt* representation only -- see
# the module docstring above for where the full raw output still lives.
MAX_VISIBLE_FEEDBACK_CHARS = 12_000

_TRUNCATION_MARKER_TEMPLATE = (
    "\n... [VERIGATE TRUNCATED {omitted} character(s) here to bound prompt size] ...\n"
)
# Longest possible rendering of the marker for a count with this many
# digits (i.e. omitted counts up to ~10**18, far beyond any real test
# output) -- used to conservatively reserve space so the final bounded
# text can never exceed its budget.
_MAX_MARKER_LEN = len(_TRUNCATION_MARKER_TEMPLATE.format(omitted="9" * 18))

GENERATOR_INSTRUCTIONS = (
    "You are implementing a candidate solution to a coding task. You are given "
    "the task specification, the starter code, and the visible test suite. Write "
    "a complete implementation that satisfies the specification and passes the "
    "visible tests. Return structured output containing only the complete module "
    "source code and a short implementation summary. Do not wrap the source in "
    "markdown fences. The source must be a complete, directly writable Python "
    "module."
)

REVISION_INSTRUCTIONS = (
    "The previous implementation did not pass the visible tests. You are given "
    "that prior source together with the visible-test execution feedback "
    "(stdout, stderr, and pass/fail counts) from running it. Produce a "
    "corrected complete implementation for the same module. Return structured "
    "output containing only the complete module source code and a short "
    "implementation summary. Do not wrap the source in markdown fences."
)


@dataclass(frozen=True)
class CandidateGenerationContext:
    """Frozen, generator-visible inputs for one task.

    Built once at the start of a generation run and reused for every
    attempt so hashes and prompts can never disagree. Contains no
    hidden-test information.
    """

    task_id: str
    specification: str
    starter_source: str
    visible_tests_source: dict[str, str] = field(default_factory=dict)
    required_module_filename: str = ""
    timeout_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "specification": self.specification,
            "starter_source": self.starter_source,
            "visible_tests_source": dict(self.visible_tests_source),
            "required_module_filename": self.required_module_filename,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class CandidatePromptSections:
    """Named, independently inspectable sections of a generation prompt."""

    instructions: str
    specification: str
    starter_source: str
    visible_tests_source: dict[str, str]
    required_module_filename: str
    previous_source: str
    visible_feedback_text: str

    def render(self) -> str:
        parts: list[str] = [
            self.instructions,
            "",
            "## Task specification",
            self.specification,
            "",
            "## Starter code",
            f"Required module filename (harness will write this file; do not choose a path): {self.required_module_filename}",
            "```python",
            self.starter_source,
            "```",
            "",
            "## Visible tests",
        ]
        for name, source in sorted(self.visible_tests_source.items()):
            parts += [f"### {name}", "```python", source, "```"]

        if self.previous_source:
            parts += [
                "",
                "## Previous implementation",
                "```python",
                self.previous_source,
                "```",
            ]

        if self.visible_feedback_text:
            parts += ["", "## Visible test execution feedback", self.visible_feedback_text]

        parts += [
            "",
            "## Output",
            "Return the complete source code for the required module and a short summary.",
        ]
        return "\n".join(parts)


@dataclass(frozen=True)
class CandidateGeneratorPrompt:
    """A fully constructed, provider-independent prompt for one generation attempt."""

    task_id: str
    attempt_number: int
    sections: CandidatePromptSections

    @property
    def text(self) -> str:
        return self.sections.render()


def build_candidate_generation_context(task: LoadedTask) -> CandidateGenerationContext:
    """Assemble generator-visible inputs from spec + starter + visible tests.

    Raises :class:`CandidateGenerationError` if the task has no starter.
    Deliberately never reads from ``task.hidden_tests_path``.
    """

    starter_path = task.starter_path
    if starter_path is None:
        raise CandidateGenerationError(
            f"Task {task.manifest.task_id!r} has no starter path; "
            "candidate generation requires starter code."
        )

    visible_tests_source = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(task.visible_tests_path.glob("*.py"))
    }

    required_module_filename = Path(task.manifest.paths.candidate).name
    if required_module_filename != task.manifest.paths.candidate:
        raise CandidateGenerationError(
            "Candidate generation requires paths.candidate to be a bare filename "
            f"(got {task.manifest.paths.candidate!r})."
        )

    return CandidateGenerationContext(
        task_id=task.manifest.task_id,
        specification=task.specification_path.read_text(encoding="utf-8"),
        starter_source=starter_path.read_text(encoding="utf-8"),
        visible_tests_source=visible_tests_source,
        required_module_filename=required_module_filename,
        timeout_seconds=task.manifest.timeout_seconds,
    )


def _bound_text_block(text: str, budget: int) -> str:
    """Keep only the beginning and end of ``text`` within ``budget`` chars.

    Deterministic: the same ``(text, budget)`` pair always produces the
    same output. If ``text`` already fits within ``budget``, it is
    returned completely unchanged (no marker is added). Otherwise the
    head and tail of ``text`` are kept -- preserving useful
    beginning/end failure information -- with an explicit truncation
    marker inserted between them stating how many characters were
    omitted. The tail is weighted larger than the head because pytest's
    per-failure tracebacks and its short summary line both appear near
    the end of stdout.
    """

    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text

    marker_reserved = min(_MAX_MARKER_LEN, budget)
    remaining = max(budget - marker_reserved, 0)
    head_len = remaining * 2 // 5  # ~40% head
    tail_len = remaining - head_len  # ~60% tail

    head = text[:head_len] if head_len > 0 else ""
    tail = text[len(text) - tail_len :] if tail_len > 0 else ""
    omitted = len(text) - len(head) - len(tail)
    marker = _TRUNCATION_MARKER_TEMPLATE.format(omitted=omitted)

    bounded = head + marker + tail
    # Conservative safety net: `marker_reserved` already accounts for the
    # worst case, so this should never actually trim further content --
    # but the hard cap is guaranteed regardless.
    return bounded[:budget]


def format_visible_feedback_for_prompt(
    feedback: VisibleTestFeedback, max_chars: int = MAX_VISIBLE_FEEDBACK_CHARS
) -> str:
    """Render bounded, deterministic visible-test feedback for a prompt.

    Always preserves the small, fixed-size ``passed``/``timed_out``/
    ``exit_code``/``passed_count``/``failed_count``/``duration_seconds``
    fields verbatim. ``stdout``/``stderr`` are each bounded (see
    :func:`_bound_text_block`) so the *entire* rendered block never
    exceeds ``max_chars`` -- regardless of how large the candidate's own
    raw pytest output was. When nothing needs to be removed, stdout/stderr
    are reproduced completely unchanged and no truncation marker appears.
    """

    header = "\n".join(
        [
            f"passed: {feedback.passed}",
            f"timed_out: {feedback.timed_out}",
            f"exit_code: {feedback.exit_code}",
            f"passed_count: {feedback.passed_count}",
            f"failed_count: {feedback.failed_count}",
            f"duration_seconds: {feedback.duration_seconds}",
            "",
        ]
    )
    stdout_label = "### stdout\n"
    stderr_label = "\n\n### stderr\n"

    fixed_overhead = len(header) + len(stdout_label) + len(stderr_label)
    remaining = max(max_chars - fixed_overhead, 0)
    # stdout usually carries the more useful per-test detail; stderr is
    # typically shorter warnings/interpreter noise.
    stdout_budget = remaining * 7 // 10
    stderr_budget = remaining - stdout_budget

    bounded_stdout = _bound_text_block(feedback.stdout, stdout_budget)
    bounded_stderr = _bound_text_block(feedback.stderr, stderr_budget)

    text = header + stdout_label + bounded_stdout + stderr_label + bounded_stderr
    # Hard guarantee regardless of any rounding above.
    return text[:max_chars]


def _format_visible_feedback(request: CandidateGenerationRequest) -> str:
    feedback = request.visible_feedback
    if feedback is None:
        return ""
    return format_visible_feedback_for_prompt(feedback)


def build_generation_prompt(request: CandidateGenerationRequest) -> CandidateGeneratorPrompt:
    """Render a generation prompt from a request. Never reads hidden tests."""

    instructions = GENERATOR_INSTRUCTIONS if request.attempt_number == 1 else REVISION_INSTRUCTIONS
    sections = CandidatePromptSections(
        instructions=instructions,
        specification=request.specification,
        starter_source=request.starter_source,
        visible_tests_source=dict(request.visible_tests_source),
        required_module_filename=request.required_module_filename,
        previous_source=request.previous_source or "",
        visible_feedback_text=_format_visible_feedback(request),
    )
    return CandidateGeneratorPrompt(
        task_id=request.task_id,
        attempt_number=request.attempt_number,
        sections=sections,
    )
