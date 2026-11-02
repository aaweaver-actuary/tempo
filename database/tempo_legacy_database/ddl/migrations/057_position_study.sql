-- Study-owned, inactive-by-default position scheduling. Existing card and review
-- state is deliberately untouched. Snapshot identifiers do not cascade to source.
CREATE TABLE study_position_epochs (
    repertoire_id TEXT NOT NULL,
    epoch TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (repertoire_id, epoch)
);

CREATE TABLE study_position_owners (
    repertoire_id TEXT PRIMARY KEY,
    epoch TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'inactive'
        CHECK (mode IN ('inactive', 'shadow', 'staged', 'active', 'paused')),
    updated_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (repertoire_id, epoch) REFERENCES study_position_epochs(repertoire_id, epoch)
);

-- Reservation only: legacy-ID namespace admission still requires a durable
-- preflight and a fence on all present and future legacy ID creators.
CREATE SEQUENCE study_position_fsrs_id_seq AS BIGINT
    MINVALUE 4503599627370496 MAXVALUE 9007199254740991
    START WITH 4503599627370496 NO CYCLE;

CREATE TABLE study_position_states (
    repertoire_id TEXT NOT NULL,
    variant TEXT NOT NULL CHECK (variant = 'standard'),
    position TEXT NOT NULL CHECK (position <> ''),
    expected_uci TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    owner_epoch TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    fsrs_internal_card_id BIGINT NOT NULL UNIQUE CHECK (fsrs_internal_card_id > 0),
    fsrs_json TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    introduced_at TIMESTAMPTZ,
    donor_card_id TEXT,
    donor_card_revision BIGINT,
    donor_fingerprint TEXT,
    donor_selection_policy TEXT,
    PRIMARY KEY (repertoire_id, variant, position),
    FOREIGN KEY (repertoire_id, owner_epoch) REFERENCES study_position_epochs(repertoire_id, epoch),
    CHECK ((donor_card_id IS NULL AND donor_card_revision IS NULL AND donor_fingerprint IS NULL AND donor_selection_policy IS NULL)
        OR (donor_card_id IS NOT NULL AND donor_card_revision IS NOT NULL AND donor_fingerprint IS NOT NULL AND donor_selection_policy IS NOT NULL))
);
CREATE INDEX study_position_due ON study_position_states
    (repertoire_id, owner_epoch, due_at, variant, position);

-- A page's outcomes, new state and checkpoint advance together. The source
-- owner supplies the manifest and namespace preflight; this table cannot certify them.
CREATE TABLE study_position_seed_runs (
    repertoire_id TEXT NOT NULL,
    owner_epoch TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    source_token_json JSONB NOT NULL,
    selection_policy TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    manifest_count BIGINT NOT NULL CHECK (manifest_count >= 0),
    allocator_preflight_json JSONB NOT NULL,
    checkpoint_variant TEXT,
    checkpoint_position TEXT,
    seeded_count BIGINT NOT NULL DEFAULT 0 CHECK (seeded_count >= 0),
    held_count BIGINT NOT NULL DEFAULT 0 CHECK (held_count >= 0),
    status TEXT NOT NULL DEFAULT 'incomplete' CHECK (status IN ('incomplete', 'complete')),
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (repertoire_id, owner_epoch),
    FOREIGN KEY (repertoire_id, owner_epoch) REFERENCES study_position_epochs(repertoire_id, epoch),
    CHECK ((checkpoint_variant IS NULL) = (checkpoint_position IS NULL)),
    CHECK (seeded_count + held_count <= manifest_count),
    CHECK ((status = 'incomplete' AND completed_at IS NULL)
        OR (status = 'complete' AND completed_at IS NOT NULL AND seeded_count + held_count = manifest_count))
);

CREATE TABLE study_position_seed_outcomes (
    repertoire_id TEXT NOT NULL,
    owner_epoch TEXT NOT NULL,
    variant TEXT NOT NULL CHECK (variant = 'standard'),
    position TEXT NOT NULL,
    expected_uci TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('seeded', 'hold')),
    hold_reason TEXT,
    donor_card_id TEXT,
    donor_card_revision BIGINT,
    donor_fingerprint TEXT,
    donor_selection_policy TEXT,
    donor_fsrs_internal_card_id BIGINT,
    allocated_fsrs_internal_card_id BIGINT UNIQUE,
    clone_fingerprint TEXT,
    introduced_at TIMESTAMPTZ,
    PRIMARY KEY (repertoire_id, owner_epoch, variant, position),
    FOREIGN KEY (repertoire_id, owner_epoch) REFERENCES study_position_seed_runs(repertoire_id, owner_epoch),
    CHECK ((disposition = 'hold' AND hold_reason IS NOT NULL AND donor_card_id IS NULL
            AND donor_card_revision IS NULL AND donor_fingerprint IS NULL AND donor_selection_policy IS NULL
            AND donor_fsrs_internal_card_id IS NULL AND allocated_fsrs_internal_card_id IS NULL
            AND clone_fingerprint IS NULL AND introduced_at IS NULL)
        OR (disposition = 'seeded' AND hold_reason IS NULL AND donor_card_id IS NOT NULL
            AND donor_card_revision IS NOT NULL AND donor_fingerprint IS NOT NULL AND donor_selection_policy IS NOT NULL
            AND donor_fsrs_internal_card_id IS NOT NULL AND allocated_fsrs_internal_card_id IS NOT NULL
            AND allocated_fsrs_internal_card_id BETWEEN 4503599627370496 AND 9007199254740991
            AND donor_fsrs_internal_card_id <> allocated_fsrs_internal_card_id
            AND clone_fingerprint IS NOT NULL AND introduced_at IS NOT NULL))
);

