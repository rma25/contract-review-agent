import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agent.budget import AgentBudget, AgentError
from app.agent.delimiters import batch_message
from app.agent.events import record_agent_event
from app.agent.tools import AgentTools
from app.llm.audited import AuditedLLMProvider
from app.llm.types import Message
from app.models import Clause, ClauseOutcome
from app.playbook import Playbook
from app.prompts import load_prompt


class IncompleteCoverageError(AgentError):
    """A batch ended without a recorded decision for every clause, even after the retry."""

    def __init__(self, *, batch: int, missing: int) -> None:
        super().__init__(f"Batch {batch} has no decision for {missing} clause(s)")
        self.batch = batch
        self.missing = missing


@dataclass(frozen=True)
class ReviewSummary:
    clauses_reviewed: int
    clauses_skipped: int
    clauses_flagged: int
    clauses_no_issue: int
    batches: int
    flags_created: int


class RiskReviewAgent:
    """Reviews clauses against the playbook in batches, using tools to create risk flags.

    Each batch is its own short conversation, which keeps every request small enough for
    free-tier limits. A batch is complete only when every clause in it has a decision from
    `record_clause_reviewed`: missing decisions get one reminder, then the review fails.
    A batch may take at most `max_turns_per_batch` LLM turns, and the shared budget caps
    LLM calls and tokens for the whole contract.
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
        max_turns_per_batch: int,
    ) -> None:
        self.session = session
        self.llm = llm
        self.playbook = playbook
        self.contract_id = contract_id
        self.budget = budget
        self.batch_size = batch_size
        self.max_turns_per_batch = max_turns_per_batch
        self.tools = AgentTools(session, contract_id, playbook)
        self.prompt = load_prompt("risk_review")

    def review(self, clauses: Sequence[Clause]) -> ReviewSummary:
        reviewable = [c for c in clauses if self.playbook.rules_for(c.clause_type)]
        skipped = len(clauses) - len(reviewable)
        batches = [
            reviewable[i : i + self.batch_size] for i in range(0, len(reviewable), self.batch_size)
        ]
        if skipped:
            self._record("agent.clauses_skipped", {"count": skipped, "reason": "no_playbook_rule"})

        for number, batch in enumerate(batches, start=1):
            self._review_batch(number, batch)

        outcomes = list(self.tools.decisions.values())
        return ReviewSummary(
            clauses_reviewed=len(reviewable),
            clauses_skipped=skipped,
            clauses_flagged=outcomes.count(ClauseOutcome.FLAGGED),
            clauses_no_issue=outcomes.count(ClauseOutcome.NO_ISSUE),
            batches=len(batches),
            flags_created=self.tools.flags_created,
        )

    def _review_batch(self, number: int, batch: Sequence[Clause]) -> None:
        flags_before = self.tools.flags_created
        self.tools.active_clause_ids = {clause.id for clause in batch}
        self._record(
            "agent.batch_started",
            {"batch": number, "clause_count": len(batch), "clause_ids": [str(c.id) for c in batch]},
        )

        messages = [
            Message.system(
                self.prompt.render(
                    playbook_version=self.playbook.version,
                    clause_types=", ".join(self.playbook.clause_types),
                )
            ),
            Message.user(batch_message(batch)),
        ]
        turns = 0
        finish_reason: str | None = None
        retried = False
        while True:
            turns, finish_reason, hit_limit = self._converse(messages, number, turns)
            missing = self._missing(batch)
            if hit_limit:
                self._record(
                    "agent.batch_turn_limit",
                    {
                        "batch": number,
                        "turns": turns,
                        "decided": len(batch) - len(missing),
                        "missing": len(missing),
                    },
                )
                break
            if not missing or retried:
                break
            retried = True
            self._record(
                "agent.coverage_retry",
                {"batch": number, "missing_count": len(missing), "clause_ids": missing},
            )
            messages.append(Message.user(_coverage_reminder(missing)))

        if missing:
            self._record(
                "agent.coverage_incomplete",
                {"batch": number, "missing_count": len(missing), "clause_ids": missing},
            )
            raise IncompleteCoverageError(batch=number, missing=len(missing))

        outcomes = [self.tools.decisions[clause.id] for clause in batch]
        self._record(
            "agent.batch_completed",
            {
                "batch": number,
                "clause_count": len(batch),
                "turns": turns,
                "flags_created": self.tools.flags_created - flags_before,
                "clauses_flagged": outcomes.count(ClauseOutcome.FLAGGED),
                "clauses_no_issue": outcomes.count(ClauseOutcome.NO_ISSUE),
                "coverage_retry": retried,
                "finish_reason": finish_reason,
            },
        )

    def _converse(
        self, messages: list[Message], number: int, turns: int
    ) -> tuple[int, str | None, bool]:
        """Run LLM turns until the model stops calling tools or the batch turn cap is hit.

        Returns the batch's turn count so far, the last finish reason, and whether the cap
        was hit.
        """
        finish_reason: str | None = None
        while turns < self.max_turns_per_batch:
            self.budget.before_call()
            response = self.llm.chat(
                messages, tools=self.tools.specs, prompt=self.prompt, contract_id=self.contract_id
            )
            self.budget.charge(response)
            turns += 1
            finish_reason = response.finish_reason
            messages.append(Message.assistant(response.text, response.tool_calls))
            if not response.tool_calls:
                return turns, finish_reason, False
            for call in response.tool_calls:
                result = self.tools.execute(call, batch=number)
                messages.append(Message.tool_result(call, json.dumps(result)))
        return turns, finish_reason, True

    def _missing(self, batch: Sequence[Clause]) -> list[str]:
        return [str(clause.id) for clause in batch if clause.id not in self.tools.decisions]

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        record_agent_event(self.session, event_type, self.contract_id, payload)
        self.session.commit()


def _coverage_reminder(missing: Sequence[str]) -> str:
    """The retry prompt. It names clause ids only, never clause text."""
    return (
        "You have not recorded a decision for these clauses: "
        + ", ".join(missing)
        + ". For each one, create any risk flags it needs, then call record_clause_reviewed. "
        "Clauses that already have a decision are done."
    )
