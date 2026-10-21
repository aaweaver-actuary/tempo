"""Existing SQLite frozen-order DDL; initialization authority stays with connections."""

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS frozen_opening_runs(run_id TEXT PRIMARY KEY,plan_checksum TEXT NOT NULL UNIQUE,plan_json TEXT NOT NULL,previous_run_id TEXT,created_at TEXT NOT NULL,reader_version TEXT NOT NULL,invalidated BIGINT NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS frozen_opening_ranks(run_id TEXT NOT NULL REFERENCES frozen_opening_runs(run_id),card_id TEXT NOT NULL,ordinal BIGINT NOT NULL CHECK(ordinal>0),fingerprint TEXT NOT NULL,memberships_json TEXT NOT NULL,definition_json TEXT NOT NULL,PRIMARY KEY(run_id,card_id),UNIQUE(run_id,ordinal))",
    "CREATE TABLE IF NOT EXISTS frozen_opening_pending(run_id TEXT PRIMARY KEY REFERENCES frozen_opening_runs(run_id))",
    "CREATE TABLE IF NOT EXISTS frozen_opening_queue_changes(run_id TEXT NOT NULL REFERENCES frozen_opening_runs(run_id),queue_id BIGINT NOT NULL,queue_date TEXT NOT NULL,card_id TEXT NOT NULL,before_position BIGINT NOT NULL,after_position BIGINT NOT NULL,definition_json TEXT NOT NULL,PRIMARY KEY(run_id,queue_id))",
    "CREATE TABLE IF NOT EXISTS frozen_opening_activation(id BIGINT PRIMARY KEY CHECK(id=1),run_id TEXT REFERENCES frozen_opening_runs(run_id),epoch BIGINT NOT NULL DEFAULT 0)",
    "INSERT INTO frozen_opening_activation(id,run_id,epoch) VALUES(1,NULL,0) ON CONFLICT(id) DO NOTHING",
]


GUARDED_TABLES = ('cards', 'repertoire_cards', 'repertoire_lines', 'opening_graph_steps',
                  'opening_graph_publications', 'reviews', 'opening_evidence_attempts',
                  'opening_evidence_events', 'opening_evidence_observations', 'teaching_states',
                  'queue_attempt_origins', 'repertoire_integrity_card_blocks', 'deleted_cards', 'settings')


GUARDED_TABLES += ('repertoires','daily_queue','opening_evidence_presentations')


SQLITE_GUARDS = [
    f"CREATE TRIGGER IF NOT EXISTS frozen_guard_{table}_{operation.lower()} AFTER {operation} ON {table} BEGIN UPDATE frozen_opening_runs SET invalidated=1 WHERE run_id IN (SELECT run_id FROM frozen_opening_pending); END"
    for table in GUARDED_TABLES for operation in ('INSERT', 'UPDATE', 'DELETE')
]
