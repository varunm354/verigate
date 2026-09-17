"""Tests for experiment.openai_reviewer.OpenAIReviewer.

All tests here use a fake OpenAI client (duck-typed: only needs a
``.responses.parse(**kwargs)`` method) and never perform real network
calls. An autouse fixture also prevents any test from reading the real
project-root ``.env`` file, so these tests never see -- and therefore
can never leak -- the real ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import httpx2
import pytest
from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError
from pydantic import ValidationError

import experiment.openai_reviewer as openai_reviewer_module
from experiment import cli
from experiment.conditions import Condition
from experiment.loader import TaskLoader
from experiment.models import ReviewerAssessment
from experiment.openai_reviewer import (
    DEFAULT_MODEL,
    OpenAIReviewer,
    OpenAIReviewerAPIError,
    OpenAIReviewerAuthError,
    OpenAIReviewerConfigError,
    OpenAIReviewerNetworkError,
    OpenAIReviewerOutputError,
    OpenAIReviewerQuotaError,
    OpenAIReviewerRateLimitError,
    OpenAIReviewerResponseSchema,
    resolve_model,
)
from experiment.prompts import build_prompt
from experiment.reviewer import ProvidesResponseMetadata, Reviewer

TASK_ID = "expression_evaluator"
HIDDEN_SENTINEL = "VERIGATE_HIDDEN_TEST_SENTINEL_7f3c1a"


@pytest.fixture(autouse=True)
def _never_read_the_real_project_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file must be fully isolated from the real .env.

    The repo's real .env may contain a real OPENAI_API_KEY. No test here
    may read it, so we replace ``load_dotenv`` with a no-op for the
    duration of this module's tests; each test sets/clears
    OPENAI_API_KEY / OPENAI_REVIEWER_MODEL explicitly via monkeypatch.
    """

    monkeypatch.setattr(openai_reviewer_module, "load_dotenv", lambda *a, **k: False)


# --------------------------------------------------------------------------
# Fake OpenAI client (no network calls, duck-typed to the real SDK surface)
# --------------------------------------------------------------------------


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(
        self,
        output_parsed,
        response_id: str = "resp_test_123",
        input_tokens: int = 111,
        output_tokens: int = 22,
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


def _valid_schema(**overrides) -> OpenAIReviewerResponseSchema:
    defaults = dict(confidence=60, suspected_issues=[], rationale="ok")
    defaults.update(overrides)
    return OpenAIReviewerResponseSchema(**defaults)


def _fake_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/responses")


def _fake_status_response(status_code: int, error_body: dict) -> httpx2.Response:
    return httpx2.Response(status_code=status_code, request=_fake_request(), json={"error": error_body})


def _load_prompt(condition: Condition):
    task = TaskLoader().load(TASK_ID)
    return task, build_prompt(task, condition)


# --------------------------------------------------------------------------
# Successful structured-output parsing
# --------------------------------------------------------------------------


def test_successful_structured_response_is_parsed_into_reviewer_assessment():
    _, prompt = _load_prompt(Condition.B_VISIBLE_PASS)
    schema = _valid_schema(
        confidence=72,
        suspected_issues=["minor edge case"],
        rationale="Looks solid overall.",
    )
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(schema))
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)

    assessment = reviewer.review(Condition.B_VISIBLE_PASS, prompt)

    assert isinstance(assessment, ReviewerAssessment)
    assert assessment.predicted_pass is True  # derived: 72 >= 50
    assert assessment.confidence == 72
    assert assessment.suspected_issues == ["minor edge case"]
    assert assessment.rationale == "Looks solid overall."


def test_response_metadata_is_recorded_when_available():
    _, prompt = _load_prompt(Condition.A_NO_RESULT)
    client = _FakeOpenAIClient(
        lambda **kwargs: _FakeResponse(_valid_schema(), response_id="resp_xyz", input_tokens=500, output_tokens=80)
    )
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)

    reviewer.review(Condition.A_NO_RESULT, prompt)

    assert reviewer.last_response_id == "resp_xyz"
    assert reviewer.last_input_tokens == 500
    assert reviewer.last_output_tokens == 80
    assert isinstance(reviewer, ProvidesResponseMetadata)
    assert isinstance(reviewer, Reviewer)


