-- Additive publication storage. Legacy results remain intact and readable until
-- the first complete generation is accepted for their repertoire.
CREATE TABLE integrity_publications (
    repertoire_id TEXT PRIMARY KEY REFERENCES repertoires(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL, graph_generation BIGINT NOT NULL, published_at TEXT NOT NULL
);
CREATE TABLE integrity_issue_generations (
    run_id TEXT NOT NULL, id TEXT NOT NULL,
    repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, fen_key TEXT, fen TEXT, trained_color TEXT,
    signature TEXT NOT NULL, moves_json TEXT NOT NULL, sources_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    blocks_complete BIGINT NOT NULL DEFAULT 0 CHECK(blocks_complete IN (0,1)),
    PRIMARY KEY(run_id,id)
);
CREATE INDEX integrity_issue_generation_scope ON integrity_issue_generations(repertoire_id,run_id,id);
CREATE TABLE integrity_block_generations (
    run_id TEXT NOT NULL, repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE, issue_id TEXT NOT NULL,
    published_at TEXT NOT NULL, PRIMARY KEY(run_id,card_id,issue_id),
    FOREIGN KEY(run_id,issue_id) REFERENCES integrity_issue_generations(run_id,id) ON DELETE CASCADE
);
CREATE INDEX integrity_block_generation_card ON integrity_block_generations(card_id,repertoire_id,run_id);
CREATE INDEX integrity_block_generation_cleanup ON integrity_block_generations(repertoire_id,run_id,card_id,issue_id);
CREATE INDEX integrity_candidate_cleanup_run ON repertoire_integrity_issue_candidates(run_id COLLATE "C",id);
CREATE INDEX integrity_accumulator_cleanup_run ON repertoire_integrity_position_accumulators(run_id COLLATE "C",fen_key);
CREATE INDEX integrity_source_cleanup_run ON repertoire_integrity_source_runs(run_id COLLATE "C",source_offset);
CREATE VIEW current_repertoire_integrity_issues AS
    SELECT issue.id,issue.repertoire_id,issue.kind,issue.fen_key,issue.fen,issue.trained_color,
           issue.signature,issue.moves_json,issue.sources_json,issue.created_at,issue.updated_at
    FROM integrity_issue_generations issue JOIN integrity_publications published
      ON published.repertoire_id=issue.repertoire_id AND published.run_id=issue.run_id
    UNION ALL
    SELECT legacy.* FROM repertoire_integrity_issues legacy
    WHERE NOT EXISTS(SELECT 1 FROM integrity_publications published WHERE published.repertoire_id=legacy.repertoire_id);
CREATE VIEW current_repertoire_integrity_card_blocks AS
    SELECT block.repertoire_id,block.card_id,block.issue_id,block.run_id scan_generation,block.published_at
    FROM integrity_block_generations block JOIN integrity_publications published
      ON published.repertoire_id=block.repertoire_id AND published.run_id=block.run_id
    UNION ALL
    SELECT legacy.* FROM repertoire_integrity_card_blocks legacy
    WHERE NOT EXISTS(SELECT 1 FROM integrity_publications published WHERE published.repertoire_id=legacy.repertoire_id);
-- Eligibility has a separate conservative waiting guard; issue/block readers
-- still see only the complete accepted publication.
CREATE VIEW integrity_training_blocks AS
    SELECT repertoire_id,card_id FROM current_repertoire_integrity_card_blocks
    UNION ALL
    SELECT state.repertoire_id,card.id FROM repertoire_integrity_state state
    JOIN cards card ON card.repertoire_id=state.repertoire_id
    WHERE state.scan_status IN ('queued','running','retrying','failed') AND card.content_type='opening'
    UNION ALL
    SELECT state.repertoire_id,link.card_id FROM repertoire_integrity_state state
    JOIN repertoire_cards link ON link.repertoire_id=state.repertoire_id
    WHERE state.scan_status IN ('queued','running','retrying','failed');
CREATE OR REPLACE FUNCTION opening_evidence_bind_queue(queue_identifier BIGINT, card_identifier TEXT, admission_repertoire TEXT)
RETURNS void LANGUAGE sql AS $$
    INSERT INTO opening_evidence_queue_contexts(queue_entry_id,presentation_snapshot_id,repertoire_id,effective_trained_color)
    SELECT queue_identifier,snapshot.id,scope.repertoire_id,learner.effective_trained_color
    FROM cards card JOIN opening_evidence_presentations snapshot
      ON snapshot.card_id=card.id AND snapshot.revision=card.revision
      AND snapshot.start_fen=card.start_fen AND snapshot.moves_json=card.moves_json
      AND snapshot.trained_color IS NOT DISTINCT FROM card.trained_color
    CROSS JOIN LATERAL (
      SELECT candidate.repertoire_id FROM (
        SELECT card.repertoire_id UNION SELECT link.repertoire_id FROM repertoire_cards link WHERE link.card_id=card.id
      ) candidate JOIN repertoires repertoire ON repertoire.id=candidate.repertoire_id
      WHERE NOT EXISTS(SELECT 1 FROM integrity_training_blocks block
        WHERE block.card_id=card.id AND block.repertoire_id=candidate.repertoire_id)
      AND (candidate.repertoire_id=admission_repertoire OR
        (admission_repertoire IS NULL AND (SELECT COUNT(*) FROM (
          SELECT card.repertoire_id UNION SELECT link.repertoire_id FROM repertoire_cards link WHERE link.card_id=card.id
        ) owners JOIN repertoires existing ON existing.id=owners.repertoire_id
          WHERE NOT EXISTS(SELECT 1 FROM integrity_training_blocks block
            WHERE block.card_id=card.id AND block.repertoire_id=owners.repertoire_id))=1))
    ) scope
    -- Legacy color belongs to this proven scope, not to the raw presentation.
    -- Capture once: repertoire edits must not change historical checkpoint identity.
    CROSS JOIN LATERAL (
      SELECT COALESCE(card.trained_color,(SELECT line.trained_color FROM repertoire_lines line
        WHERE line.repertoire_id=scope.repertoire_id ORDER BY line.created_at,line.id LIMIT 1)) effective_trained_color
    ) learner
    WHERE card.id=card_identifier AND card.content_type='opening'
      AND learner.effective_trained_color IN ('white','black')
    ON CONFLICT DO NOTHING;
$$;

INSERT INTO tempo_schema_migrations(version) VALUES(45);
