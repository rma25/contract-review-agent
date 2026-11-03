import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent.budget import AgentBudget
from app.agent.events import record_agent_event
from app.agent.tools import AgentTools
from app.llm.audited import AuditedLLMProvider
from app.llm.types import Message
from app.models import Clause
from app.playbook import Playbook
from app.prompts import load_prompt


@dataclass(frozen=True)
class ReviewSummary:
    clauses_reviewed: int
    clauses_skipped: int
    batches: int
    flags_created: int


class RiskReviewAgent:
    """Reviews clauses against the playbook in batches, using tools to create risk flags.

    Each batch is its own short conversation, which keeps every request small enough for
    free-tier limits. The shared budget caps LLM calls and tokens for the whole contract.
    """

    def __init__(
        self,
        session: Session,
        llm: AuditedLLMProvider,
        playbook: Playbook,
        *,
        contract_id: uuid.UUID,
        budget: AgentBudget,
        batch_size: int,
    ) -> None:
        self.session = session
        self.llm = llm
        self.playbook = playbook
        self.contract_id = contract_id
        self.budget = budget
        self.batch_size = batch_size
        self.tools = AgentTools(session, contract_id, playbook)
        self.prompt = load_prompt("risk_review")

    def review(self, clauses: Sequence[Clause]) -> ReviewSummary:
        reviewable = [c for c in clauses if self.playbook.rules_for(c.clause_type)]
        skipped = len(clauses) - len(reviewable)
        batches = [
            reviewable[i : i + self.batch_size] for i in range(0, len(reviewable), self.batch_size)
        ]
        if skipped:
            record_agent_event(
                self.session,
                "agent.clauses_skipped",
                self.contract_id,
                {"count": skipped, "reason": "no_playbook_rule"},
            )
            self.session.commit()

        for number, batch in enumerate(batches, start=1):
            self._review_batch(number, batch)

        return ReviewSummary(
            clauses_reviewed=len(reviewable),
            clauses_skipped=skipped,
            batches=len(batches),
            flags_created=self.tools.flags_created,
        )

    def _review_batch(self, number: int, batch: Sequence[Clause]) -> None:
        flags_before = self.tools.flags_created
        self.tools.active_clause_ids = {clause.id for clause in batch}
        record_agent_event(
            self.session,
            "agent.batch_started",
            self.contract_id,
            {"batch": number, "clause_count": len(batch), "clause_ids": [str(c.id) for c in batch]},
        )
        self.session.commit()

        messages = [
            Message.system(
                self.prompt.render(
                    playbook_version=self.playbook.version,
                    clause_types=", ".join(self.playbook.clause_types),
                )
            ),
            Message.user(_batch_message(batch)),
        ]
        turns = 0
        while True:
            self.budget.before_call()
            response = self.llm.chat(
                messages, tools=self.tools.specs, prompt=self.prompt, contract_id=self.contract_id
            )
            self.budget.charge(response)
            turns += 1
            messages.append(Message.assistant(response.text, response.tool_calls))
            if not response.tool_calls:
                break
            for call in response.tool_calls:
                result = self.tools.execute(call, batch=number)
                messages.append(Message.tool_result(call, json.dumps(result)))

        record_agent_event(
            self.session,
            "agent.batch_completed",
            self.contract_id,
            {
                "batch": number,
                "clause_count": len(batch),
                "turns": turns,
                "flags_created": self.tools.flags_created - flags_before,
                "finish_reason": response.finish_reason,
            },
        )
        self.session.commit()


def _batch_message(batch: Sequence[Clause]) -> str:
    clauses = [
        {
            "clause_id": str(clause.id),
            "clause_type": clause.clause_type,
            "heading": clause.heading or "",
            "text": clause.text,
        }
        for clause in batch
    ]
    return "Review these clauses:\n" + json.dumps(clauses, ensure_ascii=False, indent=1)
