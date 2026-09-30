-- Destroys the evidence ledger. Disable the guard triggers first, since the table is append-only by design.
-- WARNING: this erases chain-of-custody records; only use it on a database that never held real evidence.
DROP TRIGGER IF EXISTS evidence_ledger_truncate_guard ON evidence_ledger;
DROP TRIGGER IF EXISTS evidence_ledger_row_guard ON evidence_ledger;
DROP TABLE IF EXISTS evidence_ledger;
DROP FUNCTION IF EXISTS evidence_ledger_guard();
