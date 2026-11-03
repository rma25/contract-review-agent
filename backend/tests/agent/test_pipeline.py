import json
from functools import partial

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import LLMServerError
from app.llm.types import Message
from app.models import AuditEvent, Clause, Contract, ContractStatus, FlagStatus, RiskFlag
from tests.agent.conftest import MakeContract, MakeRunner
from tests.agent.fake_llm import (
    FakeLLMProvider,
    ScriptedReviewer,
    call,
    parse_batch,
    reply,
    segment_by_headings,
)
from tests.contract_fixtures import LOW_RISK

HIGH_RISK_FLAGS = {
    "limitation_of_liability": ("liability-cap", "critical", 0.95),
    "data_protection": ("data-protection", "critical", 0.9),
    "termination": ("termination", "high", 0.8),
}


def _events(session: Session, contract: Contract) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.contract_id == contract.id).order_by(AuditEvent.id)
    return list(session.scalars(stmt))


def _status_changes(session: Session, contract: Contract) -> list[tuple[str, str]]:
    return [
        (e.payload["from"], e.payload["to"])
        for e in _events(session, contract)
        if e.event_type == "contract.status_changed"
    ]


def _refresh(session: Session, contract: Contract) -> Contract:
    session.expire_all()
    refreshed = session.get(Contract, contract.id)
    assert refreshed is not None
    return refreshed


