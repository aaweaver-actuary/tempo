-- Visibility only: job/results/diagnostics and all study records stay intact.
CREATE TABLE activity_history_preferences(id BIGINT PRIMARY KEY CHECK(id=1),cleared_through TEXT,signing_key TEXT NOT NULL);
INSERT INTO activity_history_preferences(id,signing_key) VALUES(1,gen_random_uuid()::text||gen_random_uuid()::text);
CREATE INDEX activity_task_completion ON background_tasks(completed_at,id) WHERE state='complete';
INSERT INTO tempo_schema_migrations(version) VALUES(48);
