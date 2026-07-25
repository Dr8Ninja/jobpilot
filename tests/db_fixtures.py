"""Postgres-backed test fixtures.

Database tests run against a throwaway `jobpilot_test` database. If Postgres
isn't reachable they skip rather than fail, so the pure-Python suite (including
the whitelist gate) still runs anywhere.
"""

import os

import pytest
from jobpilot_shared.db.models import Base
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.environ.get(
    "JOBPILOT_TEST_DATABASE_URL",
    "postgresql+psycopg://localhost:5432/jobpilot_test",
)


def _postgres_available() -> bool:
    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    try:
        engine = create_engine(admin_url, connect_args={"connect_timeout": 2})
        with engine.connect():
            return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(), reason="Postgres is not reachable"
)


@pytest.fixture(scope="session")
def db_engine():
    if not _postgres_available():
        pytest.skip("Postgres is not reachable")

    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    db_name = TEST_DATABASE_URL.rsplit("/", 1)[1]
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin.dispose()

    engine = create_engine(TEST_DATABASE_URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine):
    """Each test runs inside a transaction that is rolled back afterwards."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        # A test that asserted on IntegrityError has already aborted the
        # transaction; rolling back again warns rather than helping.
        if transaction.is_active:
            transaction.rollback()
        connection.close()
