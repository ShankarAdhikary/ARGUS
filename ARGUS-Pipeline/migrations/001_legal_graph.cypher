// Phase 2: legal charge + victim graph layer. Idempotent. Rollback: 001_legal_graph.rollback.cypher
CREATE CONSTRAINT legal_charge_id IF NOT EXISTS FOR (c:LegalCharge) REQUIRE c.charge_id IS UNIQUE;
CREATE CONSTRAINT victim_id IF NOT EXISTS FOR (v:Victim) REQUIRE v.victim_id IS UNIQUE;
CREATE INDEX legal_charge_fir IF NOT EXISTS FOR (c:LegalCharge) ON (c.fir_id);
CREATE INDEX legal_charge_section IF NOT EXISTS FOR (c:LegalCharge) ON (c.act, c.section);
