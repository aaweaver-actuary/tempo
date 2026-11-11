ALTER TABLE repertoires ADD COLUMN new_cards_per_day BIGINT
    CHECK(new_cards_per_day BETWEEN 0 AND 100);
INSERT INTO tempo_schema_migrations(version) VALUES (29);
