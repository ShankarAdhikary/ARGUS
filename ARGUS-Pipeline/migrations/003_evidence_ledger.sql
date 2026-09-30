-- Phase 5: tamper-evident evidence ledger. Idempotent. Rollback: 003_evidence_ledger.rollback.sql
--
-- Hash chain: row_hash = SHA-256 over (file_id, file_sha256, case_id, uploaded_by, uploaded_at, prev_hash) and
-- prev_hash = row_hash of the previous ledger row. Both are computed by the application under an advisory lock
-- (evidence.py), exactly as the audit log does. A GENERATED column cannot be used here: it may not read the previous
-- row, and casting timestamptz to text is not immutable, so Postgres rejects it.
CREATE TABLE IF NOT EXISTS evidence_ledger (
    seq BIGSERIAL UNIQUE,
    ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id TEXT NOT NULL,                       -- MinIO object key
    file_sha256 TEXT NOT NULL,
    case_id UUID REFERENCES cases(case_id),
    uploaded_by TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    prev_hash TEXT,
    row_hash TEXT NOT NULL,
    file_name TEXT,
    size_bytes BIGINT,
    content_type TEXT
);

CREATE OR REPLACE FUNCTION evidence_ledger_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'evidence_ledger is append-only (% blocked)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS evidence_ledger_row_guard ON evidence_ledger;
CREATE TRIGGER evidence_ledger_row_guard BEFORE UPDATE OR DELETE ON evidence_ledger
    FOR EACH ROW EXECUTE FUNCTION evidence_ledger_guard();
DROP TRIGGER IF EXISTS evidence_ledger_truncate_guard ON evidence_ledger;
CREATE TRIGGER evidence_ledger_truncate_guard BEFORE TRUNCATE ON evidence_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION evidence_ledger_guard();
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS evidence_ledger_case_idx ON evidence_ledger (case_id);
-- migrate:split
CREATE INDEX CONCURRENTLY IF NOT EXISTS evidence_ledger_file_idx ON evidence_ledger (file_id);
