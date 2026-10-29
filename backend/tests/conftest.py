import io
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR, Settings, get_settings
from app.core.db import get_db
from app.main import create_app

TEST_MAX_UPLOAD_MB = 1


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Fresh schema in the test database, built by running the real Alembic migrations."""
    settings = get_settings()
    url = settings.test_database_url
    if url == settings.database_url:
        pytest.exit("TEST_DATABASE_URL must differ from DATABASE_URL", returncode=1)

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    alembic_cfg.attributes["configure_logger"] = False
    command.upgrade(alembic_cfg, "head")

    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(engine: Engine) -> Iterator[Connection]:
    """A connection inside an outer transaction that is rolled back after each test."""
    connection = engine.connect()
    outer = connection.begin()
    try:
        yield connection
    finally:
        outer.rollback()
        connection.close()


def _savepoint_session(connection: Connection) -> Session:
    # App code may call commit(); with create_savepoint those commits only release a
    # SAVEPOINT inside the outer test transaction, so nothing persists between tests.
    return Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    session = _savepoint_session(db_connection)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def session_factory(db_connection: Connection) -> Callable[[], Session]:
    """Factory for extra sessions (e.g. LLM audit writes) that share the test transaction."""
    return lambda: _savepoint_session(db_connection)


@pytest.fixture
def upload_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture
def client(db_session: Session, upload_dir: Path) -> Iterator[TestClient]:
    app = create_app()
    test_settings = Settings(upload_dir=upload_dir, max_upload_mb=TEST_MAX_UPLOAD_MB)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def pdf_bytes() -> bytes:
    return b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


@pytest.fixture
def docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()
