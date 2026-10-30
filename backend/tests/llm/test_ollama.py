import pytest
from pydantic import BaseModel

from app.llm.base import LLMRequestError, LLMResponseError, LLMServerError
from app.llm.providers.ollama import OllamaProvider
from app.llm.types import Message, ToolSpec
from tests.llm.mock_http import MockHTTP, json_reply, raise_connect_error, raise_timeout

BASE_URL = "http://ollama.test:11434/"


def _provider(http: MockHTTP) -> OllamaProvider:
    return OllamaProvider(
        base_url=BASE_URL, model="llama-test", temperature=0.0, http_client=http.client
    )


def _chat_reply(message: dict, done_reason: str = "stop") -> dict:
    return {
        "model": "llama-test",
        "message": {"role": "assistant", **message},
        "done": True,
        "done_reason": done_reason,
        "prompt_eval_count": 11,
        "eval_count": 6,
    }


def test_text_reply_and_request_shape() -> None:
    http = MockHTTP(json_reply(200, _chat_reply({"content": "Hello"})))

    response = _provider(http).chat([Message.system("Be terse."), Message.user("Hi")])

    assert response.text == "Hello"
    assert response.provider == "ollama"
    assert response.model == "llama-test"
    assert response.finish_reason == "stop"
    assert response.usage.model_dump() == {
        "input_tokens": 11,
        "output_tokens": 6,
        "total_tokens": 17,
    }

    assert str(http.last.url) == "http://ollama.test:11434/api/chat"
    assert "authorization" not in http.last.headers
    assert http.body() == {
        "model": "llama-test",
        "messages": [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Hi"},
        ],
        "stream": False,
        "options": {"temperature": 0.0},
    }


def test_tool_call_round_trip() -> None:
    http = MockHTTP(
        json_reply(
            200,
            _chat_reply(
                {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "lookup_rule", "arguments": {"rule_id": "LIAB-1"}}}
                    ],
                }
            ),
        ),
        json_reply(200, _chat_reply({"content": "done"})),
    )
    provider = _provider(http)
    tool = ToolSpec(name="lookup_rule", description="Look up a playbook rule")

    first = provider.chat([Message.user("Check clause")], tools=[tool])

    [call] = first.tool_calls
    assert call.name == "lookup_rule"
    assert call.arguments == {"rule_id": "LIAB-1"}
    assert call.id.startswith("call_")
    assert http.body(0)["tools"][0]["function"]["name"] == "lookup_rule"

    provider.chat(
        [
            Message.user("Check clause"),
            Message.assistant(tool_calls=[call]),
            Message.tool_result(call, '{"ok": true}'),
        ]
    )

    assistant_msg, tool_msg = http.body(1)["messages"][1:]
    assert assistant_msg["tool_calls"] == [
        {"function": {"name": "lookup_rule", "arguments": {"rule_id": "LIAB-1"}}}
    ]
    assert tool_msg == {"role": "tool", "content": '{"ok": true}', "tool_name": "lookup_rule"}


class RiskVerdict(BaseModel):
    severity: str
    score: float


def test_structured_output_request() -> None:
    http = MockHTTP(json_reply(200, _chat_reply({"content": '{"severity":"medium","score":0.5}'})))

    response = _provider(http).chat([Message.user("Score it")], response_schema=RiskVerdict)

    assert http.body()["format"]["properties"].keys() == {"severity", "score"}
    assert RiskVerdict.model_validate_json(response.text).severity == "medium"


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(500, LLMServerError), (503, LLMServerError), (404, LLMRequestError)],
)
def test_http_errors_are_mapped(status: int, error_type: type) -> None:
    http = MockHTTP(json_reply(status, {"error": "model not found"}))

    with pytest.raises(error_type) as info:
        _provider(http).chat([Message.user("x")])

    assert info.value.status_code == status


@pytest.mark.parametrize("failure", [raise_timeout, raise_connect_error])
def test_transport_failures_are_retryable_server_errors(failure) -> None:
    http = MockHTTP(failure)

    with pytest.raises(LLMServerError):
        _provider(http).chat([Message.user("x")])


def test_unexpected_body_raises_response_error() -> None:
    http = MockHTTP(json_reply(200, {"unexpected": True}))

    with pytest.raises(LLMResponseError):
        _provider(http).chat([Message.user("x")])