-- A plan is an immutable issuance snapshot, including route and due witnesses.
-- The owner rejects a different payload under the same attempt identity.
CREATE TABLE study_position_issued_plans (
    attempt_id TEXT PRIMARY KEY,
    queue_entry_id BIGINT NOT NULL,
    card_id TEXT NOT NULL,
    card_revision BIGINT NOT NULL,
    repertoire_id TEXT NOT NULL,
    variant TEXT NOT NULL CHECK (variant = 'standard'),
    owner_epoch TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    issue_study_day DATE NOT NULL,
    plan_digest TEXT NOT NULL,
    plan_json JSONB NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    UNIQUE (attempt_id, repertoire_id, variant, owner_epoch),
    UNIQUE (queue_entry_id, card_id, card_revision),
    FOREIGN KEY (repertoire_id, owner_epoch) REFERENCES study_position_epochs(repertoire_id, epoch)
);

CREATE INDEX study_position_plan_clean_lookup ON study_position_issued_plans
    (repertoire_id, owner_epoch, issue_study_day, issued_at, attempt_id);

-- The first response is durable before a terminal aggregate review exists.
CREATE TABLE study_position_first_responses (
    event_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    decision_index BIGINT NOT NULL CHECK (decision_index >= 0),
    repertoire_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    position TEXT NOT NULL,
    owner_epoch TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL,
    UNIQUE (attempt_id, decision_index),
    UNIQUE (event_id, attempt_id, decision_index, repertoire_id, variant, position, owner_epoch),
    FOREIGN KEY (attempt_id, repertoire_id, variant, owner_epoch)
        REFERENCES study_position_issued_plans(attempt_id, repertoire_id, variant, owner_epoch),
    FOREIGN KEY (repertoire_id, variant, position)
        REFERENCES study_position_states(repertoire_id, variant, position)
);

CREATE TABLE study_position_grades (
    effect_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('study', 'game_lapse')),
    event_id TEXT,
    attempt_id TEXT,
    decision_index BIGINT,
    game_effect_key TEXT UNIQUE,
    game_evidence_json JSONB,
    effect_kind TEXT NOT NULL,
    repertoire_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    position TEXT NOT NULL,
    owner_epoch TEXT NOT NULL,
    expected_uci TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    accepted_study_day DATE NOT NULL,
    grade TEXT NOT NULL CHECK (grade IN ('good', 'again', 'observe')),
    request_digest TEXT NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL,
    fsrs_before_json TEXT,
    fsrs_after_json TEXT,
    version_before BIGINT,
    version_after BIGINT,
    UNIQUE (attempt_id, decision_index, effect_kind),
    FOREIGN KEY (event_id, attempt_id, decision_index, repertoire_id, variant, position, owner_epoch)
        REFERENCES study_position_first_responses(event_id, attempt_id, decision_index, repertoire_id, variant, position, owner_epoch),
    FOREIGN KEY (repertoire_id, owner_epoch) REFERENCES study_position_epochs(repertoire_id, epoch),
    FOREIGN KEY (repertoire_id, variant, position) REFERENCES study_position_states(repertoire_id, variant, position),
    CHECK ((source_kind = 'study' AND event_id IS NOT NULL AND attempt_id IS NOT NULL
            AND decision_index IS NOT NULL AND game_effect_key IS NULL AND game_evidence_json IS NULL)
        OR (source_kind = 'game_lapse' AND event_id IS NULL AND attempt_id IS NULL
            AND decision_index IS NULL AND game_effect_key IS NOT NULL AND game_evidence_json IS NOT NULL AND grade = 'again')),
    CHECK (grade = 'observe' OR (fsrs_before_json IS NOT NULL AND fsrs_after_json IS NOT NULL
        AND version_before IS NOT NULL AND version_after IS NOT NULL AND version_after = version_before + 1))
);
-- Deliberately excludes epoch and policy: positive credit is per position/day.
CREATE UNIQUE INDEX study_position_one_good_per_day ON study_position_grades
    (repertoire_id, variant, position, accepted_study_day) WHERE grade = 'good';

INSERT INTO tempo_schema_migrations(version) VALUES(57);
