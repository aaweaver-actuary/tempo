-- Repertoire-owned preview/approval evidence; exact #492 SCHEMA_SQL handoff.
CREATE TABLE product_repertoire_previews (
    repertoire_id text NOT NULL REFERENCES repertoires(id),
    proposal_id text NOT NULL,
    proposal_revision bigint NOT NULL CHECK (proposal_revision > 0),
    display_digest text NOT NULL,
    proposal_json text NOT NULL,
    PRIMARY KEY (repertoire_id, proposal_id)
);
CREATE TABLE product_repertoire_change_receipts (
    repertoire_id text NOT NULL,
    proposal_id text NOT NULL,
    receipt_json text NOT NULL,
    before_json text,
    after_json text,
    PRIMARY KEY (repertoire_id, proposal_id),
    FOREIGN KEY (repertoire_id, proposal_id)
        REFERENCES product_repertoire_previews(repertoire_id, proposal_id),
    CHECK ((before_json IS NULL) = (after_json IS NULL))
);

-- Proposed additive Study owner DDL; global migration/readiness allocation is
-- intentionally separate from the qualified game-feedback release candidate.
CREATE TABLE study_conflict_holds (
    scope_key text PRIMARY KEY,
    repertoire_id text NOT NULL,
    route_id text NOT NULL,
    ply bigint NOT NULL CHECK(ply>=0),
    card_id text NOT NULL,
    card_revision bigint NOT NULL CHECK(card_revision>0),
    source_revision text NOT NULL,
    source_generation bigint NOT NULL,
    source_digest text NOT NULL,
    active boolean NOT NULL,
    first_seen_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE INDEX study_conflict_active_card ON study_conflict_holds(repertoire_id,card_id,card_revision)
    WHERE active;
CREATE INDEX study_conflict_active_scope ON study_conflict_holds(scope_key) WHERE active;
CREATE INDEX study_conflict_pending_queue ON daily_queue(card_id,queue_date,id)
    WHERE status IN ('queued','blocked') AND review_result_json IS NULL;
CREATE TABLE study_conflict_queue_holds (
    scope_key text NOT NULL,
    queue_entry_id bigint NOT NULL,
    card_id text NOT NULL,
    card_revision bigint NOT NULL,
    original_status text NOT NULL,
    PRIMARY KEY(scope_key,queue_entry_id)
);
CREATE TABLE study_conflict_receipts (
    command_id text PRIMARY KEY,
    payload_digest text NOT NULL,
    receipt_json text NOT NULL,
    processed_at timestamptz NOT NULL
);
-- No cascade FKs: queue rebuilds/content changes cannot erase hold/receipt
-- provenance or make an old suspended attempt current under a new definition.

INSERT INTO tempo_schema_migrations(version) VALUES(54);
