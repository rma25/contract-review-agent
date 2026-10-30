import json

import pytest
from pydantic import BaseModel

from app.llm.base import LLMRateLimitError, LLMRequestError, LLMResponseError, LLMServerError
from app.llm.providers.groq import GroqProvider
from app.llm.types import Message, ToolCall, ToolSpec
from tests.llm.mock_http import MockHTTP, json_reply, raise_connect_error, raise_timeout

API_KEY = "groq-test-key"
BASE_URL = "https://api.groq.test/openai/v1"


def _provider(http: MockHTTP) -> GroqProvider:
    return GroqProvider(
        api_key=API_KEY,
        model="llama-test",
        base_url=BASE_URL,
        temperature=0.0,
        http_client=http.client,
    )


def _completion(message: dict, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "llama-test",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
    }


def test_text_reply_and_request_shape() -> None:
    http = MockHTTP(json_reply(200, _completion({"role": "assistant", "content": "Hello"})))

    response = _provider(http).chat([Message.system("Be terse."), Message.user("Hi")])

    assert response.text == "Hello"
    assert response.provider == "groq"
    assert response.model == "llama-test"
    assert response.finish_reason == "stop"
    assert response.usage.model_dump() == {
        "input_tokens": 9,
        "output_tokens": 4,
        "total_tokens": 13,
    }

    request = http.last
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in request.content.decode()
    body = http.body()
    assert body["model"] == "llama-test"
    assert body["temperature"] == 0.0
    assert body["messages"] == [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "Hi"},
    ]


def test_tool_call_round_trip() -> None:
    tool_call = {
        "id": "call_abc",
        "type": "function",
        "function": {"name": "lookup_rule", "arguments": '{"rule_id": "LIAB-1"}'},
    }
    http = MockHTTP(
        json_reply(
            200,
            _completion(
                {"role": "assistant", "content": None, "tool_calls": [tool_call]}, "tool_calls"
            ),
        ),
        json_reply(200, _completion({"role": "assistant", "content": "done"})),
    )
    provider = _provider(http)
    tool = ToolSpec(
        name="lookup_rule",
        description="Look up a playbook rule",
        parameters={"type": "object", "properties": {"rule_id": {"type": "string"}}},
    )

    first = provider.chat([Message.user("Check clause")], tools=[tool])

    assert first.text == ""
    assert first.finish_reason == "tool_calls"
    assert first.tool_calls == [
        ToolCall(id="call_abc", name="lookup_rule", arguments={"rule_id": "LIAB-1"})
    ]
    assert http.body(0)["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup_rule",
                "description": "Look up a playbook rule",
                "parameters": tool.parameters,
            },
        }
    ]

    [call] = first.tool_calls
    provider.chat(
        [
            Message.user("Check clause"),
            Message.assistant(tool_calls=[call]),
            Message.tool_result(call, '{"ok": true}'),
        ]
    )

    assistant_msg, tool_msg = http.body(1)["messages"][1:]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["tool_calls"][0]["id"] == "call_abc"
    assert json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"]) == {
        "rule_id": "LIAB-1"
    }
    assert tool_msg == {"role": "tool", "tool_call_id": "call_abc", "content": '{"ok": true}'}


def test_invalid_tool_arguments_raise_response_error() -> None:
    bad_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "lookup_rule", "arguments": "{not json"},
    }
    http = MockHTTP(
        json_reply(200, _completion({"role": "assistant", "tool_calls": [bad_call]}, "tool_calls"))
    )

    with pytest.raises(LLMResponseError):
        _provider(http).chat([Message.user("x")])


class RiskVerdict(BaseModel):
    severity: str
    score: float


def test_structured_output_request() -> None:
    http = MockHTTP(
        json_reply(
            200, _completion({"role": "assistant", "content": '{"severity":"low","score":0.1}'})
        )
    )

    response = _provider(http).chat([Message.user("Score it")], response_schema=RiskVerdict)

    response_format = http.body()["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "RiskVerdict"
    assert response_format["json_schema"]["schema"]["properties"].keys() == {"severity", "score"}
    assert RiskVerdict.model_validate_json(response.text).score == 0.1


def test_rate_limit_maps_retry_after() -> None:
    http = MockHTTP(
        json_reply(429, {"error": {"message": "slow down"}}, headers={"retry-after": "3"})
    )

    with pytest.raises(LLMRateLimitError) as info:
        _provider(http).chat([Message.user("x")])

    assert info.value.status_code == 429
    assert info.value.retry_after == 3.0


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(500, LLMServerError), (503, LLMServerError), (400, LLMRequestError), (401, LLMRequestError)],
)
def test_http_errors_are_mapped_without_sdk_retries(status: int, error_type: type) -> None:
    http = MockHTTP(json_reply(status, {"error": {"message": "boom"}}))

    with pytest.raises(error_type) as info:
        _provider(http).chat([Message.user("x")])

    assert info.value.status_code == status
    assert len(http.requests) == 1  # the SDK's own retries are disabled
    assert API_KEY not in str(info.value)


@pytest.mark.parametrize("failure", [raise_timeout, raise_connect_error])
def test_transport_failures_are_retryable_server_errors(failure) -> None:
    http = MockHTTP(failure)

    with pytest.raises(LLMServerError):
        _provider(http).chat([Message.user("x")])
