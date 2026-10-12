ALTER TABLE settings ADD COLUMN repertoire_preparation_enabled BIGINT NOT NULL DEFAULT 1 CHECK (repertoire_preparation_enabled IN (0,1));
ALTER TABLE settings ADD COLUMN optional_analysis_and_imports_enabled BIGINT NOT NULL DEFAULT 1 CHECK (optional_analysis_and_imports_enabled IN (0,1));
INSERT INTO tempo_schema_migrations(version) VALUES(48);
