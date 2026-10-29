import hashlib
import io
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActorType, AuditEvent, Clause, Contract
from app.services.storage import DOCX_CONTENT_TYPE, PDF_CONTENT_TYPE
from tests.conftest import TEST_MAX_UPLOAD_MB


def _upload(client: TestClient, filename: str, content: bytes, content_type: str):
    return client.post("/contracts", files={"file": (filename, content, content_type)})


@pytest.mark.parametrize(
    ("filename", "fixture", "expected_type"),
    [
        ("msa.pdf", "pdf_bytes", PDF_CONTENT_TYPE),
        ("nda.docx", "docx_bytes", DOCX_CONTENT_TYPE),
    ],
)
def test_upload_contract(
    client: TestClient,
    db_session: Session,
    upload_dir: Path,
    request: pytest.FixtureRequest,
    filename: str,
    fixture: str,
    expected_type: str,
) -> None:
    content: bytes = request.getfixturevalue(fixture)

    response = _upload(client, filename, content, "application/octet-stream")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["original_filename"] == filename
    assert body["content_type"] == expected_type
    assert body["size_bytes"] == len(content)
    assert body["sha256"] == hashlib.sha256(content).hexdigest()
    assert body["status"] == "uploaded"
    assert body["clauses"] == []

    contract = db_session.get(Contract, uuid.UUID(body["id"]))
    assert contract is not None
    stored = upload_dir / contract.storage_key
    assert stored.read_bytes() == content
    assert contract.storage_key != filename

    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.contract_id == contract.id)
    ).all()
    assert [e.event_type for e in events] == ["contract.uploaded"]
    assert events[0].actor_type == ActorType.HUMAN
    assert events[0].payload["sha256"] == body["sha256"]


def test_upload_strips_directories_from_filename(
    client: TestClient, upload_dir: Path, pdf_bytes: bytes
) -> None:
    response = _upload(client, "../../evil.pdf", pdf_bytes, PDF_CONTENT_TYPE)

    assert response.status_code == 201
    assert response.json()["original_filename"] == "evil.pdf"
    assert [p.parent for p in upload_dir.iterdir()] == [upload_dir]


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("contract.txt", b"plain text"),
        ("contract.pdf", b"PK\x03\x04 not really a pdf"),
        ("contract.docx", b"%PDF-1.7 not a docx"),
        ("contract.docx", b"PK\x03\x04 truncated zip"),
    ],
)
def test_upload_rejects_unsupported_files(
    client: TestClient, db_session: Session, upload_dir: Path, filename: str, content: bytes
) -> None:
    response = _upload(client, filename, content, "application/octet-stream")

    assert response.status_code == 415
    assert db_session.scalars(select(Contract)).all() == []
    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


def test_upload_rejects_docx_without_word_document(client: TestClient) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "just a zip")

    response = _upload(client, "contract.docx", buffer.getvalue(), DOCX_CONTENT_TYPE)

    assert response.status_code == 415


def test_upload_rejects_empty_file(client: TestClient) -> None:
    response = _upload(client, "empty.pdf", b"", PDF_CONTENT_TYPE)

    assert response.status_code == 400


def test_upload_rejects_oversized_file(client: TestClient, upload_dir: Path) -> None:
    content = b"%PDF-" + b"0" * (TEST_MAX_UPLOAD_MB * 1024 * 1024)

    response = _upload(client, "huge.pdf", content, PDF_CONTENT_TYPE)

    assert response.status_code == 413
    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


def test_upload_requires_file_field(client: TestClient) -> None:
    response = client.post("/contracts")

    assert response.status_code == 422


def test_get_contract(client: TestClient, db_session: Session, pdf_bytes: bytes) -> None:
    created = _upload(client, "msa.pdf", pdf_bytes, PDF_CONTENT_TYPE).json()
    contract_id = uuid.UUID(created["id"])
    db_session.add_all(
        [
            Clause(contract_id=contract_id, ordinal=2, clause_type="termination", text="B"),
            Clause(contract_id=contract_id, ordinal=1, clause_type="indemnity", text="A"),
        ]
    )
    db_session.commit()
    db_session.expire_all()

    response = client.get(f"/contracts/{contract_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["sha256"] == created["sha256"]
    assert [c["ordinal"] for c in body["clauses"]] == [1, 2]
    assert body["clauses"][0]["clause_type"] == "indemnity"


def test_get_contract_not_found(client: TestClient) -> None:
    response = client.get(f"/contracts/{uuid.uuid4()}")

    assert response.status_code == 404


def test_get_contract_invalid_id(client: TestClient) -> None:
    response = client.get("/contracts/not-a-uuid")

    assert response.status_code == 422
