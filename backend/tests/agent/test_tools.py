from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools import AgentTools
from app.core.config import Settings
from app.models import AuditEvent, Clause, Contract, FlagStatus, RiskFlag, Severity
from app.playbook import load_playbook
from tests.agent.conftest import MakeContract
from tests.agent.fake_llm import call
from tests.contract_fixtures import LOW_RISK

LIABILITY_TEXT = (
    "Vendor's total liability will not exceed one hundred dollars. Customer's liability "
    "under this Agreement is unlimited."
)


Setup = tuple[AgentTools, Contract, list[Clause]]


@pytest.fixture
def setup(
    db_session: Session,
    make_contract: MakeContract,
    add_clauses: Callable[..., list[Clause]],
    agent_settings: Settings,
) -> Setup:
    contract = make_contract()
    clauses = add_clauses(
        contract,
        ("limitation_of_liability", LIABILITY_TEXT),
        ("termination", "Either party may terminate for material breach after 30 days."),
    )
    tools = AgentTools(db_session, contract.id, load_playbook(agent_settings.playbook_path))
    tools.active_clause_ids = {clause.id for clause in clauses}
    return tools, contract, clauses


def _tool_events(session: Session, contract: Contract) -> list[AuditEvent]:
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.contract_id == contract.id, AuditEvent.event_type == "agent.tool_call")
        .order_by(AuditEvent.id)
    )
    return list(session.scalars(stmt))


def test_get_playbook_rules(setup: Setup, db_session: Session) -> None:
    tools, contract, _ = setup

    result = tools.execute(call("get_playbook_rules", clause_type="data_protection"), batch=1)

    assert result["ok"] is True
    assert result["playbook_version"] == 1
    assert [rule["id"] for rule in result["rules"]] == ["data-protection"]
    assert result["rules"][0]["severity"] == "critical"
    assert result["rules"][0]["risk_criteria"]
    event = _tool_events(db_session, contract)[-1]
    assert event.payload == {
        "tool": "get_playbook_rules",
        "tool_call_id": "call_get_playbook_rules",
        "batch": 1,
        "clause_type": "data_protection",
        "result_count": 1,
        "ok": True,
    }


def test_get_playbook_rules_for_unknown_type_returns_no_rules(setup: Setup) -> None:
    tools, _, _ = setup
    result = tools.execute(call("get_playbook_rules", clause_type="recitals"), batch=1)
    assert result == {"ok": True, "playbook_version": 1, "clause_type": "recitals", "rules": []}


def test_create_risk_flag_is_always_pending_and_audited(setup: Setup, db_session: Session) -> None:
    tools, contract, clauses = setup

    result = tools.execute(
        call(
            "create_risk_flag",
            clause_id=str(clauses[0].id),
            rule_id="liability-cap",
            severity="critical",
            score=0.95,
            rationale="Customer liability is unlimited while Vendor's is capped at $100.",
            status="approved",  # ignored: the agent cannot set a flag's status
        ),
        batch=1,
    )

    assert result["ok"] is True
    assert result["status"] == "pending"
    flag = db_session.get(RiskFlag, result["risk_flag_id"])
    assert flag is not None
    assert flag.status == FlagStatus.PENDING
    assert flag.severity == Severity.CRITICAL
    assert flag.score == 0.95
    assert tools.flags_created == 1

    created = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.contract_id == contract.id, AuditEvent.event_type == "agent.flag_created"
        )
    ).one()
    assert created.payload["status"] == "pending"
    assert created.payload["rule_id"] == "liability-cap"
    assert "rationale" not in created.payload
    assert _tool_events(db_session, contract)[-1].payload["ok"] is True


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"clause_id": "not-a-uuid"}, "invalid_clause_id"),
        ({"clause_id": "00000000-0000-0000-0000-000000000000"}, "clause_not_in_batch"),
        ({"rule_id": "made-up-rule"}, "unknown_rule"),
        ({"severity": "extreme"}, "invalid_severity"),
        ({"score": 1.5}, "invalid_score"),
        ({"score": "0.5"}, "invalid_score"),
        ({"score": True}, "invalid_score"),
        ({"rationale": "  "}, "missing_rationale"),
    ],
)
def test_create_risk_flag_validates_arguments(
    setup, db_session: Session, overrides: dict[str, object], error_code: str
) -> None:
    tools, contract, clauses = setup
    arguments: dict[str, object] = {
        "clause_id": str(clauses[0].id),
        "rule_id": "liability-cap",
        "severity": "high",
        "score": 0.5,
        "rationale": "Uncapped liability.",
    } | overrides

    result = tools.execute(call("create_risk_flag", **arguments), batch=1)

    assert result["ok"] is False
    assert result["error"]
    assert tools.flags_created == 0
    assert db_session.scalars(select(RiskFlag)).all() == []
    event = _tool_events(db_session, contract)[-1]
    assert event.payload["ok"] is False
    assert event.payload["error_code"] == error_code


def test_duplicate_flags_are_rejected(setup: Setup) -> None:
    tools, _, clauses = setup
    arguments = {
        "clause_id": str(clauses[0].id),
        "rule_id": "liability-cap",
        "severity": "high",
        "score": 0.8,
        "rationale": "Uncapped liability.",
    }

    first = tools.execute(call("create_risk_flag", **arguments), batch=1)
    second = tools.execute(call("create_risk_flag", **arguments), batch=1)

    assert first["ok"] is True
    assert second == {"ok": False, "error": "This clause already has a flag for this rule"}


def test_unknown_tool_is_reported_to_the_model(setup: Setup, db_session: Session) -> None:
    tools, contract, _ = setup

    result = tools.execute(call("approve_contract"), batch=1)

    assert result["ok"] is False
    assert _tool_events(db_session, contract)[-1].payload["error_code"] == "unknown_tool"


def test_search_similar_clauses_finds_other_contracts_only(
    setup: Setup,
    db_session: Session,
    make_contract: MakeContract,
    add_clauses: Callable[..., list[Clause]],
) -> None:
    tools, contract, _ = setup
    other = make_contract(LOW_RISK)
    precedent, _ = add_clauses(
        other,
        ("limitation_of_liability", "Supplier's total liability is unlimited for all claims."),
        ("governing_law", "This agreement is governed by the laws of Delaware."),
    )
    db_session.add(
        RiskFlag(
            clause_id=precedent.id,
            rule_id="liability-cap",
            severity=Severity.HIGH,
            score=0.9,
            rationale="Unlimited liability.",
        )
    )
    db_session.commit()

    result = tools.execute(
        call("search_similar_clauses", text="unlimited total liability", limit=10), batch=1
    )

    assert result["ok"] is True
    matches = result["matches"]
    assert [m["clause_type"] for m in matches] == ["limitation_of_liability"]
    assert matches[0]["text"] == precedent.text
    assert matches[0]["flags"] == [
        {"rule_id": "liability-cap", "severity": "high", "review_status": "pending"}
    ]
    event = _tool_events(db_session, contract)[-1].payload
    assert event["query_chars"] == len("unlimited total liability")
    assert event["result_count"] == 1
    assert "text" not in event


def test_search_similar_clauses_requires_text(setup: Setup) -> None:
    tools, _, _ = setup
    result = tools.execute(call("search_similar_clauses", text=""), batch=1)
    assert result["ok"] is False


def test_search_with_only_short_words_returns_nothing(setup: Setup) -> None:
    tools, _, _ = setup
    result = tools.execute(call("search_similar_clauses", text="a an the of"), batch=1)
    assert result == {"ok": True, "matches": []}