# --------------------------------------------------------------------------
# Exact request contents: prompt text, no tools, condition attachment
# --------------------------------------------------------------------------


def test_exact_prompt_text_is_sent_and_no_tools_are_enabled():
    _, prompt = _load_prompt(Condition.C_ADVERSARIAL)
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema()))
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)

    reviewer.review(Condition.C_ADVERSARIAL, prompt)

    assert len(client.responses.calls) == 1
    call = client.responses.calls[0]
    assert call["input"] == prompt.text
    assert call["model"] == "gpt-test-model"
    assert call["text_format"] is OpenAIReviewerResponseSchema
    assert call["tools"] == []


@pytest.mark.parametrize(
    "condition", [Condition.A_NO_RESULT, Condition.B_VISIBLE_PASS, Condition.C_ADVERSARIAL]
)
def test_condition_is_attached_by_application_code_not_the_model(condition: Condition):
    _, prompt = _load_prompt(condition)
    # The schema the "model" returns says nothing about which condition it
    # was given -- application code must still attach the right one.
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema()))
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)

    assessment = reviewer.review(condition, prompt)

    assert assessment.condition == condition


def test_request_settings_are_identical_across_conditions_except_input_text():
    task = TaskLoader().load(TASK_ID)
    captured: list[dict] = []
    client = _FakeOpenAIClient(lambda **kwargs: (captured.append(kwargs), _FakeResponse(_valid_schema()))[-1])
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)

    for condition in Condition:
        reviewer.review(condition, build_prompt(task, condition))

    assert len(captured) == 3
    non_input_keys = set(captured[0]) - {"input"}
    for call in captured[1:]:
        for key in non_input_keys:
            assert call[key] == captured[0][key], key


def test_no_hidden_test_content_appears_in_the_request(task_id: str = TASK_ID):
    task = TaskLoader().load(task_id)
    hidden_filenames = {p.name for p in task.hidden_tests_path.glob("*.py")}
    assert hidden_filenames  # sanity

    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema()))
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)

    for condition in Condition:
        prompt = build_prompt(task, condition)
        reviewer.review(condition, prompt)

    for call in client.responses.calls:
        sent_text = call["input"]
        assert HIDDEN_SENTINEL not in sent_text
        assert "hidden_tests" not in sent_text
        for filename in hidden_filenames:
            assert filename not in sent_text


def test_prompt_condition_mismatch_is_rejected_before_any_api_call():
    task = TaskLoader().load(TASK_ID)
    prompt_for_a = build_prompt(task, Condition.A_NO_RESULT)
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema()))
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)

    with pytest.raises(ValueError):
        reviewer.review(Condition.B_VISIBLE_PASS, prompt_for_a)

    assert client.responses.calls == []  # never called the API


# --------------------------------------------------------------------------
# Model selection: default, environment, explicit override
# --------------------------------------------------------------------------


