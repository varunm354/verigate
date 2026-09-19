"""Tests for experiment.openai_candidate_generator.OpenAICandidateGenerator.

All tests here use a fake OpenAI client and never perform real network
calls. An autouse fixture also prevents any test from reading the real
project-root ``.env`` file, so these tests never see -- and therefore
can never leak -- the real ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import httpx2
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import ValidationError

import experiment.openai_candidate_generator as openai_candidate_module
from experiment import cli
from experiment.candidate_generator import CandidateGenerator, ProvidesResponseMetadata
from experiment.candidate_models import CandidateGenerationRequest, CandidateGeneratorOutput
from experiment.candidate_prompts import (
    build_candidate_generation_context,
    build_generation_prompt,
)
from experiment.loader import TaskLoader
from experiment.openai_candidate_generator import (
    DEFAULT_MODEL,
    OpenAICandidateGenerator,
    OpenAICandidateGeneratorAPIError,
    OpenAICandidateGeneratorAuthError,
    OpenAICandidateGeneratorConfigError,
    OpenAICandidateGeneratorNetworkError,
    OpenAICandidateGeneratorOutputError,
    OpenAICandidateGeneratorQuotaError,
    OpenAICandidateGeneratorRateLimitError,
    OpenAICandidateGeneratorTimeoutError,
    OpenAICandidateResponseSchema,
    resolve_model,
    _strip_optional_markdown_fences,
)

TASK_ID = "json_parser"
HIDDEN_SENTINEL = "VERIGATE_HIDDEN_TEST_SENTINEL_json_parser_9e2b6f"


@pytest.fixture(autouse=True)
def _never_read_the_real_project_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_candidate_module, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CANDIDATE_MODEL", raising=False)


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(
        self,
        output_parsed,
        response_id: str = "resp_cand_123",
        input_tokens: int = 200,
        output_tokens: int = 50,
    ) -> None:
        self.output_parsed = output_parsed
        self.id = response_id
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeResponsesResource:
    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._handler(**kwargs)


class _FakeOpenAIClient:
    def __init__(self, handler) -> None:
        self.responses = _FakeResponsesResource(handler)


def _valid_schema(**overrides) -> OpenAICandidateResponseSchema:
    defaults = dict(source="def parse(text):\n    return None\n", summary="stub")
    defaults.update(overrides)
    return OpenAICandidateResponseSchema(**defaults)


def _fake_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/responses")


def _fake_status_response(status_code: int, error_body: dict) -> httpx2.Response:
    return httpx2.Response(status_code=status_code, request=_fake_request(), json={"error": error_body})


def _sample_request() -> CandidateGenerationRequest:
    task = TaskLoader().load(TASK_ID)
    context = build_candidate_generation_context(task)
    return CandidateGenerationRequest(
        task_id=context.task_id,
        specification=context.specification,
        starter_source=context.starter_source,
        visible_tests_source=dict(context.visible_tests_source),
        required_module_filename=context.required_module_filename,
        attempt_number=1,
        max_attempts=3,
        random_seed=42,
    )


def test_successful_structured_response_is_parsed_into_generator_output():
    request = _sample_request()
    schema = _valid_schema(source="def parse(text):\n    return {}\n", summary="minimal parse")
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(schema))
    generator = OpenAICandidateGenerator(model="gpt-test-model", client=client)

    output = generator.generate(request)

    assert isinstance(output, CandidateGeneratorOutput)
    assert output.source == "def parse(text):\n    return {}\n"
    assert output.summary == "minimal parse"
    assert isinstance(generator, CandidateGenerator)
    assert isinstance(generator, ProvidesResponseMetadata)


def test_response_metadata_is_recorded_when_available():
    request = _sample_request()
    client = _FakeOpenAIClient(
        lambda **kwargs: _FakeResponse(_valid_schema(), response_id="resp_xyz", input_tokens=500, output_tokens=80)
    )
    generator = OpenAICandidateGenerator(model="gpt-test-model", client=client)

    generator.generate(request)

    assert generator.last_response_id == "resp_xyz"
    assert generator.last_input_tokens == 500
    assert generator.last_output_tokens == 80


def test_exact_prompt_text_is_sent_no_tools_and_no_seed():
    request = _sample_request()
    prompt = build_generation_prompt(request)
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema()))
    generator = OpenAICandidateGenerator(model="gpt-test-model", client=client)

    generator.generate(request)

    assert len(client.responses.calls) == 1
    call = client.responses.calls[0]
    assert call["input"] == prompt.text
    assert call["model"] == "gpt-test-model"
    assert call["text_format"] is OpenAICandidateResponseSchema
    assert call["tools"] == []
    assert "seed" not in call


def test_no_hidden_test_content_appears_in_the_request():
    task = TaskLoader().load(TASK_ID)
    hidden_filenames = {p.name for p in task.hidden_tests_path.glob("*.py")}
    assert hidden_filenames

    request = _sample_request()
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema()))
    generator = OpenAICandidateGenerator(model="gpt-test-model", client=client)
    generator.generate(request)

    sent_text = client.responses.calls[0]["input"]
    assert HIDDEN_SENTINEL not in sent_text
    assert "hidden_tests" not in sent_text
    for filename in hidden_filenames:
        assert filename not in sent_text
    hidden_source = (task.hidden_tests_path / "test_hidden.py").read_text(encoding="utf-8")
    assert hidden_source not in sent_text


def test_empty_source_is_rejected_as_output_error():
    request = _sample_request()
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema(source="   ")))
    generator = OpenAICandidateGenerator(model="gpt-test-model", client=client)

    with pytest.raises(OpenAICandidateGeneratorOutputError):
        generator.generate(request)


def test_none_parsed_output_is_rejected():
    request = _sample_request()
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(None))
    generator = OpenAICandidateGenerator(model="gpt-test-model", client=client)

    with pytest.raises(OpenAICandidateGeneratorOutputError):
        generator.generate(request)


def test_optional_markdown_fences_are_stripped():
    fenced = "```python\ndef parse(text):\n    return None\n```"
    unwrapped = _strip_optional_markdown_fences(fenced)
    assert unwrapped.strip().startswith("def parse")
    assert "```" not in unwrapped

    request = _sample_request()
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema(source=fenced)))
    generator = OpenAICandidateGenerator(model="gpt-test-model", client=client)
    output = generator.generate(request)
    assert output.source.strip().startswith("def parse")
    assert "```" not in output.source


def test_resolve_model_priority(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_CANDIDATE_MODEL", raising=False)
    assert resolve_model() == DEFAULT_MODEL == "gpt-5-mini"

    monkeypatch.setenv("OPENAI_CANDIDATE_MODEL", "gpt-env-model")
    assert resolve_model() == "gpt-env-model"
    assert resolve_model("gpt-explicit-model") == "gpt-explicit-model"


def test_openai_generator_reads_model_from_environment_when_no_override(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_CANDIDATE_MODEL", "gpt-env-model")
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema()))

    generator = OpenAICandidateGenerator(client=client)

    assert generator.model == "gpt-env-model"


def test_missing_api_key_raises_a_concise_configuration_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(OpenAICandidateGeneratorConfigError) as exc_info:
        OpenAICandidateGenerator(model="gpt-test-model")

    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert "not set" in message


def test_authentication_failure_is_wrapped_cleanly_with_no_secret_leakage():
    def handler(**kwargs):
        resp = _fake_status_response(
            401, {"message": "Invalid API key: sk-super-secret-value", "type": "invalid_request_error", "code": "invalid_api_key"}
        )
        raise AuthenticationError(
            "Invalid API key: sk-super-secret-value", response=resp, body=resp.json()["error"]
        )

    client = _FakeOpenAIClient(handler)
    generator = OpenAICandidateGenerator(model="gpt-test-model", client=client)

    with pytest.raises(OpenAICandidateGeneratorAuthError) as exc_info:
        generator.generate(_sample_request())

    assert "sk-super-secret-value" not in str(exc_info.value)


def test_quota_and_rate_limit_and_network_and_timeout_errors():
    request = _sample_request()

    def quota_handler(**kwargs):
        resp = _fake_status_response(429, {"message": "quota", "type": "insufficient_quota", "code": "insufficient_quota"})
        err = RateLimitError("quota", response=resp, body=resp.json()["error"])
        raise err

    generator = OpenAICandidateGenerator(model="gpt-test-model", client=_FakeOpenAIClient(quota_handler))
    with pytest.raises(OpenAICandidateGeneratorQuotaError):
        generator.generate(request)

    def rate_handler(**kwargs):
        resp = _fake_status_response(429, {"message": "slow down", "type": "rate_limit", "code": "rate_limit_exceeded"})
        raise RateLimitError("slow down", response=resp, body=resp.json()["error"])

    generator = OpenAICandidateGenerator(model="gpt-test-model", client=_FakeOpenAIClient(rate_handler))
    with pytest.raises(OpenAICandidateGeneratorRateLimitError):
        generator.generate(request)

    def timeout_handler(**kwargs):
        raise APITimeoutError(request=_fake_request())

    generator = OpenAICandidateGenerator(model="gpt-test-model", client=_FakeOpenAIClient(timeout_handler))
    with pytest.raises(OpenAICandidateGeneratorTimeoutError):
        generator.generate(request)

    def network_handler(**kwargs):
        raise APIConnectionError(request=_fake_request())

    generator = OpenAICandidateGenerator(model="gpt-test-model", client=_FakeOpenAIClient(network_handler))
    with pytest.raises(OpenAICandidateGeneratorNetworkError):
        generator.generate(request)

    def status_handler(**kwargs):
        resp = _fake_status_response(500, {"message": "server", "type": "server_error", "code": "server_error"})
        raise APIStatusError("server", response=resp, body=resp.json()["error"])

    generator = OpenAICandidateGenerator(model="gpt-test-model", client=_FakeOpenAIClient(status_handler))
    with pytest.raises(OpenAICandidateGeneratorAPIError):
        generator.generate(request)


def test_cli_generate_openai_reports_clean_error_on_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = cli.main(
        [
            "generate-candidate",
            "--task",
            TASK_ID,
            "--provider",
            "openai",
            "--max-attempts",
            "1",
            "--seed",
            "1",
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "OPENAI_API_KEY" in captured.err
    assert "not set" in captured.err
    assert captured.out == ""


def test_provider_schema_rejects_empty_required_fields():
    with pytest.raises(ValidationError):
        OpenAICandidateResponseSchema()  # type: ignore[call-arg]
