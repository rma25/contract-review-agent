import json
from typing import Any

import httpx2
import openai

from app.llm.base import (
    LLMError,
    LLMResponseError,
    LLMServerError,
    error_for_status,
    parse_retry_after,
)
from app.llm.types import (
    ChatResponse,
    Message,
    ResponseSchema,
    TokenUsage,
    ToolCall,
    ToolSpec,
    schema_name,
    schema_to_json,
)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider:
    """Groq through its OpenAI-compatible API. Works for any OpenAI-compatible endpoint."""

    name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = GROQ_BASE_URL,
        timeout_seconds: float = 60.0,
        temperature: float | None = None,
        http_client: httpx2.Client | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,  # we retry in app.llm.retry
            http_client=http_client,
        )

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> ChatResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_openai_message(m) for m in messages],
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if tools:
            kwargs["tools"] = [
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
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name(response_schema),
                    "schema": schema_to_json(response_schema),
                },
            }

        try:
            completion = self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:
            raise _map_status_error(exc) from None
        except openai.APITimeoutError:
            raise LLMServerError("groq request timed out") from None
        except openai.APIConnectionError:
            raise LLMServerError("groq connection error") from None

        if not completion.choices:
            raise LLMResponseError("groq returned no choices")
        choice = completion.choices[0]
        usage = completion.usage
        return ChatResponse(
            text=choice.message.content or "",
            tool_calls=[_to_tool_call(call) for call in choice.message.tool_calls or []],
            usage=TokenUsage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            ),
            provider=self.name,
            model=completion.model or self.model,
            finish_reason=choice.finish_reason,
        )


def _to_openai_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ],
        }
    return {"role": message.role, "content": message.content}


def _to_tool_call(call: Any) -> ToolCall:
    try:
        arguments = json.loads(call.function.arguments or "{}")
    except ValueError:
        raise LLMResponseError("groq returned invalid JSON arguments for tool call") from None
    if not isinstance(arguments, dict):
        raise LLMResponseError("groq returned non-object tool arguments")
    return ToolCall(id=call.id, name=call.function.name, arguments=arguments)


def _map_status_error(exc: openai.APIStatusError) -> LLMError:
    retry_after = parse_retry_after(exc.response.headers.get("retry-after"))
    return error_for_status("groq", exc.status_code, retry_after)
