-- Preserve the exact fenced conflict while unrelated queue work can continue.
-- Rows are observation evidence, never permission to introduce the target.
CREATE TABLE study_position_introduction_holds (
    repertoire_id text NOT NULL,
    source_digest text NOT NULL,
    source_revision text NOT NULL,
    target_card_id text NOT NULL,
    history_card_id text NOT NULL,
    history_card_revision bigint NOT NULL CHECK (history_card_revision > 0),
    reason text NOT NULL CHECK (reason = 'position_introduction_history_changed'),
    first_seen_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    observations bigint NOT NULL DEFAULT 1 CHECK (observations > 0),
    PRIMARY KEY (repertoire_id, source_digest, target_card_id, history_card_id, history_card_revision, reason)
);
INSERT INTO tempo_schema_migrations(version) VALUES(65);
