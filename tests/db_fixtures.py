"""Postgres-backed test fixtures.

Database tests run against a throwaway `jobpilot_test` database. If Postgres
isn't reachable they skip rather than fail, so the pure-Python suite (including
the whitelist gate) still runs anywhere.

Skipping is right on a laptop and dangerous in CI, where a mistyped connection
string would turn most of the suite into skips and report green. Setting
`JOBPILOT_REQUIRE_POSTGRES` turns "not reachable" from a skip into a failure.
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

#: Set in CI. A run where every database test silently skipped is not a pass.
REQUIRE_POSTGRES = bool(os.environ.get("JOBPILOT_REQUIRE_POSTGRES"))


def _postgres_available() -> bool:
    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    try:
        engine = create_engine(admin_url, connect_args={"connect_timeout": 2})
        with engine.connect():
            return True
    except Exception:
        if REQUIRE_POSTGRES:
            raise
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
    """Each test runs inside a transaction that is rolled back afterwards.

    `create_savepoint` is what makes commit and rollback behave the way they do
    in production. The default (`conditional_savepoint`) degrades to
    "rollback_only" against a plain outer transaction, where `commit()` does not
    durably land and `rollback()` discards the *whole* test transaction. Code
    that commits a checkpoint and then rolls back a later failure — which the
    pipeline and the run bookkeeping both do deliberately — cannot be tested
    honestly under those semantics.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()
        # A test that asserted on IntegrityError has already aborted the
        # transaction; rolling back again warns rather than helping.
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def global_session(db_engine):
    """Point the process-wide session factory at the test database.

    For code that opens its own session and so cannot be handed one: the Celery
    task, which receives only a run id, and the CLI, which talks to the database
    through `session_scope`.

    Everything under this fixture genuinely commits, which puts it outside the
    per-test transaction the `db` fixture provides. It therefore truncates on
    the way out — `db_engine` is session-scoped, so rows left behind would
    collide with unrelated tests later in the run.
    """
    from jobpilot_shared.db import session as session_module
    from jobpilot_shared.db.session import get_engine, get_session_factory

    previous_engine = session_module._engine
    previous_factory = session_module._session_factory
    get_engine(TEST_DATABASE_URL)
    try:
        yield get_session_factory()
    finally:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        with get_engine().begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        session_module._engine = previous_engine
        session_module._session_factory = previous_factory
