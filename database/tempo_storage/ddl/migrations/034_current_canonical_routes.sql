-- Previously stored certificates have no current-source proof and fail closed.
ALTER TABLE canonical_prefix_positions ADD COLUMN source_revision BIGINT NOT NULL DEFAULT -1;
CREATE INDEX canonical_prefix_preview_versions ON canonical_prefix_previews(repertoire_id,expected_revision,source_revision,created_at DESC);
INSERT INTO tempo_schema_migrations(version) VALUES (34);
