-- The database is workspace-bound; retain the workspace in semantic identity too.
CREATE TABLE learning.accepted_evidence_revisions (
    workspace_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    evidence_revision BIGINT NOT NULL CHECK (evidence_revision > 0),
    semantic_digest TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, evidence_id)
);

-- Old transport receipts contain acknowledgements, not the semantic payload.
-- NULL explicitly means that same-revision content cannot be verified. Exact
-- transport retries still replay their original receipt; only a newer revision
-- can establish a known semantic digest.
INSERT INTO learning.accepted_evidence_revisions
    (workspace_id, evidence_id, evidence_revision)
SELECT result_json::jsonb->>'workspace_id',
       result_json::jsonb->>'evidence_id',
       MAX((result_json::jsonb->>'evidence_revision')::bigint)
FROM learning.evidence_deliveries
WHERE result_json::jsonb->>'status' = 'completed'
GROUP BY result_json::jsonb->>'workspace_id', result_json::jsonb->>'evidence_id';
