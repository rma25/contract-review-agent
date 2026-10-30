"""Opt-in end-to-end analysis against the real provider in .env.

Run with: uv run pytest -m live -k agent

Uses roughly 5 to 10 LLM calls, well within free-tier daily limits.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent import AnalysisRunner
from app.api.deps import get_analysis_runner
from app.core.config import Settings
from app.llm import get_llm_provider
from app.services.storage import DOCX_CONTENT_TYPE, LocalFileStorage
from tests.contract_fixtures import HIGH_RISK, expected_labels, fixture_docx
from tests.live import skip_if_unconfigured

pytestmark = pytest.mark.live

SEVERE = {"high", "critical"}


def test_live_agent_flags_the_high_risk_fixture(
    client: TestClient, session_factory: Callable[[], Session], upload_dir: Path
) -> None:
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
        "/contracts", files={"file": ("vendor.docx", fixture_docx(HIGH_RISK), DOCX_CONTENT_TYPE)}
    )
    contract_id = upload.json()["id"]
    assert client.post(f"/contracts/{contract_id}/analyze").status_code == 202

    contract = client.get(f"/contracts/{contract_id}").json()
    assert contract["status"] == "awaiting_review", contract["status"]
    assert len(contract["clauses"]) >= 5

    flags = client.get(f"/contracts/{contract_id}/flags").json()
    assert flags, "the high-risk fixture produced no flags"
    assert {flag["status"] for flag in flags} == {"pending"}

    expected = expected_labels()["fixtures"][HIGH_RISK]["must_flag"]
    severe_expected = {rule for rule, severity in expected.items() if severity in SEVERE}
    severe_found = {flag["rule_id"] for flag in flags if flag["severity"] in SEVERE}
    assert severe_found & severe_expected, f"no high/critical flag on {sorted(severe_expected)}"
