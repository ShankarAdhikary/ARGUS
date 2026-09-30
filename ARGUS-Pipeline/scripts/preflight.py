"""Refuse to bring the stack up in production with debug ports published.

    python scripts/preflight.py && docker compose up -d

With ARGUS_ENV=production (environment or .env) it inspects the merged compose config and exits 1 if any service
publishes a debug/datastore port to the host (Neo4j browser 7474, MinIO console 9001, the API's 8000, ...). The only
ports production may publish are nginx's 80 and 443. In any other ARGUS_ENV it just reports what is published.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN = {
    7474: "Neo4j browser", 7687: "Neo4j bolt", 9001: "MinIO console", 9000: "MinIO API", 8000: "API (must go through nginx TLS)",
    5432: "PostgreSQL", 6379: "Redis", 9200: "Elasticsearch", 8200: "Vault",
}


def published_ports(config: dict) -> list[tuple[str, int]]:
    """[(service, host_port)] for every port a service publishes on the host."""
    out = []
    for name, svc in (config.get("services") or {}).items():
        for p in svc.get("ports") or []:
            published = p.get("published") if isinstance(p, dict) else str(p).split(":")[-2] if ":" in str(p) else None
            if published:
                out.append((name, int(str(published).split("-")[0])))
    return out


def violations(config: dict) -> list[str]:
    return [f"{svc} publishes {port} ({FORBIDDEN[port]})" for svc, port in published_ports(config) if port in FORBIDDEN]


def env_name() -> str:
    value = os.getenv("ARGUS_ENV")
    if value is None:
        env_file = Path(".env")
        if env_file.exists():
            m = re.search(r"^ARGUS_ENV=(\S+)", env_file.read_text(), flags=re.M)
            value = m.group(1) if m else None
    return (value or "development").lower()


def main() -> int:
    raw = subprocess.run(["docker", "compose", "config", "--format", "json"], capture_output=True, text=True)
    if raw.returncode != 0:
        print(raw.stderr, file=sys.stderr)
        return 2
    config = json.loads(raw.stdout)
    env = env_name()
    found = violations(config)
    if env == "production" and found:
        print("Refusing to start: ARGUS_ENV=production with debug ports open:", *found, sep="\n  - ", file=sys.stderr)
        print("Remove docker-compose.debug.yml from the compose command.", file=sys.stderr)
        return 1
    print(f"preflight ok (ARGUS_ENV={env}); published host ports: {sorted({p for _, p in published_ports(config)})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
