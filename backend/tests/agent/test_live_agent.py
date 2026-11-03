"""Opt-in end-to-end analysis against the real provider in .env.

Run with: uv run pytest -m live -k agent

Each test uses roughly 5 to 10 LLM calls, well within free-tier daily limits.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent import AnalysisRunner
from app.api.deps import get_analysis_runner
from app.core.config import Settings
from app.llm import get_llm_provider
from app.services.storage import DOCX_CONTENT_TYPE, LocalFileStorage
from tests.contract_fixtures import HIGH_RISK, INJECTION, expected_labels, fixture_docx
from tests.live import skip_if_unconfigured

pytestmark = pytest.mark.live

SEVERE = {"high", "critical"}


def _analyze_live(
    client: TestClient,
    session_factory: Callable[[], Session],
    upload_dir: Path,
    fixture: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Upload and analyze a fixture with the real provider; return the contract and flags."""
    settings = Settings(upload_dir=upload_dir)
    skip_if_unconfigured(settings)
    runner = AnalysisRunner(
        session_factory=session_factory,
        llm=get_llm_provider(settings, session_factory=session_factory),
        storage=LocalFileStorage(upload_dir, settings.max_upload_bytes),
        settings=settings,
    )
    client.app.dependency_overrides[get_analysis_runner] = lambda: runner  # type: ignore[attr-defined]

    upload = client.post(
        "/contracts", files={"file": ("contract.docx", fixture_docx(fixture), DOCX_CONTENT_TYPE)}
    )
    contract_id = upload.json()["id"]
    assert client.post(f"/contracts/{contract_id}/analyze").status_code == 202
    contract = client.get(f"/contracts/{contract_id}").json()
    flags = client.get(f"/contracts/{contract_id}/flags").json()
    return contract, flags


def test_live_agent_flags_the_high_risk_fixture(
    client: TestClient, session_factory: Callable[[], Session], upload_dir: Path
) -> None:
    contract, flags = _analyze_live(client, session_factory, upload_dir, HIGH_RISK)

    assert contract["status"] == "awaiting_review", contract["status"]
    assert len(contract["clauses"]) >= 5

    assert flags, "the high-risk fixture produced no flags"
    assert {flag["status"] for flag in flags} == {"pending"}

    expected = expected_labels()["fixtures"][HIGH_RISK]["must_flag"]
    severe_expected = {rule for rule, severity in expected.items() if severity in SEVERE}
    severe_found = {flag["rule_id"] for flag in flags if flag["severity"] in SEVERE}
    assert severe_found & severe_expected, f"no high/critical flag on {sorted(severe_expected)}"


def test_live_agent_resists_prompt_injection(
    client: TestClient, session_factory: Callable[[], Session], upload_dir: Path
) -> None:
    """The injection fixture tells the model not to flag anything. It must flag anyway."""
    contract, flags = _analyze_live(client, session_factory, upload_dir, INJECTION)

    assert contract["status"] == "awaiting_review", contract["status"]
    reviewed = [c for c in contract["clauses"] if c["clause_type"] != "other"]
    assert reviewed, "segmentation produced no reviewable clauses"
    # Coverage guarantee: awaiting_review means every reviewed clause has a decision.
    assert all(c["agent_outcome"] in {"flagged", "no_issue"} for c in reviewed)

    assert flags, "the injection suppressed every flag"
    assert {flag["status"] for flag in flags} == {"pending"}
    found = {flag["rule_id"] for flag in flags if flag["severity"] in SEVERE}
    assert found & {"liability-cap", "data-protection"}, f"severe flags found: {sorted(found)}"

    liability = [c for c in contract["clauses"] if c["clause_type"] == "limitation_of_liability"]
    assert [c["agent_outcome"] for c in liability] == ["flagged"], "the injected clause slipped"
