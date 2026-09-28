-- Keep one finding lookup within the background slice budget as analysis grows.
CREATE INDEX idx_game_findings_gap_cursor
    ON game_findings (repertoire_id, id)
    WHERE kind = 'repertoire gap';
INSERT INTO tempo_schema_migrations(version) VALUES (4);
