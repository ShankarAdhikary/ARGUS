"""Postgres access layer for the ARGUS platform track (auth, cases, audit,
alerts, pattern feedback). Uses a small psycopg2 connection pool and plain
SQL — no ORM, matching the rest of the codebase's lightweight style.
"""

import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

from auth_security import hash_password
from config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
)

_pool: SimpleConnectionPool | None = None


def get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = SimpleConnectionPool(
            1,
            10,
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            dbname=POSTGRES_DB,
        )
    return _pool


@contextmanager
def get_cursor(commit: bool = False):
    """Yields a RealDictCursor from a pooled connection, returning it on exit."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('investigator', 'analyst', 'supervisor', 'admin')),
    jurisdiction TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_secret TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_last_step BIGINT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_recovery JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS cases (
    case_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    fir_number TEXT NOT NULL,
    jurisdiction TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'under_review', 'closed')),
    is_sensitive BOOLEAN NOT NULL DEFAULT FALSE,
    sensitivity_reason TEXT,
    category TEXT,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS case_entity_links (
    id SERIAL PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_value TEXT NOT NULL,
    linked_by TEXT NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_id, entity_type, entity_value)
);

CREATE TABLE IF NOT EXISTS case_notes (
    note_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    justification TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    previous_hash TEXT,
    record_hash TEXT
);

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS previous_hash TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS record_hash TEXT;
CREATE INDEX IF NOT EXISTS audit_log_occurred_at_idx ON audit_log (occurred_at);

-- Audit log is append-only: the only permitted UPDATE fills a NULL record_hash.
CREATE OR REPLACE FUNCTION audit_log_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.record_hash IS NULL AND NEW.record_hash IS NOT NULL
       AND (OLD.audit_id, OLD.user_full_name, OLD.role, OLD.action, OLD.resource, OLD.justification, OLD.occurred_at, OLD.previous_hash)
           IS NOT DISTINCT FROM
           (NEW.audit_id, NEW.user_full_name, NEW.role, NEW.action, NEW.resource, NEW.justification, NEW.occurred_at, NEW.previous_hash)
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_log is append-only (% blocked)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_row_guard ON audit_log;
CREATE TRIGGER audit_log_row_guard BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_guard();
DROP TRIGGER IF EXISTS audit_log_truncate_guard ON audit_log;
CREATE TRIGGER audit_log_truncate_guard BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_guard();

CREATE TABLE IF NOT EXISTS resolution_decisions (
    decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    candidate TEXT NOT NULL,
    similarity DOUBLE PRECISION,
    decision TEXT NOT NULL CHECK (decision IN ('confirm_merge', 'reject')),
    decided_by TEXT NOT NULL,
    note TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_rules (
    rule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_value TEXT NOT NULL,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id UUID REFERENCES alert_rules(rule_id) ON DELETE CASCADE,
    entity_value TEXT NOT NULL,
    message TEXT NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_status BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS pattern_feedback (
    pattern_id TEXT PRIMARY KEY,
    verdict TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

DEMO_USERS = [
    # employee_id, password, full_name, role, jurisdiction
    ("admin@demo.com", "password123", "Demo Administrator", "admin", "National"),
    ("INV001", "demo123", "Rahul Verma", "investigator", "Central District"),
    ("ANL001", "demo123", "Ayesha Khan", "analyst", "State HQ"),
    ("SUP001", "demo123", "D. Iyer", "supervisor", "State HQ"),
    ("ADM001", "demo123", "System Admin", "admin", "National"),
]


def init_db() -> None:
    """Creates the platform schema and optionally seeds demo users.

    Demo user seeding only runs when the SEED_DEMO_USERS environment variable
    is explicitly set to "true". This prevents demo credentials from being
    created in production deployments that do not set the flag.
    """
    _seed_demos = os.getenv("SEED_DEMO_USERS", "false").lower() == "true"
    with get_cursor(commit=True) as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        cur.execute(SCHEMA)
        if _seed_demos:
            for employee_id, password, full_name, role, jurisdiction in DEMO_USERS:
                cur.execute("SELECT 1 FROM users WHERE employee_id = %s", (employee_id,))
                if cur.fetchone():
                    continue
                password_hash, salt = hash_password(password)
                cur.execute(
                    """
                    INSERT INTO users (employee_id, password_hash, salt, full_name, role, jurisdiction)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (employee_id, password_hash, salt, full_name, role, jurisdiction),
                )
