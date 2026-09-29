"""Who is making the current request, made available to the database layer.

get_current_user stores the caller here; db.get_cursor reads it to apply Postgres row-level security for
that caller. Background jobs, login and start-up have no request context and run as the trusted service.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# Roles that are not confined to one jurisdiction. This single tuple drives the API checks AND the database policies.
CROSS_JURISDICTION_ROLES = ("admin", "supervisor", "analyst")

_current: ContextVar[Optional[dict]] = ContextVar("argus_request_user", default=None)


def set_request_user(user: dict) -> None:
    _current.set({"role": user["role"], "jurisdiction": user.get("jurisdiction") or ""})


def get_request_user() -> Optional[dict]:
    return _current.get()


def clear_request_user() -> None:
    _current.set(None)
