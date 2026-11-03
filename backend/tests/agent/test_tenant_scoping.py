"""Similar-clause search never crosses organizations."""

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from app.agent.tools import AgentTools
from app.core.config import Settings
from app.models import DEFAULT_ORGANIZATION_ID, Clause, Contract, Organization
from app.playbook import load_playbook
from app.repositories.clause_repository import ClauseRepository
from tests.agent.conftest import MakeContract
from tests.agent.fake_llm import call
from tests.contract_fixtures import LOW_RISK, MEDIUM_RISK

UNLIMITED = "The Supplier's total liability under this agreement is unlimited."
ACME_ONLY = "Indemnification obligations survive termination indefinitely for Zanzibar claims."


@pytest.fixture
def other_org(db_session: Session) -> Organization:
    org = Organization(id=uuid.uuid4(), name="Other tenant")
    db_session.add(org)
    db_session.commit()
    return org


@pytest.fixture
def contracts(
    db_session: Session,
    make_contract: MakeContract,
    add_clauses: Callable[..., list[Clause]],
    other_org: Organization,
) -> tuple[Contract, Contract, Contract]:
    """The contract under review, a same-org contract, and another org's contract."""
    current = make_contract()
    same_org = make_contract(LOW_RISK)
    foreign = make_contract(MEDIUM_RISK)
    foreign.organization_id = other_org.id
    db_session.commit()
    add_clauses(same_org, ("limitation_of_liability", UNLIMITED))
    add_clauses(
        foreign,
        ("limitation_of_liability", UNLIMITED),
        ("indemnification", ACME_ONLY),
    )
    return current, same_org, foreign


def test_new_contracts_belong_to_the_default_organization(make_contract: MakeContract) -> None:
    assert make_contract().organization_id == DEFAULT_ORGANIZATION_ID


def test_repository_search_is_scoped_to_one_organization(
    db_session: Session, contracts: tuple[Contract, Contract, Contract], other_org: Organization
) -> None:
    current, same_org, foreign = contracts
    repository = ClauseRepository(db_session)

    ours = repository.search_similar(
        "unlimited liability",
        organization_id=DEFAULT_ORGANIZATION_ID,
        exclude_contract_id=current.id,
        limit=5,
    )
    theirs = repository.search_similar(
        "unlimited liability",
        organization_id=other_org.id,
        exclude_contract_id=current.id,
        limit=5,
    )

    assert [m.clause.contract_id for m in ours] == [same_org.id]
    assert [m.clause.contract_id for m in theirs] == [foreign.id]


def test_tool_never_returns_another_organizations_clauses(
    db_session: Session, contracts: tuple[Contract, Contract, Contract], agent_settings: Settings
) -> None:
    current, _, _ = contracts
    tools = AgentTools(db_session, current.id, load_playbook(agent_settings.playbook_path))

    shared = tools.execute(call("search_similar_clauses", text=UNLIMITED, limit=5), batch=1)
    foreign_only = tools.execute(call("search_similar_clauses", text=ACME_ONLY, limit=5), batch=1)

    assert [m["text"] for m in shared["matches"]] == [UNLIMITED]
    assert foreign_only == {"ok": True, "matches": []}


def test_search_requires_an_organization(db_session: Session) -> None:
    with pytest.raises(TypeError):
        ClauseRepository(db_session).search_similar(  # type: ignore[call-arg]
            "unlimited liability", exclude_contract_id=uuid.uuid4(), limit=5
        )
