"""A scripted LLM provider for agent tests. Never touches the network."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import LLMError
from app.llm.types import ChatResponse, Message, ResponseSchema, TokenUsage, ToolCall, ToolSpec

Script = Callable[[list[Message], list[ToolSpec] | None, ResponseSchema | None], ChatResponse]
Step = ChatResponse | LLMError | Script


@dataclass
class RecordedCall:
    messages: list[Message]
    tools: list[ToolSpec] | None
    response_schema: ResponseSchema | None


class FakeLLMProvider:
    """Plays back scripted steps in order: a ChatResponse, an LLMError to raise, or a
    callable that builds the response from the request. `default` handles calls after
    the script runs out."""

    name = "fake"

    def __init__(self, *steps: Step, default: Step | None = None, model: str = "fake-1") -> None:
        self.model = model
        self.steps: list[Step] = list(steps)
        self.default = default
        self.calls: list[RecordedCall] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: ResponseSchema | None = None,
    ) -> ChatResponse:
        self.calls.append(RecordedCall(list(messages), tools, response_schema))
        if self.steps:
            step = self.steps.pop(0)
        elif self.default is not None:
            step = self.default
        else:
            raise AssertionError("FakeLLMProvider ran out of scripted responses")
        if isinstance(step, LLMError):
            raise step
        if isinstance(step, ChatResponse):
            return step
        return step(messages, tools, response_schema)


def reply(
    text: str = "", *, tool_calls: list[ToolCall] | None = None, tokens: int = 100
) -> ChatResponse:
    return ChatResponse(
        text=text,
        tool_calls=tool_calls or [],
        usage=TokenUsage(input_tokens=tokens - 10, output_tokens=10, total_tokens=tokens),
        provider="fake",
        model="fake-1",
        finish_reason="STOP",
    )


def call(name: str, call_id: str | None = None, **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id or f"call_{name}", name=name, arguments=arguments)


# --- Scripted behaviors -------------------------------------------------------------------

_NUMBERED_HEADING = re.compile(r"^\d+\.\s+(.+)$")

HEADING_TYPES = {
    "limitation of liability": "limitation_of_liability",
    "indemnification": "indemnification",
    "term and automatic renewal": "auto_renewal",
    "term and renewal": "auto_renewal",
    "termination": "termination",
    "governing law": "governing_law",
    "governing law and disputes": "governing_law",
    "confidentiality": "confidentiality",
    "fees and payment": "payment_terms",
    "intellectual property": "ip_ownership",
    "data protection": "data_protection",
    "non-competition": "non_compete",
}


def segment_by_headings(
    messages: list[Message], _tools: object, _schema: object, *, tokens: int = 500
) -> ChatResponse:
    """Segment the contract text in the user message on numbered headings ("4. Termination")."""
    text = next(m.content for m in messages if m.role == "user")
    clauses: list[dict[str, str]] = []
    for line in text.splitlines():
        match = _NUMBERED_HEADING.match(line.strip())
        if match:
            title = match.group(1).strip()
            clauses.append(
                {
                    "heading": line.strip(),
                    "text": "",
                    "clause_type": HEADING_TYPES.get(title.lower(), "other"),
                }
            )
        elif clauses and line.strip():
            clauses[-1]["text"] = (clauses[-1]["text"] + " " + line.strip()).strip()
    return reply(json.dumps({"clauses": clauses}), tokens=tokens)


@dataclass
class ScriptedReviewer:
    """Acts like a well-behaved reviewer: fetch rules for each clause type, flag the clause
    types in `flag`, then finish. Turn order is inferred from the conversation so far."""

    flag: dict[str, tuple[str, str, float]] = field(default_factory=dict)
    tokens: int = 200

    def __call__(self, messages: list[Message], _tools: object, _schema: object) -> ChatResponse:
        batch = json.loads(messages[1].content.split("\n", 1)[1])
        last = messages[-1]
        if last.role == "user":
            types = sorted({clause["clause_type"] for clause in batch})
            return reply(
                tool_calls=[call("get_playbook_rules", f"rules_{t}", clause_type=t) for t in types],
                tokens=self.tokens,
            )
        if last.role == "tool" and last.name == "get_playbook_rules":
            calls = [
                call(
                    "create_risk_flag",
                    f"flag_{clause['clause_id']}",
                    clause_id=clause["clause_id"],
                    rule_id=self.flag[clause["clause_type"]][0],
                    severity=self.flag[clause["clause_type"]][1],
                    score=self.flag[clause["clause_type"]][2],
                    rationale="Meets the rule's risk criteria.",
                )
                for clause in batch
                if clause["clause_type"] in self.flag
            ]
            if calls:
                return reply(tool_calls=calls, tokens=self.tokens)
        return reply("Batch reviewed.", tokens=self.tokens)
