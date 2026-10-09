-- Shared legal positions/evidence survive repertoire deletion. Route calculations are
-- immutable and reusable; only generation membership is repertoire-owned.
CREATE TABLE inventory_positions (
    fen_key TEXT PRIMARY KEY, fen TEXT NOT NULL, legal_reply_count BIGINT NOT NULL,
    legal_moves_version BIGINT NOT NULL DEFAULT 1 CHECK (legal_moves_version=1)
);
CREATE TABLE inventory_legal_replies (
    fen_key TEXT NOT NULL REFERENCES inventory_positions(fen_key), move_uci TEXT NOT NULL,
    resulting_fen_key TEXT NOT NULL, PRIMARY KEY(fen_key,move_uci)
);
CREATE INDEX inventory_reply_result ON inventory_legal_replies(resulting_fen_key);
CREATE TABLE inventory_generations (
    id TEXT PRIMARY KEY, repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    graph_generation BIGINT NOT NULL, source_revision BIGINT NOT NULL,
    prefix_revision BIGINT NOT NULL, preview_id TEXT,
    state TEXT NOT NULL CHECK(state IN ('building','published','superseded','failed')),
    created_at TEXT NOT NULL, published_at TEXT, last_error TEXT
);
CREATE INDEX inventory_generation_repertoire ON inventory_generations(repertoire_id,created_at,id);
CREATE UNIQUE INDEX inventory_one_build ON inventory_generations(repertoire_id) WHERE state='building';
ALTER TABLE repertoires ADD COLUMN inventory_publication_id TEXT;
CREATE TABLE inventory_routes (
    id TEXT PRIMARY KEY, start_fen TEXT NOT NULL, moves_json JSONB NOT NULL,
    trained_color TEXT NOT NULL CHECK(trained_color IN ('white','black')),
    scope_start_ply BIGINT NOT NULL, origin_json TEXT NOT NULL, absolute_ply_offset BIGINT NOT NULL,
    complete BIGINT NOT NULL DEFAULT 0 CHECK(complete IN (0,1)),
    completed_plies BIGINT NOT NULL DEFAULT 0, checkpoint_fen TEXT NOT NULL
);
CREATE TABLE inventory_generation_routes (
    generation_id TEXT NOT NULL REFERENCES inventory_generations(id) ON DELETE CASCADE,
    line_id TEXT NOT NULL, route_id TEXT NOT NULL REFERENCES inventory_routes(id),
    PRIMARY KEY(generation_id,line_id)
);
CREATE INDEX inventory_membership_route ON inventory_generation_routes(route_id,generation_id);
CREATE TABLE inventory_occurrences (
    route_id TEXT NOT NULL REFERENCES inventory_routes(id) ON DELETE CASCADE,
    ply BIGINT NOT NULL, fen_key TEXT NOT NULL, opponent BIGINT NOT NULL,
    authored_move TEXT, PRIMARY KEY(route_id,ply)
);
CREATE INDEX inventory_occurrence_position ON inventory_occurrences(fen_key,route_id,ply);
CREATE TABLE inventory_responses (
    generation_id TEXT NOT NULL REFERENCES inventory_generations(id) ON DELETE CASCADE,
    fen_key TEXT NOT NULL, move_uci TEXT NOT NULL, card_id TEXT NOT NULL,
    line_id TEXT NOT NULL, PRIMARY KEY(generation_id,fen_key,move_uci,card_id,line_id)
);
CREATE INDEX inventory_response_position ON inventory_responses(generation_id,fen_key,line_id);
CREATE TABLE position_cohort_evidence (
    fen_key TEXT NOT NULL REFERENCES inventory_positions(fen_key),
    source_id TEXT NOT NULL, model_version TEXT NOT NULL, schema_version TEXT NOT NULL,
    rating_cohort TEXT NOT NULL, speed_cohort TEXT NOT NULL DEFAULT '',
    query_parameters_json TEXT NOT NULL DEFAULT '{}',
    evidence_status TEXT NOT NULL DEFAULT 'not_fetched'
        CHECK(evidence_status IN ('not_fetched','pending','valid','stale','unavailable','failed')),
    fetched_at TEXT, valid_until TEXT, response_fingerprint TEXT,
    PRIMARY KEY(fen_key,source_id,model_version,schema_version,rating_cohort,speed_cohort)
);
-- One compact durable sweep, not startup traversal. Existing worker dispatch owns it.
INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,
    payload_version,payload_json,attempt_count,max_attempts,next_attempt_at,created_at,updated_at)
SELECT 'position-inventory-upgrade','position_inventory_reconcile','all',1,85,'queued','queued',
    1,'{"after_repertoire_id":""}',0,5,now()::text,now()::text,now()::text
WHERE EXISTS(SELECT 1 FROM repertoires);
INSERT INTO tempo_schema_migrations(version) VALUES(40);
