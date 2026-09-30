// Removes the constraints/indexes AND all LegalCharge / Victim nodes created by the legal layer.
DROP INDEX legal_charge_section IF EXISTS;
DROP INDEX legal_charge_fir IF EXISTS;
DROP CONSTRAINT victim_id IF EXISTS;
DROP CONSTRAINT legal_charge_id IF EXISTS;
MATCH (n:LegalCharge) DETACH DELETE n;
MATCH (n:Victim) DETACH DELETE n;
