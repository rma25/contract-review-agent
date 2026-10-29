from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.db import get_db


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


class _BrokenSession:
    def execute(self, *_: object, **__: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


def test_health_reports_database_unavailable(client: TestClient) -> None:
    client.app.dependency_overrides[get_db] = lambda: _BrokenSession()  # type: ignore[attr-defined]

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
