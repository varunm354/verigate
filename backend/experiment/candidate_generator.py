"""Provider-independent candidate-generator interface.

Defines the typed protocol that any coding-agent backend (mock, OpenAI,
...) must implement, plus a deterministic :class:`MockCandidateGenerator`
used for automated tests and for validating the generation loop before
any real API calls are made or any API credits are spent.

No implementation may read hidden-test source, paths, or results, and
none may choose an output filesystem path -- generators return source
text only.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from .candidate_models import CandidateGenerationRequest, CandidateGeneratorOutput
from .reviewer import ProvidesResponseMetadata

# Re-export so callers can isinstance-check either protocol from one import.
__all__ = [
    "CandidateGenerator",
    "MockCandidateGenerator",
    "ProvidesResponseMetadata",
]


@runtime_checkable
class CandidateGenerator(Protocol):
    """Anything that can turn a generation request into source + summary.

    Concrete implementations must expose ``provider``/``model`` identifiers
    and a ``generate`` method with this exact signature. No implementation
    may read or return hidden-test source, paths, or results, and none may
    return a filesystem path -- only source text and a summary.
    """

    provider: str
    model: str

    def generate(self, request: CandidateGenerationRequest) -> CandidateGeneratorOutput:
        """Return validated source + summary for this request."""
        ...


class MockCandidateGenerator:
    """Deterministic generator with no network calls.

    By default each call returns the request's own starter source (a
    complete, valid Python module that will fail visible tests). Tests
    can pass ``sources`` to return a fixed sequence of implementations.

    ``random_seed`` on the request is recorded by the orchestrator for
    workflow reproducibility; this mock's output is deterministic because
    it ignores sampling entirely, not because a seed was sent to a model.
    """

    provider = "mock"
    model = "mock-deterministic-v1"

    def __init__(
        self,
        sources: Optional[Sequence[str]] = None,
        summaries: Optional[Sequence[str]] = None,
    ) -> None:
        self._sources = list(sources) if sources is not None else None
        self._summaries = list(summaries) if summaries is not None else None
        self._index = 0
        self.requests: list[CandidateGenerationRequest] = []

    def generate(self, request: CandidateGenerationRequest) -> CandidateGeneratorOutput:
        self.requests.append(request)
        if self._sources is None:
            source = request.starter_source
        else:
            source = self._sources[min(self._index, len(self._sources) - 1)]
        if self._summaries is None:
            summary = f"mock candidate attempt {request.attempt_number}"
        else:
            summary = self._summaries[min(self._index, len(self._summaries) - 1)]
        self._index += 1
        return CandidateGeneratorOutput(source=source, summary=summary)
