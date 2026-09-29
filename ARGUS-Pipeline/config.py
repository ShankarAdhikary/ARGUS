"""Runtime configuration for the ARGUS MVP.

All infrastructure addresses come from environment variables so the same code
can run locally or inside Docker Compose.

SECURITY: Every secret must be set explicitly. The application will refuse to
start if any required secret is missing when running outside of development mode.
"""

import os


def _require(key: str, default: str | None = None) -> str:
    """Return the env var value or raise RuntimeError if it is missing in production."""
    value = os.getenv(key, default)
    if not value:
        raise RuntimeError(
            f"[ARGUS] Required environment variable {key!r} is not set. "
            "Set it in your .env file or container environment before starting."
        )
    return value


_DEV_MODE = os.getenv("ARGUS_ENV", "production").lower() in ("dev", "development", "local")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
MINIO_SECRET_KEY = _require("MINIO_SECRET_KEY", "password" if _DEV_MODE else None)
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
BUCKET_NAME = os.getenv("MINIO_BUCKET", "argus-raw-data")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None  # None = no auth (local dev)
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
ELASTICSEARCH_USER = os.getenv("ELASTICSEARCH_USER", "elastic")
ELASTICSEARCH_PASSWORD = os.getenv("ELASTICSEARCH_PASSWORD")
# Only send basic auth when a password is configured, so a local unsecured
# Elasticsearch still works for development.
ES_AUTH_KWARGS = (
    {"basic_auth": (ELASTICSEARCH_USER, ELASTICSEARCH_PASSWORD)}
    if ELASTICSEARCH_PASSWORD
    else {}
)
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _require("NEO4J_PASSWORD", "password" if _DEV_MODE else None)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

# Gemini is reached through its OpenAI-compatible surface, so both providers
# share one request shape and differ only in URL, key and model.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
)
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if origin.strip()]

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "admin")
POSTGRES_PASSWORD = _require("POSTGRES_PASSWORD", "password" if _DEV_MODE else None)
POSTGRES_DB = os.getenv("POSTGRES_DB", "argus_db")

JWT_SECRET = _require("JWT_SECRET", "argus-dev-secret-change-in-production" if _DEV_MODE else None)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480" if _DEV_MODE else "60"))

# Built-in TOTP MFA (authenticator apps). Roles listed here MUST enrol before they can sign in;
# everyone else may opt in from the Security page.
MFA_REQUIRED_ROLES = [r.strip() for r in os.getenv("MFA_REQUIRED_ROLES", "").split(",") if r.strip()]
MFA_ISSUER = os.getenv("MFA_ISSUER", "ARGUS")
# Fernet key used to encrypt TOTP secrets at rest. Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MFA_ENCRYPTION_KEY = os.getenv("MFA_ENCRYPTION_KEY", "")

LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCKOUT_SECONDS = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "900"))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(100 * 1024 * 1024)))


def assert_production_safe() -> None:
    """Refuse to boot with unsafe settings unless ARGUS_ENV is dev/local."""
    if _DEV_MODE:
        return
    problems = []
    if len(JWT_SECRET) < 32:
        problems.append("JWT_SECRET must be at least 32 characters")
    if os.getenv("SEED_DEMO_USERS", "false").lower() == "true":
        problems.append("SEED_DEMO_USERS must not be true (demo credentials are public)")
    if "*" in CORS_ORIGINS:
        problems.append("CORS_ORIGINS must not contain '*'")
    if not REDIS_PASSWORD:
        problems.append("REDIS_PASSWORD must be set")
    if not MFA_ENCRYPTION_KEY:
        problems.append("MFA_ENCRYPTION_KEY must be set (Fernet key for TOTP secrets)")
    if problems:
        raise RuntimeError("[ARGUS] Unsafe production configuration: " + "; ".join(problems))
