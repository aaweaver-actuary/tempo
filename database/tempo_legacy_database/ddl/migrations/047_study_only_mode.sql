ALTER TABLE settings ADD COLUMN study_only_mode BIGINT NOT NULL DEFAULT 0
    CHECK (study_only_mode IN (0,1));
INSERT INTO tempo_schema_migrations(version) VALUES(47);
