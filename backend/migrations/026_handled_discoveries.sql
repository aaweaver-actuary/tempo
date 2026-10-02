-- Keep completed decisions out of the discovery inbox without removing study history.
ALTER TABLE repertoire_opportunities ADD COLUMN handled_evidence_json TEXT;
UPDATE repertoire_opportunities SET handled_evidence_json=evidence_json
WHERE admission_state='queued' AND admitted_card_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM discovery_admission_intents AS intent
    WHERE intent.opportunity_id=repertoire_opportunities.id
      AND intent.evidence_fingerprint=repertoire_opportunities.evidence_fingerprint
      AND intent.state='queued'
      AND intent.card_id=repertoire_opportunities.admitted_card_id
  );
-- An old queued flag may belong to a previous revision. Keep current evidence
-- actionable without changing the historical intent, card, queue, or reviews.
UPDATE repertoire_opportunities SET admission_state=NULL,admitted_card_id=NULL,seen_at=NULL
WHERE admission_state='queued' AND handled_evidence_json IS NULL;
INSERT INTO tempo_schema_migrations(version) VALUES (26);
