import base64
import json
import uuid
from typing import Any

import httpx2
from google import genai
from google.genai import errors, types

from app.llm.base import (
    LLMError,
    LLMRequestError,
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
    schema_to_json,
)

THOUGHT_SIGNATURE_KEY = "gemini_thought_signature"


class GeminiProvider:
    """Google Gemini via the google-genai SDK (Gemini API, not Vertex AI)."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        temperature: float | None = None,
        http_client: httpx2.Client | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),  # milliseconds
                retry_options=types.HttpRetryOptions(attempts=1),  # we retry in app.llm.retry
                httpx_client=http_client,
            ),
        )

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> ChatResponse:
        system_instruction, contents = _to_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=self.temperature,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if tools:
            config.tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool.name,
                            description=tool.description,
                            parameters_json_schema=tool.parameters,
                        )
                        for tool in tools
                    ]
                )
            ]
        if response_schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = schema_to_json(response_schema)

        try:
            response = self._client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except errors.APIError as exc:
            raise _map_api_error(exc, self.model) from None
        except httpx2.TimeoutException:
            raise LLMServerError("gemini request timed out") from None
        except httpx2.TransportError as exc:
            raise LLMServerError(f"gemini transport error: {type(exc).__name__}") from None

        return self._to_response(response)

    def _to_response(self, response: types.GenerateContentResponse) -> ChatResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        finish_reason: str | None = None

        if response.candidates:
            candidate = response.candidates[0]
            finish_reason = _enum_value(candidate.finish_reason)
            parts = candidate.content.parts if candidate.content and candidate.content.parts else []
            for part in parts:
                if part.function_call is not None:
                    metadata: dict[str, Any] = {}
                    if part.thought_signature:
                        metadata[THOUGHT_SIGNATURE_KEY] = base64.b64encode(
                            part.thought_signature
                        ).decode("ascii")
                    tool_calls.append(
                        ToolCall(
                            id=part.function_call.id or f"call_{uuid.uuid4().hex[:12]}",
                            name=part.function_call.name or "",
                            arguments=dict(part.function_call.args or {}),
                            provider_metadata=metadata,
                        )
                    )
                elif part.text and not part.thought:
                    text_parts.append(part.text)
        elif response.prompt_feedback and response.prompt_feedback.block_reason:
            finish_reason = f"blocked:{_enum_value(response.prompt_feedback.block_reason)}"

        usage = response.usage_metadata
        input_tokens = (usage.prompt_token_count or 0) if usage else 0
        output_tokens = (
            ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
            if usage
            else 0
        )
        total_tokens = (usage.total_token_count or 0) if usage else 0

        return ChatResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens or input_tokens + output_tokens,
            ),
            provider=self.name,
            model=response.model_version or self.model,
            finish_reason=finish_reason,
        )


def _to_contents(messages: list[Message]) -> tuple[str | None, list[types.Content]]:
    system_parts = [m.content for m in messages if m.role == "system"]
    contents: list[types.Content] = []
    for message in messages:
        if message.role == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=message.content)]))
        elif message.role == "assistant":
            parts: list[types.Part] = []
            if message.content:
                parts.append(types.Part(text=message.content))
            for call in message.tool_calls:
                signature = call.provider_metadata.get(THOUGHT_SIGNATURE_KEY)
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            id=call.id, name=call.name, args=call.arguments
                        ),
                        thought_signature=base64.b64decode(signature) if signature else None,
                    )
                )
            contents.append(types.Content(role="model", parts=parts))
        elif message.role == "tool":
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=message.tool_call_id,
                                name=message.name,
                                response=_tool_response_payload(message.content),
                            )
                        )
                    ],
                )
            )
    return ("\n\n".join(system_parts) or None), contents


def _tool_response_payload(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except ValueError:
        return {"result": content}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _map_api_error(exc: errors.APIError, model: str) -> LLMError:
    status = exc.code or 0
    if status == 404:
        return LLMRequestError(f"gemini model not found: {model}", status_code=404)
    retry_after = None
    headers = getattr(exc.response, "headers", None)
    if headers is not None:
        retry_after = parse_retry_after(headers.get("retry-after"))
    if retry_after is None:
        retry_after = _retry_info_delay(exc.details)
    if not status:
        return LLMServerError("gemini returned an error without a status code")
    return error_for_status("gemini", status, retry_after)


def _retry_info_delay(details: Any) -> float | None:
    """Read google.rpc.RetryInfo ("retryDelay": "17s") from an error body, if present."""
    error = details.get("error", details) if isinstance(details, dict) else {}
    for detail in error.get("details", []) if isinstance(error, dict) else []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            return parse_retry_after(delay[:-1])
    return None


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value)).lower()
