-- Preserve transaction-deadline episodes without deleting or resetting work.
ALTER TABLE background_tasks ADD COLUMN transaction_timeout_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE background_tasks ADD COLUMN transaction_timeout_checkpoint TEXT;
CREATE INDEX idx_opening_graph_current_roots
    ON opening_graph_steps(repertoire_id,generation,card_id NULLS FIRST) WHERE parent_card_id IS NULL;
CREATE INDEX idx_opening_graph_mature_parent_candidates
    ON opening_graph_steps(parent_card_id,card_id NULLS FIRST,repertoire_id,generation)
    WHERE parent_card_id IS NOT NULL;
CREATE INDEX idx_cards_locked_openings ON cards(id)
    WHERE content_type='opening' AND state='locked' AND archived=0;
CREATE INDEX idx_cards_mature_parent ON cards(id) WHERE state='mature';

INSERT INTO tempo_schema_migrations(version) VALUES(40);
