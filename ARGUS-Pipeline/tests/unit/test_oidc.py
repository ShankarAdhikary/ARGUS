"""Keycloak / OIDC token verification. No Keycloak needed: a throw-away RSA key stands in for the realm's."""

import asyncio
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

import auth_security
import config

ISSUER = "https://sso.example.gov.in/realms/argus"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def oidc(monkeypatch, key):
    monkeypatch.setattr(config, "KEYCLOAK_URL", "https://sso.example.gov.in")
    monkeypatch.setattr(config, "KEYCLOAK_REALM", "argus")
    monkeypatch.setattr(config, "KEYCLOAK_ISSUER", "")
    monkeypatch.setattr(config, "KEYCLOAK_AUDIENCE", "argus-api")
    fake = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key()))
    monkeypatch.setattr(auth_security, "_jwks", lambda: fake)
    monkeypatch.setattr("request_context.set_request_user", lambda u: None)


def _token(key, **overrides):
    claims = {
        "iss": ISSUER, "aud": "argus-api", "sub": "kc-user-1", "exp": int(time.time()) + 300,
        "preferred_username": "INV777", "name": "Asha Rao", "jurisdiction": "Harbor Division",
        "realm_access": {"roles": ["investigator", "offline_access"]},
    }
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def _current(token):
    return asyncio.run(auth_security.get_current_user(authorization=f"Bearer {token}"))


def test_valid_token_is_mapped_to_an_argus_user(oidc, key):
    user = _current(_token(key))
    assert user == {"user_id": "kc-user-1", "employee_id": "INV777", "full_name": "Asha Rao",
                    "role": "investigator", "jurisdiction": "Harbor Division"}


def test_most_privileged_argus_role_wins(oidc, key):
    assert _current(_token(key, realm_access={"roles": ["analyst", "supervisor"]}))["role"] == "supervisor"


def test_token_without_an_argus_role_is_forbidden(oidc, key):
    with pytest.raises(HTTPException) as err:
        _current(_token(key, realm_access={"roles": ["offline_access"]}))
    assert err.value.status_code == 403


@pytest.mark.parametrize("bad", [
    {"exp": int(time.time()) - 10},           # expired
    {"iss": "https://evil.example/realms/argus"},   # wrong issuer
    {"aud": "some-other-api"},                # wrong audience
])
def test_bad_tokens_are_rejected(oidc, key, bad):
    with pytest.raises(HTTPException) as err:
        _current(_token(key, **bad))
    assert err.value.status_code == 401


def test_token_signed_by_another_key_is_rejected(oidc):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as err:
        _current(_token(other))
    assert err.value.status_code == 401


def test_shared_secret_forgery_is_rejected(oidc, key):
    # Classic algorithm-confusion attack: an attacker signs an HS256 token using the *public* key as the HMAC secret.
    # PyJWT refuses to build such a token, so it is assembled by hand here.
    import base64, hashlib, hmac, json

    from cryptography.hazmat.primitives import serialization

    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    claims = {"iss": ISSUER, "aud": "argus-api", "sub": "x", "exp": int(time.time()) + 300, "realm_access": {"roles": ["admin"]}}
    signing_input = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + b"." + b64(json.dumps(claims).encode())
    forged = (signing_input + b"." + b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())).decode()
    with pytest.raises(HTTPException) as err:
        _current(forged)
    assert err.value.status_code == 401


def test_local_argus_tokens_are_not_accepted_in_oidc_mode(oidc):
    local = auth_security.create_access_token({"user_id": "u", "employee_id": "E", "full_name": "N", "role": "admin", "jurisdiction": "National"})
    with pytest.raises(HTTPException) as err:
        _current(local)
    assert err.value.status_code == 401


def test_default_mode_is_unchanged_when_keycloak_is_not_configured(monkeypatch):
    monkeypatch.setattr(config, "KEYCLOAK_URL", "")
    monkeypatch.setattr("request_context.set_request_user", lambda u: None)
    assert auth_security.oidc_enabled() is False
    local = auth_security.create_access_token({"user_id": "u1", "employee_id": "E1", "full_name": "Local User", "role": "analyst", "jurisdiction": "State HQ"})
    assert _current(local)["employee_id"] == "E1"
