-- Non-serving single-position transaction prototype. No retained-stage adoption,
-- runtime selection, grants, source publication or legacy authority transition.
CREATE TABLE position_effect_authorities (
    repertoire_id TEXT PRIMARY KEY,
    owner_epoch TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    study_timezone TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope = 'disposable-prototype')
);
CREATE TABLE position_effect_states (
    repertoire_id TEXT NOT NULL REFERENCES position_effect_authorities(repertoire_id),
    variant TEXT NOT NULL CHECK (variant = 'standard'),
    position TEXT NOT NULL,
    expected_uci TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    owner_epoch TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    version BIGINT NOT NULL CHECK (version > 0),
    fsrs_internal_card_id BIGINT NOT NULL UNIQUE CHECK (fsrs_internal_card_id > 0),
    fsrs_json TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (repertoire_id, variant, position)
);
CREATE TABLE position_effect_attempts (
    attempt_id TEXT PRIMARY KEY,
    queue_entry_id BIGINT NOT NULL,
    card_id TEXT NOT NULL,
    card_revision BIGINT NOT NULL CHECK (card_revision > 0),
    repertoire_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    position TEXT NOT NULL,
    expected_uci TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    owner_epoch TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    issued_day DATE NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    due BOOLEAN NOT NULL,
    issued_version BIGINT NOT NULL CHECK (issued_version > 0),
    completion_receipt_id TEXT,
    completion_saved_at TIMESTAMPTZ,
    checkpoint BIGINT NOT NULL DEFAULT 0 CHECK (checkpoint >= 0),
    FOREIGN KEY (repertoire_id, variant, position)
        REFERENCES position_effect_states(repertoire_id, variant, position),
    CHECK ((completion_receipt_id IS NULL) = (completion_saved_at IS NULL))
);
CREATE TABLE position_effect_responses (
    event_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES position_effect_attempts(attempt_id),
    decision_index BIGINT NOT NULL CHECK (decision_index >= 0),
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL,
    UNIQUE (attempt_id, decision_index),
    UNIQUE (event_id, attempt_id, decision_index)
);
CREATE TABLE position_effect_receipts (
    effect_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL,
    decision_index BIGINT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL,
    repertoire_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    position TEXT NOT NULL,
    owner_epoch TEXT NOT NULL,
    grade TEXT NOT NULL CHECK (grade IN ('good','again','observe')),
    accepted_at TIMESTAMPTZ NOT NULL,
    accepted_study_day DATE NOT NULL,
    fsrs_before_json TEXT NOT NULL,
    fsrs_after_json TEXT NOT NULL,
    version_before BIGINT NOT NULL,
    version_after BIGINT NOT NULL,
    checkpoint BIGINT NOT NULL,
    UNIQUE (attempt_id, decision_index),
    FOREIGN KEY (event_id, attempt_id, decision_index)
        REFERENCES position_effect_responses(event_id, attempt_id, decision_index),
    CHECK (version_after = version_before + CASE WHEN grade = 'observe' THEN 0 ELSE 1 END)
);
-- Epoch deliberately excluded: the daily credit survives authority epochs.
CREATE UNIQUE INDEX position_effect_one_good_day ON position_effect_receipts
    (repertoire_id,variant,position,accepted_study_day) WHERE grade='good';
