-- Counts accepted automated selections, not retries, heartbeats or completions.
CREATE TABLE engine_scheduling_state (
    id BIGINT PRIMARY KEY CHECK(id=1),
    automated_streak BIGINT NOT NULL DEFAULT 0 CHECK(automated_streak BETWEEN 0 AND 3)
);
INSERT INTO engine_scheduling_state(id) VALUES(1);
INSERT INTO tempo_schema_migrations(version) VALUES(42);
