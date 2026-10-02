-- Keep completed decisions out of the discovery inbox without removing study history.
ALTER TABLE repertoire_opportunities ADD COLUMN handled_evidence_json TEXT;
UPDATE repertoire_opportunities SET handled_evidence_json=evidence_json
WHERE admission_state='queued' AND admitted_card_id IS NOT NULL;
INSERT INTO tempo_schema_migrations(version) VALUES (26);
