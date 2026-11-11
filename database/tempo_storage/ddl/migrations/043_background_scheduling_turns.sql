-- Scheduling survives worker restarts without changing any pending work.
CREATE TABLE background_scheduling_turns (
    lane TEXT PRIMARY KEY,
    next_turn BIGINT NOT NULL DEFAULT 0 CHECK(next_turn>=0 AND next_turn<7),
    promoted_since_turn BIGINT NOT NULL DEFAULT 0 CHECK(promoted_since_turn IN (0,1)),
    control_streak BIGINT NOT NULL DEFAULT 0 CHECK(control_streak>=0 AND control_streak<=2)
);
INSERT INTO background_scheduling_turns(lane) VALUES('durable');
CREATE INDEX idx_background_tasks_class_age
    ON background_tasks(kind,COALESCE(pending_since,created_at),id,next_attempt_at)
    WHERE state IN ('queued','retrying');
CREATE INDEX idx_background_tasks_expired_lease
    ON background_tasks(lease_expires_at,id) WHERE state='leased';
INSERT INTO tempo_schema_migrations(version) VALUES(43);
