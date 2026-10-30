from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]

# A Pydantic model class or a raw JSON schema dict.
ResponseSchema = type[BaseModel] | dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Opaque provider data that must be echoed back on the next turn
    # (e.g. Gemini thought signatures). Never shown to users.
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Set on role="tool" messages: which call this result answers, and the tool's name.
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: list[ToolCall] | None = None) -> "Message":
        return cls(role="assistant", content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool_result(cls, call: ToolCall, content: str) -> "Message":
        return cls(role="tool", content=content, tool_call_id=call.id, name=call.name)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    provider: str
    model: str
    finish_reason: str | None = None


def schema_to_json(schema: ResponseSchema) -> dict[str, Any]:
    if isinstance(schema, dict):
        return schema
    return schema.model_json_schema()


def schema_name(schema: ResponseSchema) -> str:
    if isinstance(schema, dict):
        return str(schema.get("title", "response"))
    return schema.__name__
