-- Non-serving representative retained-history staging. No authority switch.
CREATE TABLE retained_position_batches (
    transfer_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    batch_digest TEXT NOT NULL,
    target_generation TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    cursor TEXT,
    PRIMARY KEY (transfer_id, batch_id)
);
CREATE TABLE retained_position_states (
    repertoire_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    position TEXT NOT NULL,
    expected_uci TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    owner_epoch TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    version BIGINT NOT NULL CHECK (version > 0),
    fsrs_internal_card_id BIGINT NOT NULL UNIQUE CHECK (fsrs_internal_card_id > 0),
    fsrs_json TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    introduced_at TIMESTAMPTZ,
    donor_card_id TEXT NOT NULL,
    donor_card_revision BIGINT NOT NULL,
    donor_fingerprint TEXT NOT NULL,
    donor_selection_policy TEXT NOT NULL,
    source_fact_digest TEXT NOT NULL,
    PRIMARY KEY (repertoire_id, variant, position)
);
-- Exact encoded facts and references preserve IDs, review invalidation and
-- issued/grade provenance without claiming that they are runtime tables.
CREATE TABLE retained_position_facts (
    source_key TEXT PRIMARY KEY,
    target_key TEXT NOT NULL UNIQUE,
    family TEXT NOT NULL,
    codec TEXT NOT NULL,
    original_bytes BYTEA NOT NULL,
    original_digest TEXT NOT NULL,
    references_json JSONB NOT NULL,
    transfer_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    FOREIGN KEY (transfer_id, batch_id) REFERENCES retained_position_batches(transfer_id, batch_id)
);
