"""A scripted LLM provider for agent tests. Never touches the network."""

import html
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


# --- Reading requests ---------------------------------------------------------------------

_CONTRACT_BLOCK = re.compile(r"<contract>\n(.*)\n</contract>", re.DOTALL)
_CLAUSE_BLOCK = re.compile(
    r'<clause id="([^"]+)" type="([^"]+)">\n'
    r"<heading>(.*?)</heading>\n<text>(.*?)</text>\n</clause>",
    re.DOTALL,
)


def unwrap_contract(content: str) -> str:
    """The contract text of a segmentation request, still escaped as the model sees it."""
    match = _CONTRACT_BLOCK.fullmatch(content)
    assert match, "the contract must be wrapped in <contract> tags"
    return match.group(1)


def parse_batch(content: str) -> list[dict[str, str]]:
    """The clause blocks of a review batch message, as a model would read them."""
    return [
        {
            "clause_id": clause_id,
            "clause_type": html.unescape(clause_type),
            "heading": heading,
            "text": text,
        }
        for clause_id, clause_type, heading, text in _CLAUSE_BLOCK.findall(content)
    ]


def recorded_clause_ids(messages: list[Message]) -> set[str]:
    """Clause ids with a successful record_clause_reviewed result in the conversation."""
    recorded: set[str] = set()
    for message in messages:
        if message.role == "tool" and message.name == "record_clause_reviewed":
            result = json.loads(message.content)
            if result["ok"]:
                recorded.add(result["clause_id"])
    return recorded


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
    text = unwrap_contract(next(m.content for m in messages if m.role == "user"))
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
    """Acts like a well-behaved reviewer: fetch rules for each clause type, then flag the
    clause types in `flag` and record a decision for every clause in the same turn, then
    finish. Turn order is inferred from the conversation so far.

    Clause types in `forget` get no decision until the coverage reminder arrives; clause
    types in `never_record` never get one.
    """

    flag: dict[str, tuple[str, str, float]] = field(default_factory=dict)
    tokens: int = 200
    forget: frozenset[str] = frozenset()
    never_record: frozenset[str] = frozenset()

    def __call__(self, messages: list[Message], _tools: object, _schema: object) -> ChatResponse:
        batch = parse_batch(messages[1].content)
        if not any(m.role == "tool" and m.name == "get_playbook_rules" for m in messages):
            types = sorted({clause["clause_type"] for clause in batch})
            return reply(
                tool_calls=[call("get_playbook_rules", f"rules_{t}", clause_type=t) for t in types],
                tokens=self.tokens,
            )
        reminded = sum(m.role == "user" for m in messages) > 1
        recorded = recorded_clause_ids(messages)
        todo = [
            clause
            for clause in batch
            if clause["clause_id"] not in recorded
            and clause["clause_type"] not in self.never_record
            and (reminded or clause["clause_type"] not in self.forget)
        ]
        # Finish after a decision turn, or once nothing is left to decide.
        if not todo or messages[-1].name == "record_clause_reviewed":
            return reply("Batch reviewed.", tokens=self.tokens)
        flags = [
            call(
                "create_risk_flag",
                f"flag_{clause['clause_id']}",
                clause_id=clause["clause_id"],
                rule_id=self.flag[clause["clause_type"]][0],
                severity=self.flag[clause["clause_type"]][1],
                score=self.flag[clause["clause_type"]][2],
                rationale="Meets the rule's risk criteria.",
            )
            for clause in todo
            if clause["clause_type"] in self.flag
        ]
        decisions = [
            call(
                "record_clause_reviewed",
                f"record_{clause['clause_id']}",
                clause_id=clause["clause_id"],
                outcome="flagged" if clause["clause_type"] in self.flag else "no_issue",
                reason="Reviewed against the playbook rule.",
            )
            for clause in todo
        ]
        return reply(tool_calls=flags + decisions, tokens=self.tokens)
