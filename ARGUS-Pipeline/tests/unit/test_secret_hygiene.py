"""Because the repository is public, its example values are known to everyone: they must never be usable for real."""

import base64
import importlib.util
import re
import sys
from pathlib import Path

import pytest

import config

ROOT = Path(__file__).resolve().parents[2]


def _load_init_env():
    spec = importlib.util.spec_from_file_location("init_env", ROOT / "scripts" / "init_env.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["init_env"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", [None, "", "changeme", "ChangeMe", "password", "admin", "secret", "short",
                                   "argus-dev-secret-change-in-production", "CHANGE_ME", "CHANGE_ME_generate_this"])
def test_placeholder_and_short_secrets_are_weak(value):
    assert config.is_weak_secret(value)


def test_strong_secret_is_accepted():
    assert not config.is_weak_secret("9f2c41d07a6b3e58c1d4e7f09a2b6c3d8e5f1a70")


def test_production_refuses_to_start_with_the_documented_placeholders(monkeypatch):
    monkeypatch.setattr(config, "_DEV_MODE", False)
    monkeypatch.setattr(config, "JWT_SECRET", "argus-dev-secret-change-in-production")     # long enough, but publicly known
    monkeypatch.setattr(config, "POSTGRES_PASSWORD", "changeme")
    monkeypatch.setattr(config, "REDIS_PASSWORD", "changeme")
    monkeypatch.setattr(config, "MFA_ENCRYPTION_KEY", "k")
    with pytest.raises(RuntimeError) as err:
        config.assert_production_safe()
    message = str(err.value)
    assert "JWT_SECRET is a placeholder" in message and "POSTGRES_PASSWORD" in message and "REDIS_PASSWORD" in message


def test_generated_env_has_no_placeholders_and_passes_the_production_check(tmp_path, monkeypatch):
    init_env = _load_init_env()
    text = init_env.build((ROOT / ".env.example").read_text())
    values = dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", text, flags=re.M))
    assert not [k for k, v in values.items() if v.upper().startswith("CHANGE_ME")]   # comments may still mention the word
    for name in ("POSTGRES_PASSWORD", "NEO4J_PASSWORD", "ELASTIC_PASSWORD", "MINIO_SECRET_KEY", "REDIS_PASSWORD", "JWT_SECRET"):
        assert not config.is_weak_secret(values[name]), name
    assert len(values["JWT_SECRET"]) >= 32
    assert len(base64.urlsafe_b64decode(values["MFA_ENCRYPTION_KEY"])) == 32      # a valid Fernet key
    # Optional keys stay as the example had them.
    assert values["GROQ_API_KEY"] == "" and values["ARGUS_ENV"] == "development"


def test_two_runs_produce_different_secrets():
    init_env = _load_init_env()
    example = (ROOT / ".env.example").read_text()
    assert init_env.build(example) != init_env.build(example)


def test_init_env_will_not_overwrite_an_existing_env(tmp_path, monkeypatch):
    init_env = _load_init_env()
    target = tmp_path / ".env"
    target.write_text("KEEP=me\n")
    monkeypatch.setattr(init_env, "TARGET", target)
    monkeypatch.setattr(sys, "argv", ["init_env.py"])
    assert init_env.main() == 1 and target.read_text() == "KEEP=me\n"
    monkeypatch.setattr(sys, "argv", ["init_env.py", "--force"])
    assert init_env.main() == 0
    assert (tmp_path / ".bak").exists() or (tmp_path / ".env.bak").exists() or any(p.suffix == ".bak" for p in tmp_path.iterdir())
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_the_env_file_is_never_copied_into_docker_images():
    if not (ROOT / ".dockerignore").exists():
        pytest.skip(".dockerignore is not part of a built image; checked when run from the repository")
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in ignore and "uploads/" in ignore and "backups/" in ignore
