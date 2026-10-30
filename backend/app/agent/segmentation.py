import uuid
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.agent.budget import AgentBudget, AgentError
from app.agent.events import record_agent_event
from app.llm.audited import AuditedLLMProvider
from app.llm.types import Message
from app.models import Clause
from app.playbook import OTHER_CLAUSE_TYPE, Playbook
from app.prompts import load_prompt
from app.repositories.clause_repository import ClauseRepository

MAX_HEADING_CHARS = 512


class SegmentationError(AgentError):
    """The model's segmentation could not be parsed or contained no clauses."""


class SegmentedClause(BaseModel):
    heading: str = ""
    text: str
    clause_type: str


class SegmentationResult(BaseModel):
    clauses: list[SegmentedClause] = Field(default_factory=list)


def segmentation_schema(clause_types: list[str]) -> dict[str, Any]:
    """JSON schema for structured output, with clause_type restricted to the playbook."""
    return {
        "title": "SegmentationResult",
        "type": "object",
        "properties": {
            "clauses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "text": {"type": "string"},
                        "clause_type": {"type": "string", "enum": clause_types},
                    },
                    "required": ["heading", "text", "clause_type"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["clauses"],
        "additionalProperties": False,
    }


def segment_contract(
    session: Session,
    llm: AuditedLLMProvider,
    playbook: Playbook,
    *,
    contract_id: uuid.UUID,
    raw_text: str,
    budget: AgentBudget,
) -> list[Clause]:
    """Split contract text into ordered, typed clauses with one structured LLM call.

    Replaces any clauses from an earlier run and commits.
    """
    prompt = load_prompt("clause_segmentation")
    clause_types = playbook.segmentation_types
    messages = [
        Message.system(prompt.render(clause_types=", ".join(clause_types))),
        Message.user(raw_text),
    ]

    budget.before_call()
    response = llm.chat(
        messages,
        response_schema=segmentation_schema(clause_types),
        prompt=prompt,
        contract_id=contract_id,
    )
    budget.charge(response)

    try:
        result = SegmentationResult.model_validate_json(response.text)
    except ValidationError:
        raise SegmentationError("Segmentation response did not match the schema") from None

    known_types = set(clause_types)
    clauses = [
        Clause(
            heading=item.heading.strip()[:MAX_HEADING_CHARS] or None,
            text=item.text.strip(),
            clause_type=item.clause_type if item.clause_type in known_types else OTHER_CLAUSE_TYPE,
        )
        for item in result.clauses
        if item.text.strip()
    ]
    if not clauses:
        raise SegmentationError("Segmentation returned no clauses")

    ClauseRepository(session).replace_for_contract(contract_id, clauses)
    record_agent_event(
        session,
        "agent.clauses_segmented",
        contract_id,
        {
            "clause_count": len(clauses),
            "by_type": dict(Counter(clause.clause_type for clause in clauses)),
            "prompt_name": prompt.name,
            "prompt_version": prompt.version,
        },
    )
    session.commit()
    return clauses
