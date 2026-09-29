"""Create ARGUS-Pipeline/.env from .env.example with strong random secrets.

    python scripts/init_env.py            # refuses to overwrite an existing .env
    python scripts/init_env.py --force    # replace it (the old file is kept as .env.bak)

Every value that is `CHANGE_ME` (or an empty secret) gets a fresh random one. Nothing is printed except the file
location, so the secrets are not left in your terminal history or logs. Uses only the standard library.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

# Secrets that must be filled even though the example leaves them empty.
ALWAYS_GENERATE = {"REDIS_PASSWORD", "MFA_ENCRYPTION_KEY", "JWT_SECRET"}


def generate(name: str) -> str:
    if name == "MFA_ENCRYPTION_KEY":
        return base64.urlsafe_b64encode(os.urandom(32)).decode()      # a valid Fernet key
    if name == "JWT_SECRET":
        return secrets.token_hex(32)
    if name == "MINIO_ACCESS_KEY":
        return "argus" + secrets.token_hex(6)
    # Hex only: safe inside YAML, shell and connection URIs.
    return secrets.token_hex(24)


def build(text: str) -> str:
    out = []
    for line in text.splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            name, value = match.group(1), match.group(2).strip()
            if value.upper().startswith("CHANGE_ME") or (not value and name in ALWAYS_GENERATE):
                line = f"{name}={generate(name)}"
        out.append(line)
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="overwrite an existing .env (kept as .env.bak)")
    args = parser.parse_args()

    if not EXAMPLE.exists():
        print(f"Cannot find {EXAMPLE}", file=sys.stderr)
        return 1
    if TARGET.exists():
        if not args.force:
            print(f"{TARGET} already exists — not touching it. Use --force to replace it (a .env.bak copy is kept).", file=sys.stderr)
            return 1
        shutil.copy2(TARGET, TARGET.with_suffix(".bak"))

    TARGET.write_text(build(EXAMPLE.read_text()))
    TARGET.chmod(stat.S_IRUSR | stat.S_IWUSR)   # owner-only
    print(f"Wrote {TARGET} with freshly generated secrets (mode 600).")
    print("Optional: add GROQ_API_KEY or GEMINI_API_KEY for better text extraction. Keep this file out of git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
