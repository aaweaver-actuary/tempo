-- Fail closed on existing duplicates; never choose or delete an immutable draft.
CREATE UNIQUE INDEX bridge_proposal_identity
ON content.bridge_drafts ((document->'proposal'->>'workspace_id'), source_key,
                          (document->'proposal'->>'proposal_id'))
WHERE jsonb_typeof(document->'proposal') = 'object';
