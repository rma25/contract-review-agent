"""The coverage guarantee and the per-batch turn cap."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.types import ChatResponse, Message
from app.models import AuditEvent, Clause, ClauseOutcome, Contract, ContractStatus
from tests.agent.conftest import MakeContract, MakeRunner
from tests.agent.fake_llm import (
    FakeLLMProvider,
    ScriptedReviewer,
    call,
    recorded_clause_ids,
    reply,
    segment_by_headings,
)

FLAGS = {"limitation_of_liability": ("liability-cap", "critical", 0.95)}


def _events(session: Session, contract: Contract, event_type: str) -> list[AuditEvent]:
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.contract_id == contract.id, AuditEvent.event_type == event_type)
        .order_by(AuditEvent.id)
    )
    return list(session.scalars(stmt))


def _status(session: Session, contract: Contract) -> ContractStatus:
    session.expire_all()
    refreshed = session.get(Contract, contract.id)
    assert refreshed is not None
    return refreshed.status


def _failure_reason(session: Session, contract: Contract) -> str:
    return _events(session, contract, "contract.status_changed")[-1].payload["reason"]


def test_every_reviewed_clause_gets_a_recorded_decision(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()

    make_runner(FakeLLMProvider(segment_by_headings, default=ScriptedReviewer(FLAGS)))(contract.id)

    assert _status(db_session, contract) == ContractStatus.AWAITING_REVIEW
    clauses = db_session.scalars(select(Clause).where(Clause.contract_id == contract.id)).all()
    reviewed = [c for c in clauses if c.clause_type != "other"]
    assert len(reviewed) == 10
    assert all(c.agent_reason == "Reviewed against the playbook rule." for c in reviewed)
    assert [c.clause_type for c in reviewed if c.agent_outcome == ClauseOutcome.FLAGGED] == [
        "limitation_of_liability"
    ]
    assert all(c.agent_outcome is None for c in clauses if c.clause_type == "other")

    decisions = _events(db_session, contract, "agent.clause_reviewed")
    assert len(decisions) == 10
    assert set(decisions[0].payload) == {"clause_id", "outcome", "batch", "reason_chars"}
    assert decisions[0].payload["reason_chars"] == len("Reviewed against the playbook rule.")
    assert _events(db_session, contract, "agent.coverage_retry") == []
    completed = _events(db_session, contract, "agent.batch_completed")
    assert [(e.payload["clauses_flagged"], e.payload["clauses_no_issue"]) for e in completed] == [
        (0, 5),
        (1, 4),
    ]


def test_missing_decisions_get_one_retry_for_those_clauses(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    reviewer = ScriptedReviewer(FLAGS, forget=frozenset({"termination"}))
    fake = FakeLLMProvider(segment_by_headings, default=reviewer)

    make_runner(fake)(contract.id)

    assert _status(db_session, contract) == ContractStatus.AWAITING_REVIEW
    termination = db_session.scalars(
        select(Clause).where(Clause.contract_id == contract.id, Clause.clause_type == "termination")
    ).one()
    assert termination.agent_outcome == ClauseOutcome.NO_ISSUE

    retries = _events(db_session, contract, "agent.coverage_retry")
    assert [e.payload for e in retries] == [
        {"batch": 1, "missing_count": 1, "clause_ids": [str(termination.id)]}
    ]
    reminder = next(
        m.content
        for c in fake.calls
        for m in c.messages
        if m.role == "user" and m.content.startswith("You have not recorded")
    )
    assert str(termination.id) in reminder
    assert termination.text[:40] not in reminder  # ids only, never clause text
    batch_one = _events(db_session, contract, "agent.batch_completed")[0].payload
    assert batch_one["coverage_retry"] is True
    assert batch_one["turns"] == 5  # rules, decisions, finish, retry decisions, finish


def test_decisions_still_missing_after_the_retry_fail_the_contract(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    reviewer = ScriptedReviewer(FLAGS, never_record=frozenset({"termination"}))

    make_runner(FakeLLMProvider(segment_by_headings, default=reviewer))(contract.id)

    assert _status(db_session, contract) == ContractStatus.FAILED
    assert _failure_reason(db_session, contract) == "reviewing:IncompleteCoverageError"
    assert len(_events(db_session, contract, "agent.coverage_retry")) == 1
    incomplete = _events(db_session, contract, "agent.coverage_incomplete")
    assert [(e.payload["batch"], e.payload["missing_count"]) for e in incomplete] == [(1, 1)]
    assert _events(db_session, contract, "agent.completed") == []


def test_a_model_that_never_records_decisions_fails_after_one_retry(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    fake = FakeLLMProvider(segment_by_headings, default=reply("Looks fine."))

    make_runner(fake)(contract.id)

    assert _status(db_session, contract) == ContractStatus.FAILED
    assert len(fake.calls) == 1 + 2  # segmentation, first answer, the one retry
    incomplete = _events(db_session, contract, "agent.coverage_incomplete")[0].payload
    assert incomplete["missing_count"] == 5


def test_turn_cap_fails_a_batch_with_missing_decisions(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    looping = reply(tool_calls=[call("get_playbook_rules", clause_type="termination")])
    fake = FakeLLMProvider(segment_by_headings, default=looping)

    make_runner(fake, agent_max_turns_per_batch=3, agent_max_iterations=25)(contract.id)

    assert _status(db_session, contract) == ContractStatus.FAILED
    assert len(fake.calls) == 1 + 3
    limit = _events(db_session, contract, "agent.batch_turn_limit")
    assert [e.payload for e in limit] == [{"batch": 1, "turns": 3, "decided": 0, "missing": 5}]
    # The cap used up the batch's turns, so there is no retry.
    assert _events(db_session, contract, "agent.coverage_retry") == []
    assert _failure_reason(db_session, contract) == "reviewing:IncompleteCoverageError"


def test_turn_cap_with_full_coverage_completes_the_batch(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    reviewer = ScriptedReviewer(FLAGS)

    def keeps_calling_tools(messages: list[Message], *args: object) -> ChatResponse:
        response = reviewer(messages, *args)  # type: ignore[arg-type]
        batch_done = recorded_clause_ids(messages) and not response.tool_calls
        if batch_done:
            return reply(tool_calls=[call("get_playbook_rules", clause_type="termination")])
        return response

    fake = FakeLLMProvider(segment_by_headings, default=keeps_calling_tools)

    make_runner(fake, agent_max_turns_per_batch=3)(contract.id)

    assert _status(db_session, contract) == ContractStatus.AWAITING_REVIEW
    limit = _events(db_session, contract, "agent.batch_turn_limit")
    assert [(e.payload["batch"], e.payload["missing"]) for e in limit] == [(1, 0), (2, 0)]
    assert len(fake.calls) == 1 + 3 + 3
