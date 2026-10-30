import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import AnalysisRunner
from app.api.deps import get_analysis_runner
from app.core.config import Settings, get_settings
from app.llm import LLMServerError
from app.models import AuditEvent, ContractStatus
from app.services.storage import DOCX_CONTENT_TYPE
from tests.agent.conftest import MakeContract, MakeRunner
from tests.agent.fake_llm import FakeLLMProvider, ScriptedReviewer, segment_by_headings
from tests.contract_fixtures import HIGH_RISK, fixture_docx

FLAGS = {
    "limitation_of_liability": ("liability-cap", "critical", 0.95),
    "indemnification": ("indemnification", "high", 0.9),
}

UseFake = Callable[[FakeLLMProvider], None]


@pytest.fixture
def use_fake(client: TestClient, make_runner: MakeRunner) -> UseFake:
    """Route POST /analyze to a runner backed by the given fake provider."""

    def install(fake: FakeLLMProvider) -> None:
        runner: AnalysisRunner = make_runner(fake)
        client.app.dependency_overrides[get_analysis_runner] = lambda: runner  # type: ignore[attr-defined]

    return install


def _upload_fixture(client: TestClient) -> str:
    response = client.post(
        "/contracts",
        files={"file": ("vendor.docx", fixture_docx(HIGH_RISK), DOCX_CONTENT_TYPE)},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_analyze_then_list_flags(client: TestClient, use_fake: UseFake) -> None:
    use_fake(FakeLLMProvider(segment_by_headings, default=ScriptedReviewer(FLAGS)))
    contract_id = _upload_fixture(client)

    response = client.post(f"/contracts/{contract_id}/analyze")

    assert response.status_code == 202, response.text
    # The response reflects the state when the request returned; the TestClient then runs
    # the background task before handing control back.
    assert response.json()["status"] == "extracting"

    contract = client.get(f"/contracts/{contract_id}").json()
    assert contract["status"] == "awaiting_review"
    assert len(contract["clauses"]) == 12
    assert contract["clauses"][8]["heading"] == "9. Limitation of Liability"

    flags = client.get(f"/contracts/{contract_id}/flags")
    assert flags.status_code == 200
    body = flags.json()
    assert [(f["clause_ordinal"], f["rule_id"]) for f in body] == [
        (8, "indemnification"),
        (9, "liability-cap"),
    ]
    assert body[1] | {"id": None, "clause_id": None, "created_at": None} == {
        "id": None,
        "clause_id": None,
        "created_at": None,
        "clause_ordinal": 9,
        "clause_type": "limitation_of_liability",
        "clause_heading": "9. Limitation of Liability",
        "rule_id": "liability-cap",
        "severity": "critical",
        "score": 0.95,
        "rationale": "Meets the rule's risk criteria.",
        "status": "pending",
    }


def test_analyze_request_is_audited_as_a_human_action(
    client: TestClient, use_fake: UseFake, db_session: Session
) -> None:
    use_fake(FakeLLMProvider(segment_by_headings, default=ScriptedReviewer()))
    contract_id = _upload_fixture(client)

    client.post(f"/contracts/{contract_id}/analyze")

    changes = db_session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.contract_id == uuid.UUID(contract_id),
            AuditEvent.event_type == "contract.status_changed",
        )
        .order_by(AuditEvent.id)
    ).all()
    assert [(e.actor_type.value, e.payload["from"], e.payload["to"]) for e in changes] == [
        ("human", "uploaded", "extracting"),
        ("agent", "extracting", "analyzing"),
        ("agent", "analyzing", "awaiting_review"),
    ]
    assert changes[0].payload["reason"] == "analysis_requested"


@pytest.mark.parametrize(
    "status", [ContractStatus.EXTRACTING, ContractStatus.ANALYZING, ContractStatus.AWAITING_REVIEW]
)
def test_analyze_conflicts_unless_uploaded_or_failed(
    client: TestClient, use_fake: UseFake, make_contract: MakeContract, status: ContractStatus
) -> None:
    fake = FakeLLMProvider()
    use_fake(fake)
    contract = make_contract(status=status)

    response = client.post(f"/contracts/{contract.id}/analyze")

    assert response.status_code == 409
    assert status.value in response.json()["detail"]
    assert fake.calls == []


def test_failed_analysis_can_be_retried(client: TestClient, use_fake: UseFake) -> None:
    use_fake(FakeLLMProvider(LLMServerError("gemini returned HTTP 503")))
    contract_id = _upload_fixture(client)

    client.post(f"/contracts/{contract_id}/analyze")
    assert client.get(f"/contracts/{contract_id}").json()["status"] == "failed"

    use_fake(FakeLLMProvider(segment_by_headings, default=ScriptedReviewer(FLAGS)))
    retry = client.post(f"/contracts/{contract_id}/analyze")

    assert retry.status_code == 202
    assert client.get(f"/contracts/{contract_id}").json()["status"] == "awaiting_review"
    assert len(client.get(f"/contracts/{contract_id}/flags").json()) == 2


def test_unknown_contract_returns_404(client: TestClient, use_fake: UseFake) -> None:
    use_fake(FakeLLMProvider())
    missing = uuid.uuid4()
    assert client.post(f"/contracts/{missing}/analyze").status_code == 404
    assert client.get(f"/contracts/{missing}/flags").status_code == 404


def test_flags_are_empty_before_analysis(client: TestClient) -> None:
    contract_id = _upload_fixture(client)
    response = client.get(f"/contracts/{contract_id}/flags")
    assert response.status_code == 200
    assert response.json() == []


def test_missing_provider_key_returns_503_and_changes_nothing(
    client: TestClient, upload_dir
) -> None:
    unconfigured = Settings(
        _env_file=None, upload_dir=upload_dir, llm_provider="gemini", gemini_api_key=""
    )
    contract_id = _upload_fixture(client)
    client.app.dependency_overrides[get_settings] = lambda: unconfigured  # type: ignore[attr-defined]

    response = client.post(f"/contracts/{contract_id}/analyze")

    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["detail"]
    assert client.get(f"/contracts/{contract_id}").json()["status"] == "uploaded"
