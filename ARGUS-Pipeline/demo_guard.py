"""Refuse to run in production while any account still uses a published demo password.

The demo accounts (README, db.DEMO_USERS) have passwords that are public. SEED_DEMO_USERS=false stops them being created,
but an account created earlier, or a database restored from a dev environment, keeps its password. So at startup, in
production, every stored login is re-derived against each published demo password; any match refuses to start.

Passwords are PBKDF2 with a per-user random salt, so hashes cannot be compared across users: each account has to be
re-derived with each demo password (about 2 derivations per account, run on a thread pool: hashlib releases the GIL).
The check fails closed: if it cannot run in production, the API does not start.

    python demo_guard.py        # exit 1 if any account still has a demo password (for pre-deploy checks)
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

import config
from auth_security import verify_password


def demo_passwords() -> set[str]:
    """Every password published for a demo account: the single source of truth is db.DEMO_USERS."""
    from db import DEMO_USERS

    return {password for _, password, *_ in DEMO_USERS}


def demo_credentials_in_use(rows: Iterable[dict], passwords: Optional[set[str]] = None, workers: int = 8) -> list[str]:
    """employee_ids whose stored password equals a demo password. rows: dicts with employee_id, password_hash, salt."""
    passwords = passwords if passwords is not None else demo_passwords()
    candidates = [r for r in rows if r.get("password_hash") and r.get("salt")]   # OIDC-provisioned users have no local password

    def uses_demo_password(row: dict) -> bool:
        return any(verify_password(p, row["password_hash"], row["salt"]) for p in passwords)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        flags = list(pool.map(uses_demo_password, candidates))
    return sorted(r["employee_id"] for r, hit in zip(candidates, flags) if hit)


def find_in_database() -> list[str]:
    from db import get_cursor

    with get_cursor() as cur:
        cur.execute("SELECT employee_id, password_hash, salt FROM users")
        return demo_credentials_in_use(cur.fetchall())


def assert_no_demo_credentials() -> None:
    """Startup guard. No-op in development and when SSO has switched local sign-in off; raises in production on any hit."""
    if config._DEV_MODE or config.KEYCLOAK_URL:
        return
    try:
        found = find_in_database()
    except Exception as exc:  # fail closed: an unverifiable production database must not serve requests
        raise RuntimeError(f"[ARGUS] Refusing to start: could not check accounts for published demo passwords ({exc})") from exc
    if found:
        raise RuntimeError(
            "[ARGUS] Refusing to start in production: these accounts still use a published demo password: "
            + ", ".join(found) + ". Change or delete them (the README lists these passwords publicly)."
        )


if __name__ == "__main__":
    hits = find_in_database()
    print("accounts using a published demo password:", ", ".join(hits) if hits else "none")
    sys.exit(1 if hits else 0)
