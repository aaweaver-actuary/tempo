-- Select one superseded run by repertoire; drain its indexed children first.
-- Equality lookups avoid locale-sensitive task namespace ranges.
CREATE INDEX idx_segmentation_run_cleanup ON opening_segmentation_runs(repertoire_id,id);
INSERT INTO tempo_schema_migrations(version) VALUES (46);