def test_resolve_model_defaults_to_the_documented_constant(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_REVIEWER_MODEL", raising=False)
    assert resolve_model() == DEFAULT_MODEL == "gpt-5.6-luna"


def test_resolve_model_reads_the_environment_variable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_REVIEWER_MODEL", "gpt-env-model")
    assert resolve_model() == "gpt-env-model"


def test_resolve_model_explicit_argument_takes_priority_over_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_REVIEWER_MODEL", "gpt-env-model")
    assert resolve_model("gpt-explicit-model") == "gpt-explicit-model"


def test_openai_reviewer_reads_model_from_environment_when_no_override(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_REVIEWER_MODEL", "gpt-env-model")
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(_valid_schema()))

    reviewer = OpenAIReviewer(client=client)

    assert reviewer.model == "gpt-env-model"


def test_cli_review_openai_provider_honors_explicit_model_override(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key-for-tests")
    monkeypatch.setenv("OPENAI_REVIEWER_MODEL", "gpt-env-model")

    captured: dict = {}

    class _FakeOpenAIForCLI:
        def __init__(self, api_key: str) -> None:
            captured["api_key_seen"] = api_key

            def handler(**kwargs):
                captured.update(kwargs)
                return _FakeResponse(_valid_schema())

            self.responses = _FakeResponsesResource(handler)

    monkeypatch.setattr(openai_reviewer_module, "OpenAI", _FakeOpenAIForCLI)

    result = cli.run_review(TASK_ID, Condition.A_NO_RESULT, "openai", model="gpt-cli-override")

    # The CLI --model flag beats the environment variable.
    assert result["model"] == "gpt-cli-override"
    assert captured["model"] == "gpt-cli-override"


def test_mock_provider_rejects_a_model_override():
    with pytest.raises(ValueError):
        cli.run_review(TASK_ID, Condition.A_NO_RESULT, "mock", model="gpt-should-not-be-allowed")


# --------------------------------------------------------------------------
# Configuration and error handling
# --------------------------------------------------------------------------


def test_missing_api_key_raises_a_concise_configuration_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(OpenAIReviewerConfigError) as exc_info:
        OpenAIReviewer(model="gpt-test-model")

    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert "not set" in message


def test_reviewer_works_when_api_key_is_supplied_via_process_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key-for-tests")
    monkeypatch.setattr(openai_reviewer_module, "OpenAI", lambda api_key: _FakeOpenAIClient(
        lambda **kwargs: _FakeResponse(_valid_schema())
    ))

    # Should not raise -- the key came from the process environment, not
    # from any .env file (which we've disabled for this whole module).
    reviewer = OpenAIReviewer(model="gpt-test-model")
    task = TaskLoader().load(TASK_ID)
    prompt = build_prompt(task, Condition.A_NO_RESULT)
    assessment = reviewer.review(Condition.A_NO_RESULT, prompt)
    assert isinstance(assessment, ReviewerAssessment)


def test_authentication_failure_is_wrapped_cleanly_with_no_secret_leakage():
    def handler(**kwargs):
        resp = _fake_status_response(
            401, {"message": "Invalid API key: sk-super-secret-value", "type": "invalid_request_error", "code": "invalid_api_key"}
        )
        raise AuthenticationError(
            "Invalid API key: sk-super-secret-value", response=resp, body=resp.json()["error"]
        )

    client = _FakeOpenAIClient(handler)
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)
    _, prompt = _load_prompt(Condition.A_NO_RESULT)

    with pytest.raises(OpenAIReviewerAuthError) as exc_info:
        reviewer.review(Condition.A_NO_RESULT, prompt)

    assert "sk-super-secret-value" not in str(exc_info.value)


def test_insufficient_quota_is_distinguished_from_plain_rate_limiting():
    def handler(**kwargs):
        resp = _fake_status_response(429, {"message": "quota", "type": "insufficient_quota", "code": "insufficient_quota"})
        raise RateLimitError("You exceeded your current quota", response=resp, body=resp.json()["error"])

    client = _FakeOpenAIClient(handler)
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)
    _, prompt = _load_prompt(Condition.A_NO_RESULT)

    with pytest.raises(OpenAIReviewerQuotaError):
        reviewer.review(Condition.A_NO_RESULT, prompt)


def test_plain_rate_limit_is_reported_distinctly_from_quota_errors():
    def handler(**kwargs):
        resp = _fake_status_response(
            429, {"message": "slow down", "type": "requests", "code": "rate_limit_exceeded"}
        )
        raise RateLimitError("slow down", response=resp, body=resp.json()["error"])

    client = _FakeOpenAIClient(handler)
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)
    _, prompt = _load_prompt(Condition.A_NO_RESULT)

    with pytest.raises(OpenAIReviewerRateLimitError):
        reviewer.review(Condition.A_NO_RESULT, prompt)


def test_network_failure_is_wrapped_cleanly():
    def handler(**kwargs):
        raise APIConnectionError(request=_fake_request())

    client = _FakeOpenAIClient(handler)
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)
    _, prompt = _load_prompt(Condition.A_NO_RESULT)

    with pytest.raises(OpenAIReviewerNetworkError):
        reviewer.review(Condition.A_NO_RESULT, prompt)


def test_generic_api_status_error_is_wrapped_cleanly():
    def handler(**kwargs):
        resp = _fake_status_response(500, {"message": "internal error", "type": "server_error", "code": None})
        raise APIStatusError("internal error", response=resp, body=resp.json()["error"])

    client = _FakeOpenAIClient(handler)
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)
    _, prompt = _load_prompt(Condition.A_NO_RESULT)

    with pytest.raises(OpenAIReviewerAPIError):
        reviewer.review(Condition.A_NO_RESULT, prompt)


