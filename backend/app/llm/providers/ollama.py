import uuid
from typing import Any

import httpx2

from app.llm.base import LLMResponseError, LLMServerError, error_for_status, parse_retry_after
from app.llm.types import (
    ChatResponse,
    Message,
    ResponseSchema,
    TokenUsage,
    ToolCall,
    ToolSpec,
    schema_to_json,
)


class OllamaProvider:
    """A local Ollama server via its HTTP API (POST /api/chat, non-streaming)."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        temperature: float | None = None,
        http_client: httpx2.Client | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._chat_url = f"{base_url.rstrip('/')}/api/chat"
        self._client = http_client or httpx2.Client()
        self._timeout = timeout_seconds

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> ChatResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_ollama_message(m) for m in messages],
            "stream": False,
        }
        if self.temperature is not None:
            body["options"] = {"temperature": self.temperature}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        if response_schema is not None:
            body["format"] = schema_to_json(response_schema)

        try:
            response = self._client.post(self._chat_url, json=body, timeout=self._timeout)
        except httpx2.TimeoutException:
            raise LLMServerError("ollama request timed out") from None
        except httpx2.TransportError as exc:
            raise LLMServerError(f"ollama transport error: {type(exc).__name__}") from None

        if response.status_code >= 400:
            raise error_for_status(
                "ollama",
                response.status_code,
                parse_retry_after(response.headers.get("retry-after")),
            )

        try:
            data = response.json()
            message = data["message"]
        except (ValueError, KeyError, TypeError):
            raise LLMResponseError("ollama returned an unexpected response body") from None

        input_tokens = int(data.get("prompt_eval_count") or 0)
        output_tokens = int(data.get("eval_count") or 0)
        return ChatResponse(
            text=message.get("content") or "",
            tool_calls=[_to_tool_call(call) for call in message.get("tool_calls") or []],
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
            provider=self.name,
            model=data.get("model") or self.model,
            finish_reason=data.get("done_reason"),
        )


def _to_ollama_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "content": message.content, "tool_name": message.name}
    result: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.role == "assistant" and message.tool_calls:
        result["tool_calls"] = [
            {"function": {"name": call.name, "arguments": call.arguments}}
            for call in message.tool_calls
        ]
    return result


def _to_tool_call(call: dict[str, Any]) -> ToolCall:
    function = call.get("function") or {}
    arguments = function.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise LLMResponseError("ollama returned non-object tool arguments")
    return ToolCall(
        id=call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
        name=function.get("name", ""),
        arguments=arguments,
    )
