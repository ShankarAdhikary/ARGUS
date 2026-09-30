"""Production must refuse to run while any account still has a published demo password."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import auth_security
import config
import demo_guard

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fast_hashes(monkeypatch):
    monkeypatch.setattr(auth_security, "PBKDF2_ITERATIONS", 1000)      # same code path, milliseconds instead of ~70 ms per derivation


def account(employee_id, password):
    h, salt = auth_security.hash_password(password)
    return {"employee_id": employee_id, "password_hash": h, "salt": salt}


def test_published_demo_passwords_come_from_the_seed_list():
    assert demo_guard.demo_passwords() == {"password123", "demo123"}


def test_flags_only_accounts_whose_password_is_a_demo_password():
    rows = [account("admin@demo.com", "password123"), account("INV001", "demo123"),
            account("real.officer", "c0rrect-horse-battery-staple"), account("renamed.demo", "demo123"),
            {"employee_id": "sso.user", "password_hash": None, "salt": None}]      # SSO-provisioned: no local password to check
    assert demo_guard.demo_credentials_in_use(rows) == ["INV001", "admin@demo.com", "renamed.demo"]     # found by password, not by name
    assert demo_guard.demo_credentials_in_use([account("real.officer", "c0rrect-horse-battery-staple")]) == []
    assert demo_guard.demo_credentials_in_use([]) == []


def test_each_users_own_salt_is_respected():
    a, b = account("u1", "demo123"), account("u2", "demo123")
    assert a["password_hash"] != b["password_hash"]                        # same password, different salts: hashes cannot be compared directly
    assert demo_guard.demo_credentials_in_use([a, b]) == ["u1", "u2"]


def test_production_refuses_to_start_and_names_the_accounts(monkeypatch):
    monkeypatch.setattr(config, "_DEV_MODE", False)
    monkeypatch.setattr(config, "KEYCLOAK_URL", "")
    monkeypatch.setattr(demo_guard, "find_in_database", lambda: ["INV001", "admin@demo.com"])
    with pytest.raises(RuntimeError, match=r"Refusing to start in production.*INV001, admin@demo\.com"):
        demo_guard.assert_no_demo_credentials()
    monkeypatch.setattr(demo_guard, "find_in_database", lambda: [])
    demo_guard.assert_no_demo_credentials()                                # clean database: starts


def test_check_fails_closed_when_the_database_cannot_be_read(monkeypatch):
    monkeypatch.setattr(config, "_DEV_MODE", False)
    monkeypatch.setattr(config, "KEYCLOAK_URL", "")
    monkeypatch.setattr(demo_guard, "find_in_database", MagicMock(side_effect=ConnectionError("db down")))
    with pytest.raises(RuntimeError, match="could not check accounts.*db down"):
        demo_guard.assert_no_demo_credentials()


def test_guard_is_a_noop_in_development_and_when_sso_replaces_local_sign_in(monkeypatch):
    explode = MagicMock(side_effect=AssertionError("must not touch the database"))
    monkeypatch.setattr(demo_guard, "find_in_database", explode)
    monkeypatch.setattr(config, "_DEV_MODE", True)
    demo_guard.assert_no_demo_credentials()
    monkeypatch.setattr(config, "_DEV_MODE", False)
    monkeypatch.setattr(config, "KEYCLOAK_URL", "https://sso.example")
    demo_guard.assert_no_demo_credentials()
    explode.assert_not_called()


def test_startup_runs_the_guard():
    import main
    assert main.assert_no_demo_credentials is demo_guard.assert_no_demo_credentials


def _preflight():
    spec = importlib.util.spec_from_file_location("preflight_seed", ROOT / "scripts/preflight.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["preflight_seed"] = module
    spec.loader.exec_module(module)
    return module


def test_preflight_flags_services_that_seed_demo_accounts():
    pf = _preflight()
    cfg = {"services": {"api": {"environment": {"SEED_DEMO_USERS": "true"}}, "worker": {"environment": ["SEED_DEMO_USERS=false"]},
                        "other": {"environment": {"SEED_DEMO_USERS": "TRUE"}}, "nginx": {}}}
    assert pf.seed_violations(cfg) == ["api seeds the demo accounts (SEED_DEMO_USERS=true); their passwords are public",
                                       "other seeds the demo accounts (SEED_DEMO_USERS=true); their passwords are public"]
    assert pf.seed_violations({"services": {"api": {"environment": {"SEED_DEMO_USERS": "false"}}}}) == []
    assert pf.seed_violations({}) == []
