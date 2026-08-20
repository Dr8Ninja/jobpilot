"""Shared FastAPI dependencies.

Split out of `main.py` so `auth.py` can depend on the session without the two
modules importing each other. `main` re-exports `get_db`, so existing
`dependency_overrides[get_db]` keys keep pointing at the same function object.
"""

from jobpilot_shared.db.session import get_session_factory
from sqlalchemy.orm import Session


def get_db() -> Session:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
