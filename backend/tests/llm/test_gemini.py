import base64

import pytest
from pydantic import BaseModel

from app.llm.base import LLMRateLimitError, LLMRequestError, LLMServerError
from app.llm.providers.gemini import THOUGHT_SIGNATURE_KEY, GeminiProvider
from app.llm.types import Message, ToolCall, ToolSpec
from tests.llm.mock_http import MockHTTP, json_reply, raise_timeout

API_KEY = "gemini-test-key"
SIGNATURE = b"opaque-signature"


def _provider(http: MockHTTP) -> GeminiProvider:
    return GeminiProvider(
        api_key=API_KEY, model="gemini-test", temperature=0.0, http_client=http.client
    )


def _candidate(parts: list[dict], finish_reason: str = "STOP") -> dict:
    return {
        "candidates": [
            {"content": {"role": "model", "parts": parts}, "finishReason": finish_reason}
        ],
        "usageMetadata": {
            "promptTokenCount": 12,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 3,
            "totalTokenCount": 20,
        },
        "modelVersion": "gemini-test-001",
    }


def test_text_reply_and_request_shape() -> None:
    http = MockHTTP(json_reply(200, _candidate([{"text": "Hello "}, {"text": "there"}])))

    response = _provider(http).chat([Message.system("Be terse."), Message.user("Hi")])

    assert response.text == "Hello there"
    assert response.tool_calls == []
    assert response.provider == "gemini"
    assert response.model == "gemini-test-001"
    assert response.finish_reason == "stop"
    assert response.usage.model_dump() == {
        "input_tokens": 12,
        "output_tokens": 8,  # candidates + thoughts
        "total_tokens": 20,
    }

    request = http.last
    assert request.url.path.endswith("/models/gemini-test:generateContent")
    assert request.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in str(request.url)
    body = http.body()
    assert API_KEY not in request.content.decode()
    assert body["systemInstruction"]["parts"] == [{"text": "Be terse."}]
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Hi"}]}]
    assert body["generationConfig"]["temperature"] == 0.0


def test_thought_parts_are_excluded_from_text() -> None:
    http = MockHTTP(
        json_reply(200, _candidate([{"text": "thinking...", "thought": True}, {"text": "42"}]))
    )

    assert _provider(http).chat([Message.user("?")]).text == "42"


def test_tool_call_round_trip() -> None:
    signature_b64 = base64.b64encode(SIGNATURE).decode()
    http = MockHTTP(
        json_reply(
            200,
            _candidate(
                [
                    {
                        "functionCall": {"name": "lookup_rule", "args": {"rule_id": "LIAB-1"}},
                        "thoughtSignature": signature_b64,
                    }
                ]
            ),
        ),
        json_reply(200, _candidate([{"text": "done"}])),
    )
    provider = _provider(http)
    tool = ToolSpec(
        name="lookup_rule",
        description="Look up a playbook rule",
        parameters={"type": "object", "properties": {"rule_id": {"type": "string"}}},
    )

    first = provider.chat([Message.user("Check clause")], tools=[tool])

    assert first.text == ""
    [call] = first.tool_calls
    assert call.name == "lookup_rule"
    assert call.arguments == {"rule_id": "LIAB-1"}
    assert call.id.startswith("call_")
    assert call.provider_metadata[THOUGHT_SIGNATURE_KEY] == signature_b64
    declaration = http.body(0)["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "lookup_rule"

    provider.chat(
        [
            Message.user("Check clause"),
            Message.assistant(tool_calls=[call]),
            Message.tool_result(call, '{"max_cap": "12 months fees"}'),
        ],
        tools=[tool],
    )

    model_turn, tool_turn = http.body(1)["contents"][1:]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][0]["functionCall"]["name"] == "lookup_rule"
    assert model_turn["parts"][0]["thoughtSignature"] == signature_b64
    response_part = tool_turn["parts"][0]["functionResponse"]
    assert response_part["name"] == "lookup_rule"
    assert response_part["response"] == {"max_cap": "12 months fees"}


def test_non_json_tool_result_is_wrapped() -> None:
    http = MockHTTP(json_reply(200, _candidate([{"text": "ok"}])))
    call = ToolCall(id="c1", name="lookup_rule")
    _provider(http).chat([Message.assistant(tool_calls=[call]), Message.tool_result(call, "n/a")])

    part = http.body()["contents"][1]["parts"][0]["functionResponse"]
    assert part["response"] == {"result": "n/a"}


class RiskVerdict(BaseModel):
    severity: str
    score: float


def test_structured_output_request() -> None:
    http = MockHTTP(json_reply(200, _candidate([{"text": '{"severity":"high","score":0.9}'}])))

    response = _provider(http).chat([Message.user("Score it")], response_schema=RiskVerdict)

    config = http.body()["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"]["properties"].keys() == {"severity", "score"}
    assert RiskVerdict.model_validate_json(response.text).severity == "high"


def test_blocked_prompt_reports_reason() -> None:
    http = MockHTTP(json_reply(200, {"promptFeedback": {"blockReason": "SAFETY"}}))

    response = _provider(http).chat([Message.user("x")])

    assert response.text == ""
    assert response.finish_reason == "blocked:safety"


def test_rate_limit_uses_retry_after_header() -> None:
    http = MockHTTP(
        json_reply(
            429,
            {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}},
            headers={"retry-after": "7"},
        )
    )

    with pytest.raises(LLMRateLimitError) as info:
        _provider(http).chat([Message.user("x")])

    assert info.value.status_code == 429
    assert info.value.retry_after == 7.0


def test_rate_limit_uses_retry_info_from_body() -> None:
    body = {
        "error": {
            "code": 429,
            "message": "quota",
            "status": "RESOURCE_EXHAUSTED",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}],
        }
    }
    http = MockHTTP(json_reply(429, body))

    with pytest.raises(LLMRateLimitError) as info:
        _provider(http).chat([Message.user("x")])

    assert info.value.retry_after == 17.0


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(500, LLMServerError), (503, LLMServerError), (400, LLMRequestError), (403, LLMRequestError)],
)
def test_http_errors_are_mapped_without_sdk_retries(status: int, error_type: type) -> None:
    http = MockHTTP(json_reply(status, {"error": {"code": status, "message": "boom"}}))

    with pytest.raises(error_type) as info:
        _provider(http).chat([Message.user("x")])

    assert info.value.status_code == status
    assert len(http.requests) == 1  # the SDK's own retries are disabled
    assert API_KEY not in str(info.value)


def test_unknown_model_is_reported_by_name() -> None:
    http = MockHTTP(
        json_reply(404, {"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})
    )

    with pytest.raises(LLMRequestError) as info:
        _provider(http).chat([Message.user("x")])

    assert str(info.value) == "gemini model not found: gemini-test"
    assert info.value.status_code == 404
    assert len(http.requests) == 1
    assert API_KEY not in str(info.value)


def test_timeout_is_a_retryable_server_error() -> None:
    http = MockHTTP(raise_timeout)

    with pytest.raises(LLMServerError):
        _provider(http).chat([Message.user("x")])
