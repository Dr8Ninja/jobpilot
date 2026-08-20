"""Single-user bearer-token auth.

Deliberately small. There is one user, one token, and no session state — a
password flow would be more machinery guarding the same one door.

The token is compared with `secrets.compare_digest`, not `==`. A plain
comparison returns as soon as two bytes differ, and the time it took says how
much of the token was right.
"""

import secrets

from fastapi import Depends, HTTPException, Request
from jobpilot_shared.db.models import User
from jobpilot_shared.ownership import resolve_owner
from jobpilot_shared.settings import get_settings
from sqlalchemy.orm import Session

from .deps import get_db

#: Reachable without a token so a load balancer or `curl` can check liveness
#: without being handed a credential.
OPEN_PATHS = frozenset({"/health"})


def require_token(request: Request) -> None:
    """Reject anything without the configured bearer token, when auth is on."""
    settings = get_settings()
    if not settings.auth_enabled:
        return
    if request.url.path.endswith(tuple(OPEN_PATHS)):
        return

    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, settings.api_token):
        raise HTTPException(
            401,
            "Missing or invalid bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def current_user(db: Session = Depends(get_db)) -> User | None:
    """The owner of this installation.

    None before `confirm-facts` has ever run — the queue is empty then anyway,
    so there is nothing to scope and nothing to hide.
    """
    return resolve_owner(db)
