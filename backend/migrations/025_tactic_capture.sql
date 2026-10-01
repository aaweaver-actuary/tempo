CREATE TABLE tactic_captures (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('puzzle_rush','game','manual','other')),
    source_ref TEXT,
    source_url TEXT,
    note TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX idx_tactic_captures_card ON tactic_captures(card_id);

-- Only the known game-tactics path produced the obsolete plural value.
UPDATE cards SET content_type='tactic'
WHERE content_type='tactics' AND (repertoire_id='__game_tactics__' OR EXISTS(
    SELECT 1 FROM game_findings finding WHERE finding.card_id=cards.id
      AND finding.kind='tactical miss' AND finding.id=cards.source_ref
));
UPDATE daily_queue SET card_bucket='tactic'
WHERE card_bucket='tactics' AND card_id IN (SELECT id FROM cards WHERE content_type='tactic'
           AND (repertoire_id='__game_tactics__' OR EXISTS(
             SELECT 1 FROM game_findings finding WHERE finding.card_id=cards.id
               AND finding.kind='tactical miss' AND finding.id=cards.source_ref)));
INSERT INTO tempo_schema_migrations(version) VALUES (25);
