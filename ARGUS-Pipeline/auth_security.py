"""Password hashing, JWT issuance/verification, and role-based access
control dependencies for the ARGUS platform track.

Uses stdlib PBKDF2-HMAC for password hashing (no extra native-build
dependency like bcrypt) and PyJWT for tokens.
"""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Header, HTTPException

import config
from request_context import set_request_user
from config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET, MFA_ENCRYPTION_KEY, MFA_ISSUER

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Returns (password_hash, salt) as hex strings."""
    salt = salt or os.urandom(16).hex()
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return derived.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return candidate == password_hash


def create_access_token(user: dict) -> str:
    """Embeds the full user profile in the token so most requests never
    need a DB round-trip; /auth/me still re-reads the DB for freshness."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["user_id"],
        "employee_id": user["employee_id"],
        "full_name": user["full_name"],
        "role": user["role"],
        "jurisdiction": user["jurisdiction"],
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authentication token.")


# ---------------------------------------------------------------------------
# Optional OIDC (Keycloak) verification
# ---------------------------------------------------------------------------

_ARGUS_ROLES_BY_PRIVILEGE = ("admin", "supervisor", "analyst", "investigator")
_OIDC_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384"]  # asymmetric only: a shared-secret (HS*) token is never accepted
_jwk_client = None


def oidc_enabled() -> bool:
    return bool(config.KEYCLOAK_URL)


def _oidc_issuer() -> str:
    return config.KEYCLOAK_ISSUER or f"{config.KEYCLOAK_URL}/realms/{config.KEYCLOAK_REALM}"


def _jwks():
    global _jwk_client
    if _jwk_client is None:
        from jwt import PyJWKClient

        _jwk_client = PyJWKClient(
            f"{config.KEYCLOAK_URL}/realms/{config.KEYCLOAK_REALM}/protocol/openid-connect/certs",
            cache_keys=True, lifespan=3600, timeout=5,
        )
    return _jwk_client


def decode_oidc_token(token: str) -> dict:
    """Verify a Keycloak access token: signature (via the realm's JWKS), issuer, audience and expiry."""
    try:
        signing_key = _jwks().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, signing_key, algorithms=_OIDC_ALGORITHMS,
            audience=config.KEYCLOAK_AUDIENCE, issuer=_oidc_issuer(),
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(status_code=503, detail="Identity provider is unreachable.")
    except (jwt.InvalidTokenError, jwt.PyJWKClientError):
        raise HTTPException(status_code=401, detail="Invalid authentication token.")


def _claim(payload: dict, dotted: str):
    value = payload
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def user_from_oidc_claims(payload: dict) -> dict:
    """Map a verified Keycloak token onto the user dict the rest of ARGUS uses."""
    roles = _claim(payload, config.KEYCLOAK_ROLE_CLAIM) or []
    role = next((r for r in _ARGUS_ROLES_BY_PRIVILEGE if r in roles), None)  # most privileged ARGUS role wins
    if role is None:
        raise HTTPException(status_code=403, detail="Your account has no ARGUS role assigned.")
    username = payload.get("preferred_username") or payload["sub"]
    return {
        "user_id": payload["sub"],
        "employee_id": username,
        "full_name": payload.get("name") or username,
        "role": role,
        "jurisdiction": str(payload.get(config.KEYCLOAK_JURISDICTION_CLAIM) or ""),
    }


async def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """FastAPI dependency: extracts and validates the Bearer token."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")
    token = authorization.split(" ", 1)[1].strip()
    if oidc_enabled():
        user = user_from_oidc_claims(decode_oidc_token(token))   # ARGUS's own tokens are not accepted in this mode
        set_request_user(user)
        return user
    payload = decode_access_token(token)
    if payload.get("purpose"):
        # Short-lived MFA step tokens must never work as session tokens.
        raise HTTPException(status_code=401, detail="Invalid authentication token.")
    user = {
        "user_id": payload["sub"],
        "employee_id": payload["employee_id"],
        "full_name": payload["full_name"],
        "role": payload["role"],
        "jurisdiction": payload["jurisdiction"],
    }
    set_request_user(user)  # lets the database layer apply row-level security for this caller
    return user


def require_role(*roles: str):
    """FastAPI dependency factory: raises 403 unless the caller's role is
    in `roles`. Usage: Depends(require_role("admin"))."""
    from fastapi import Depends

    def wrapped(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user["role"] not in roles:
            raise HTTPException(status_code=403, detail=f"Requires role: {' or '.join(roles)}.")
        return current_user

    return wrapped


# ---------------------------------------------------------------------------
# Multi-factor authentication (TOTP, RFC 6238) — no external identity provider needed
# ---------------------------------------------------------------------------

TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret: str, counter: int, digits: int = TOTP_DIGITS) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(value).zfill(digits)


def totp_code(secret: str, for_time: Optional[float] = None, digits: int = TOTP_DIGITS) -> str:
    return _hotp(secret, int((for_time if for_time is not None else time.time()) // TOTP_STEP_SECONDS), digits)


def verify_totp(secret: str, code: str, last_step: Optional[int] = None, window: int = 1,
                now: Optional[float] = None) -> Optional[int]:
    """Return the matched time-step, or None. Steps at or before `last_step` are rejected so a
    captured code cannot be replayed within its validity window."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != TOTP_DIGITS:
        return None
    current = int((now if now is not None else time.time()) // TOTP_STEP_SECONDS)
    for step in range(current - window, current + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_hotp(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str) -> str:
    from urllib.parse import quote
    return (
        f"otpauth://totp/{quote(MFA_ISSUER)}:{quote(account)}"
        f"?secret={secret}&issuer={quote(MFA_ISSUER)}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP_SECONDS}"
    )


def _fernet():
    from cryptography.fernet import Fernet
    key = MFA_ENCRYPTION_KEY or base64.urlsafe_b64encode(hashlib.sha256(("argus-dev-mfa:" + JWT_SECRET).encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def new_recovery_codes(count: int = 8) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return ["".join(secrets.choice(alphabet) for _ in range(5)) + "-" + "".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").upper().encode()).hexdigest()


def create_purpose_token(user_id: str, purpose: str, minutes: int = 5) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user_id, "purpose": purpose, "iat": now, "exp": now + timedelta(minutes=minutes)}, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_purpose_token(token: str, purpose: str) -> str:
    """Return the user_id in a valid, unexpired purpose token."""
    payload = decode_access_token(token)
    if payload.get("purpose") != purpose:
        raise HTTPException(status_code=401, detail="Invalid or expired sign-in step. Please start again.")
    return payload["sub"]