def test_refused_or_missing_parsed_output_raises_output_error():
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(None))
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)
    _, prompt = _load_prompt(Condition.A_NO_RESULT)

    with pytest.raises(OpenAIReviewerOutputError):
        reviewer.review(Condition.A_NO_RESULT, prompt)


def test_provider_schema_no_longer_has_a_predicted_pass_field():
    # Regression test for the live smoke-test finding: a separate
    # `predicted_pass` field let models report "confidence in my verdict"
    # instead of "confidence of passing" (e.g. predicted_pass=False,
    # confidence=99, meaning "99% sure it fails"). The model is now asked
    # for only confidence/suspected_issues/rationale.
    assert "predicted_pass" not in OpenAIReviewerResponseSchema.model_fields

    # Pydantic ignores unknown kwargs by default, so even if a model somehow
    # still returned a "predicted_pass" key, it would never become an
    # attribute on the parsed object -- there is no field for it to fill.
    schema = OpenAIReviewerResponseSchema(
        predicted_pass=False, confidence=60, suspected_issues=[], rationale="r"
    )
    assert not hasattr(schema, "predicted_pass")

    # The JSON schema sent to the API (via text_format) also never mentions it.
    json_schema = OpenAIReviewerResponseSchema.model_json_schema()
    assert "predicted_pass" not in json_schema.get("properties", {})


def test_confidence_field_description_clarifies_probability_of_passing():
    description = OpenAIReviewerResponseSchema.model_fields["confidence"].description
    assert description is not None
    assert "probability" in description.lower()
    assert "pass" in description.lower()
    assert "not confidence in the reviewer" in description.lower()


@pytest.mark.parametrize(
    "confidence,expected_predicted_pass",
    [
        (0, False),
        (1, False),
        (49, False),
        (50, True),  # exact boundary: >= 50 derives True
        (51, True),
        (99, True),
        (100, True),
    ],
)
def test_predicted_pass_is_derived_from_confidence_not_requested_from_the_model(
    confidence: int, expected_predicted_pass: bool
):
    # This is the exact scenario from the live smoke test: the model
    # returns only a confidence number (here, e.g. 99), and application
    # code -- not the model -- decides predicted_pass from it.
    schema = _valid_schema(confidence=confidence)
    client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(schema))
    reviewer = OpenAIReviewer(model="gpt-test-model", client=client)
    _, prompt = _load_prompt(Condition.A_NO_RESULT)

    assessment = reviewer.review(Condition.A_NO_RESULT, prompt)

    assert assessment.confidence == confidence
    assert assessment.predicted_pass is expected_predicted_pass
    # Derivation guarantees ReviewerAssessment's own consistency validator
    # can never be violated by OpenAIReviewer output.


def test_out_of_bounds_confidence_is_rejected_by_the_provider_schema_itself():
    with pytest.raises(ValidationError):
        OpenAIReviewerResponseSchema(confidence=150, suspected_issues=[], rationale="r")


# --------------------------------------------------------------------------
# CLI-level: clean, secret-free error output
# --------------------------------------------------------------------------


def test_cli_review_openai_reports_a_clean_error_on_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = cli.main(
        ["review", "--task", TASK_ID, "--condition", "A_NO_RESULT", "--provider", "openai"]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "OPENAI_API_KEY" in captured.err
    assert "not set" in captured.err
    assert captured.out == ""


def test_cli_review_openai_reports_a_clean_error_on_auth_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key-for-tests")

    def handler(**kwargs):
        resp = _fake_status_response(
            401, {"message": "bad key: sk-not-a-real-key-for-tests", "type": "invalid_request_error", "code": "invalid_api_key"}
        )
        raise AuthenticationError(
            "bad key: sk-not-a-real-key-for-tests", response=resp, body=resp.json()["error"]
        )

    monkeypatch.setattr(
        openai_reviewer_module, "OpenAI", lambda api_key: _FakeOpenAIClient(handler)
    )

    exit_code = cli.main(
        ["review", "--task", TASK_ID, "--condition", "A_NO_RESULT", "--provider", "openai"]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "sk-not-a-real-key-for-tests" not in captured.err
    assert "sk-not-a-real-key-for-tests" not in captured.out
