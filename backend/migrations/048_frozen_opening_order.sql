-- Additive Learning-owned frozen order; guard is present only during explicit apply.
CREATE TABLE IF NOT EXISTS frozen_opening_runs(run_id TEXT PRIMARY KEY,plan_checksum TEXT NOT NULL UNIQUE,plan_json TEXT NOT NULL,previous_run_id TEXT,created_at TEXT NOT NULL,reader_version TEXT NOT NULL,invalidated BIGINT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS frozen_opening_ranks(run_id TEXT NOT NULL REFERENCES frozen_opening_runs(run_id),card_id TEXT NOT NULL,ordinal BIGINT NOT NULL CHECK(ordinal>0),fingerprint TEXT NOT NULL,memberships_json TEXT NOT NULL,definition_json TEXT NOT NULL,PRIMARY KEY(run_id,card_id),UNIQUE(run_id,ordinal));
CREATE TABLE IF NOT EXISTS frozen_opening_pending(run_id TEXT PRIMARY KEY REFERENCES frozen_opening_runs(run_id));
CREATE TABLE IF NOT EXISTS frozen_opening_queue_changes(run_id TEXT NOT NULL REFERENCES frozen_opening_runs(run_id),queue_id BIGINT NOT NULL,queue_date TEXT NOT NULL,card_id TEXT NOT NULL,before_position BIGINT NOT NULL,after_position BIGINT NOT NULL,definition_json TEXT NOT NULL,PRIMARY KEY(run_id,queue_id));
CREATE TABLE IF NOT EXISTS frozen_opening_activation(id BIGINT PRIMARY KEY CHECK(id=1),run_id TEXT REFERENCES frozen_opening_runs(run_id),epoch BIGINT NOT NULL DEFAULT 0);
INSERT INTO frozen_opening_activation(id,run_id,epoch) VALUES(1,NULL,0) ON CONFLICT(id) DO NOTHING;
CREATE FUNCTION frozen_invalidate_pending() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE frozen_opening_runs SET invalidated=1
    WHERE run_id IN (SELECT run_id FROM frozen_opening_pending) AND invalidated=0;
    RETURN NULL;
END $$;
CREATE TRIGGER frozen_guard_cards AFTER INSERT OR UPDATE OR DELETE ON cards FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_repertoire_cards AFTER INSERT OR UPDATE OR DELETE ON repertoire_cards FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_repertoire_lines AFTER INSERT OR UPDATE OR DELETE ON repertoire_lines FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_opening_graph_steps AFTER INSERT OR UPDATE OR DELETE ON opening_graph_steps FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_opening_graph_publications AFTER INSERT OR UPDATE OR DELETE ON opening_graph_publications FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_reviews AFTER INSERT OR UPDATE OR DELETE ON reviews FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_opening_evidence_attempts AFTER INSERT OR UPDATE OR DELETE ON opening_evidence_attempts FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_opening_evidence_events AFTER INSERT OR UPDATE OR DELETE ON opening_evidence_events FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_opening_evidence_observations AFTER INSERT OR UPDATE OR DELETE ON opening_evidence_observations FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_teaching_states AFTER INSERT OR UPDATE OR DELETE ON teaching_states FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_queue_attempt_origins AFTER INSERT OR UPDATE OR DELETE ON queue_attempt_origins FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_repertoire_integrity_card_blocks AFTER INSERT OR UPDATE OR DELETE ON repertoire_integrity_card_blocks FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_deleted_cards AFTER INSERT OR UPDATE OR DELETE ON deleted_cards FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_settings AFTER INSERT OR UPDATE OR DELETE ON settings FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_repertoires AFTER INSERT OR UPDATE OR DELETE ON repertoires FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_daily_queue AFTER INSERT OR UPDATE OR DELETE ON daily_queue FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
CREATE TRIGGER frozen_guard_presentations AFTER INSERT OR UPDATE OR DELETE ON opening_evidence_presentations FOR EACH STATEMENT EXECUTE FUNCTION frozen_invalidate_pending();
INSERT INTO tempo_schema_migrations(version) VALUES(48);
