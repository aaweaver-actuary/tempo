ALTER TABLE settings ADD COLUMN defensive_analysis_enabled BIGINT NOT NULL DEFAULT 0
    CHECK(defensive_analysis_enabled IN (0, 1));
INSERT INTO tempo_schema_migrations(version) VALUES (31);
