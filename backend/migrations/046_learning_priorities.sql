ALTER TABLE repertoires
    ADD COLUMN learning_priority_weight DOUBLE PRECISION NOT NULL DEFAULT 1
        CHECK(learning_priority_weight >= 0 AND learning_priority_weight < 'Infinity'::double precision),
    ADD COLUMN learning_priority_active BIGINT NOT NULL DEFAULT 1
        CHECK(learning_priority_active IN (0,1)),
    ADD COLUMN learning_priority_revision BIGINT NOT NULL DEFAULT 1
        CHECK(learning_priority_revision >= 1);
INSERT INTO tempo_schema_migrations(version) VALUES (46);
