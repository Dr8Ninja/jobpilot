"""Who this installation belongs to.

The `users` and `profiles` tables have existed since the first migration and
until now nothing read them: every query was global and the profile was fetched
as `select(Profile)` — whichever row Postgres happened to return first. That is
harmless with one row and wrong the moment there are two, and the row in
question is the whitelist every tailored resume is checked against.

One place resolves the owner, so the API, the worker and the CLI all agree.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import User
from .settings import get_settings


def resolve_owner(session: Session) -> User | None:
    """The user this installation belongs to, or None before `confirm-facts`.

    Matched on `owner_email` first, then falling back to the lowest user id —
    an installation that predates the setting still resolves to its one user.
    """
    settings = get_settings()
    user = session.scalar(select(User).where(User.email == settings.owner_email))
    if user is not None:
        return user
    return session.scalar(select(User).order_by(User.id).limit(1))


def owner_id(session: Session) -> int | None:
    owner = resolve_owner(session)
    return owner.id if owner is not None else None
