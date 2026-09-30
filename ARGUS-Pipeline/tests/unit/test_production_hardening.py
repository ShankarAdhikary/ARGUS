"""Phase 7: production guards, TLS/Vault wiring and secret loading."""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

import config

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


preflight = _load("preflight", "scripts/preflight.py")
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
DEBUG = yaml.safe_load((ROOT / "docker-compose.debug.yml").read_text())


def _published(compose):
    return {(name, str(p).split(":")[-2]) for name, svc in compose["services"].items() for p in svc.get("ports", [])}


def test_only_nginx_publishes_ports_and_only_80_443():
    ports = _published(COMPOSE)
    assert {s for s, _ in ports} == {"nginx"}
    assert all(("80" in p or "443" in p) for _, p in ports)


def test_debug_ports_are_gone_from_the_base_file_and_isolated_in_the_override():
    for banned in ("7474", "9001", "8000"):
        assert not any(banned in port for _, port in _published(COMPOSE))
    assert {"7474", "9001"} <= {p.split(":")[-2] for svc in DEBUG["services"].values() for p in svc.get("ports", [])}
    assert DEBUG["services"]["api"]["environment"]["ARGUS_DEBUG_PORTS_OPEN"] == "true"
    assert all(p.startswith("127.0.0.1:") for svc in DEBUG["services"].values() for p in svc.get("ports", []))


def test_api_and_worker_carry_no_secrets_in_their_environment():
    secret_names = {"JWT_SECRET", "MFA_ENCRYPTION_KEY", "VICTIM_HASH_PEPPER", "GROQ_API_KEY", "GEMINI_API_KEY", "POSTGRES_PASSWORD",
                    "NEO4J_PASSWORD", "REDIS_PASSWORD", "ELASTICSEARCH_PASSWORD", "ELASTIC_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"}
    for name in ("api", "worker"):
        env = set(COMPOSE["services"][name]["environment"])
        assert not env & secret_names, (name, env & secret_names)
        assert "vault_secrets:/vault/secrets:ro" in COMPOSE["services"][name]["volumes"]
        assert "vault-agent" in COMPOSE["services"][name]["depends_on"]


def test_vault_is_internal_and_uses_the_agent_sidecar_pattern():
    svc = COMPOSE["services"]
    assert svc["vault"]["image"] == "hashicorp/vault:1.15" and "ports" not in svc["vault"]
    assert svc["vault-agent"]["command"].startswith("agent ") and svc["vault-agent"]["depends_on"]["vault-init"]["condition"] == "service_completed_successfully"
    assert COMPOSE["volumes"]["vault_secrets"]["driver_opts"]["type"] == "tmpfs"
    for name in ("postgres", "neo4j", "elasticsearch", "minio", "redis"):
        assert "vault-agent" in svc[name]["depends_on"], name
    assert "VAULT_UNSEAL_KEY" in svc["vault-init"]["environment"]


def test_every_secret_file_the_agent_renders_is_read_by_config():
    agent = (ROOT / "vault/agent.hcl").read_text()
    for key in ("jwt_secret", "mfa_encryption_key", "victim_hash_pepper", "postgres_password", "neo4j_password", "redis_password",
                "elasticsearch_password", "minio_access_key", "minio_secret_key", "groq_api_key", "gemini_api_key"):
        assert f"/vault/secrets/{key}" in agent, key


def test_nginx_terminates_tls_and_proxies_to_the_api():
    conf = (ROOT / "nginx/nginx.conf").read_text()
    assert "listen 443 ssl" in conf and "TLSv1.3" in conf and "http://api:8000" in conf
    # A literal upstream is resolved once at startup and goes stale when the api container is recreated (502s).
    assert "resolver 127.0.0.11" in conf and "proxy_pass         $api_upstream" in conf and "proxy_pass         http://api" not in conf
    assert "return 301 https://" in conf and "Strict-Transport-Security" in conf


def test_preflight_flags_debug_ports_in_production():
    bad = {"services": {"neo4j": {"ports": [{"published": "7474", "target": 7474}]}, "nginx": {"ports": [{"published": "443", "target": 443}]}}}
    assert preflight.violations(bad) == ["neo4j publishes 7474 (Neo4j browser)"]
    assert preflight.violations({"services": {"nginx": {"ports": [{"published": "443"}, {"published": "80"}]}}}) == []
    assert preflight.published_ports({"services": {"a": {"ports": ["127.0.0.1:9001:9001"]}}}) == [("a", 9001)]


def test_production_refuses_debug_ports_flag(monkeypatch):
    strong = "9f2c41d07a6b3e58c1d4e7f09a2b6c3d8e5f1a70"
    for name in ("JWT_SECRET", "POSTGRES_PASSWORD", "NEO4J_PASSWORD", "MINIO_SECRET_KEY", "REDIS_PASSWORD", "ELASTICSEARCH_PASSWORD"):
        monkeypatch.setattr(config, name, strong)
    monkeypatch.setattr(config, "_DEV_MODE", False)
    monkeypatch.setattr(config, "MFA_ENCRYPTION_KEY", "k")
    monkeypatch.setattr(config, "CORS_ORIGINS", ["https://argus.example"])
    monkeypatch.delenv("SEED_DEMO_USERS", raising=False)
    monkeypatch.delenv("ARGUS_DEBUG_PORTS_OPEN", raising=False)
    config.assert_production_safe()                                   # clean production config passes
    monkeypatch.setenv("ARGUS_DEBUG_PORTS_OPEN", "true")
    with pytest.raises(RuntimeError, match="debug ports"):
        config.assert_production_safe()


def test_secret_file_wins_over_env_and_empty_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("MY_TOKEN", "from-env")
    assert config._secret("MY_TOKEN") == "from-env"
    (tmp_path / "my_token").write_text("from-vault\n")
    assert config._secret("MY_TOKEN") == "from-vault"
    (tmp_path / "my_token").write_text("\n")
    assert config._secret("MY_TOKEN") == "from-env"
    assert config._secret("NOT_SET_ANYWHERE") is None