def test_analysis_runs_end_to_end(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    fake = FakeLLMProvider(segment_by_headings, default=ScriptedReviewer(HIGH_RISK_FLAGS))

    make_runner(fake)(contract.id)

    contract = _refresh(db_session, contract)
    assert contract.status == ContractStatus.AWAITING_REVIEW
    assert contract.raw_text is not None
    assert "Master Vendor Services Agreement" in contract.raw_text

    clauses = db_session.scalars(
        select(Clause).where(Clause.contract_id == contract.id).order_by(Clause.ordinal)
    ).all()
    assert [c.ordinal for c in clauses] == list(range(1, 13))
    assert clauses[0].heading == "1. Services"
    assert clauses[8].clause_type == "limitation_of_liability"

    flags = db_session.scalars(
        select(RiskFlag).join(RiskFlag.clause).where(Clause.contract_id == contract.id)
    ).all()
    assert sorted(f.rule_id for f in flags) == ["data-protection", "liability-cap", "termination"]
    assert {f.status for f in flags} == {FlagStatus.PENDING}

    assert _status_changes(db_session, contract) == [
        ("extracting", "analyzing"),
        ("analyzing", "awaiting_review"),
    ]
    types = [e.event_type for e in _events(db_session, contract)]
    assert types[:4] == [
        "contract.text_extracted",
        "contract.status_changed",
        "llm.call.succeeded",
        "agent.clauses_segmented",
    ]
    assert types.count("agent.flag_created") == 3
    assert types[-2:] == ["agent.completed", "contract.status_changed"]

    completed = next(e for e in _events(db_session, contract) if e.event_type == "agent.completed")
    # 12 clauses: 10 typed + "Services" and "Entire Agreement" skipped as "other".
    assert completed.payload | {"total_tokens": 0} == {
        "playbook_version": 1,
        "clause_count": 12,
        "clauses_reviewed": 10,
        "clauses_skipped": 2,
        "clauses_flagged": 3,
        "clauses_no_issue": 7,
        "batches": 2,
        "flags_created": 3,
        "llm_calls": 1 + 3 + 3,  # segmentation + (rules, flags + decisions, finish) per batch
        "total_tokens": 0,
    }


def test_segmentation_uses_structured_output_with_playbook_types(
    make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    fake = FakeLLMProvider(segment_by_headings, default=ScriptedReviewer())

    make_runner(fake)(contract.id)

    schema = fake.calls[0].response_schema
    assert isinstance(schema, dict)
    enum = schema["properties"]["clauses"]["items"]["properties"]["clause_type"]["enum"]
    assert enum[-1] == "other"
    assert "limitation_of_liability" in enum
    assert fake.calls[0].tools is None
    assert all(call.tools for call in fake.calls[1:])


def test_clauses_are_batched(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    fake = FakeLLMProvider(segment_by_headings, default=ScriptedReviewer())

    make_runner(fake, agent_batch_size=3)(contract.id)

    batches = [
        e.payload for e in _events(db_session, contract) if e.event_type == "agent.batch_started"
    ]
    assert [b["clause_count"] for b in batches] == [3, 3, 3, 1]
    # Every conversation offers only its own batch's clauses.
    first_batch = parse_batch(fake.calls[1].messages[1].content)
    assert [c["clause_id"] for c in first_batch] == batches[0]["clause_ids"]


def test_flags_outside_the_current_batch_are_rejected(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract(LOW_RISK)
    holder: dict[str, str] = {}
    reviewer = ScriptedReviewer()

    def flag_a_first_batch_clause_later(messages: list[Message], *args: object):
        batch = parse_batch(messages[1].content)
        first = holder.setdefault("first", batch[0]["clause_id"])
        if first not in {clause["clause_id"] for clause in batch} and len(messages) == 2:
            return reply(
                tool_calls=[
                    call(
                        "create_risk_flag",
                        clause_id=first,
                        rule_id="termination",
                        severity="high",
                        score=0.5,
                        rationale="x",
                    )
                ]
            )
        return reviewer(messages, *args)

    fake = FakeLLMProvider(segment_by_headings, default=flag_a_first_batch_clause_later)

    make_runner(fake, agent_batch_size=5)(contract.id)

    assert _refresh(db_session, contract).status == ContractStatus.AWAITING_REVIEW
    tool_events = [e for e in _events(db_session, contract) if e.event_type == "agent.tool_call"]
    failures = [
        (e.payload["batch"], e.payload["error_code"]) for e in tool_events if not e.payload["ok"]
    ]
    assert failures == [(2, "clause_not_in_batch")]
    rejected = next(
        m
        for c in fake.calls
        for m in c.messages
        if m.role == "tool" and m.name == "create_risk_flag"
    )
    assert json.loads(rejected.content)["ok"] is False


def test_iteration_cap_stops_the_agent_and_fails_the_contract(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    # A model that never stops asking for rules.
    looping = reply(tool_calls=[call("get_playbook_rules", clause_type="termination")])
    fake = FakeLLMProvider(segment_by_headings, default=looping)

    make_runner(fake, agent_max_iterations=4)(contract.id)

    contract = _refresh(db_session, contract)
    assert contract.status == ContractStatus.FAILED
    assert len(fake.calls) == 4
    events = _events(db_session, contract)
    stopped = next(e for e in events if e.event_type == "agent.stopped")
    assert stopped.payload == {
        "reason": "max_iterations",
        "stage": "reviewing",
        "llm_calls": 4,
        "total_tokens": 500 + 3 * 100,
    }
    assert events[-1].payload == {
        "from": "analyzing",
        "to": "failed",
        "reason": "reviewing:AgentBudgetExceededError",
    }


def test_token_budget_stops_the_agent(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    fake = FakeLLMProvider(
        partial(segment_by_headings, tokens=5_000), default=ScriptedReviewer(tokens=5_000)
    )

    make_runner(fake, agent_token_budget=12_000)(contract.id)

    assert _refresh(db_session, contract).status == ContractStatus.FAILED
    stopped = next(e for e in _events(db_session, contract) if e.event_type == "agent.stopped")
    assert stopped.payload["reason"] == "token_budget"
    assert stopped.payload["total_tokens"] == 15_000
    assert len(fake.calls) == 3


def test_invalid_segmentation_fails_the_contract(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    fake = FakeLLMProvider(reply("not json"))

    make_runner(fake)(contract.id)

    assert _refresh(db_session, contract).status == ContractStatus.FAILED
    assert _status_changes(db_session, contract)[-1] == ("analyzing", "failed")
    failed = _events(db_session, contract)[-1]
    assert failed.payload["reason"] == "segmenting:SegmentationError"


def test_llm_errors_fail_the_contract_without_leaking_messages(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    fake = FakeLLMProvider(segment_by_headings, LLMServerError("gemini returned HTTP 503"))

    make_runner(fake)(contract.id)

    assert _refresh(db_session, contract).status == ContractStatus.FAILED
    events = _events(db_session, contract)
    assert events[-1].payload["reason"] == "reviewing:LLMServerError"
    assert "llm.call.failed" in [e.event_type for e in events]


def test_contract_over_the_length_limit_fails_before_any_llm_call(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    fake = FakeLLMProvider()

    make_runner(fake, agent_max_contract_chars=1_000)(contract.id)

    assert _refresh(db_session, contract).status == ContractStatus.FAILED
    assert fake.calls == []
    assert _status_changes(db_session, contract) == [("extracting", "failed")]
    assert _events(db_session, contract)[-1].payload["reason"] == (
        "extracting:ContractTooLongError"
    )


def test_unreadable_file_fails_extraction(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract(content=b"PK\x03\x04 not really a docx")

    make_runner(FakeLLMProvider())(contract.id)

    assert _refresh(db_session, contract).status == ContractStatus.FAILED
    assert _events(db_session, contract)[-1].payload["reason"] == ("extracting:TextExtractionError")


def test_contract_not_waiting_for_analysis_is_left_alone(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract(status=ContractStatus.UPLOADED)
    fake = FakeLLMProvider()

    make_runner(fake)(contract.id)

    assert _refresh(db_session, contract).status == ContractStatus.UPLOADED
    assert fake.calls == []
    assert _events(db_session, contract) == []


def test_rerun_after_failure_replaces_clauses(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    make_runner(FakeLLMProvider(segment_by_headings, LLMServerError("boom")))(contract.id)
    assert _refresh(db_session, contract).status == ContractStatus.FAILED

    contract = _refresh(db_session, contract)
    contract.status = ContractStatus.EXTRACTING  # what AnalysisService.start does
    db_session.commit()
    make_runner(FakeLLMProvider(segment_by_headings, default=ScriptedReviewer()))(contract.id)

    assert _refresh(db_session, contract).status == ContractStatus.AWAITING_REVIEW
    ordinals = db_session.scalars(
        select(Clause.ordinal).where(Clause.contract_id == contract.id)
    ).all()
    assert sorted(ordinals) == list(range(1, 13))


def test_audit_payloads_never_contain_contract_text(
    db_session: Session, make_contract: MakeContract, make_runner: MakeRunner
) -> None:
    contract = make_contract()
    fake = FakeLLMProvider(segment_by_headings, default=ScriptedReviewer(HIGH_RISK_FLAGS))

    make_runner(fake)(contract.id)

    dumped = json.dumps([e.payload for e in _events(db_session, contract)])
    assert "one hundred dollars" not in dumped
    assert "Meets the rule" not in dumped  # rationale text stays out of the audit log
