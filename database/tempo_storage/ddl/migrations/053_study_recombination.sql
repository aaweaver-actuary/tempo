-- Study-owned presentation regrouping; source moves/history/obligation identities stay intact.

CREATE TABLE study_recombination_streaks (
    presentation_id text NOT NULL, revision bigint NOT NULL CHECK (revision > 0),
    evidence_epoch text NOT NULL, state_json jsonb NOT NULL,
    PRIMARY KEY (presentation_id, revision, evidence_epoch)
);
CREATE TABLE study_recombination_events (
    event_id text PRIMARY KEY, event_json jsonb NOT NULL
);
CREATE TABLE study_recombination_completions (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id text NOT NULL UNIQUE, review_id text NOT NULL UNIQUE,
    presentation_id text NOT NULL, revision bigint NOT NULL, evidence_epoch text NOT NULL,
    completed_at timestamptz NOT NULL, event_json jsonb NOT NULL
);
CREATE INDEX study_recombination_recent ON study_recombination_completions
    (presentation_id, revision, evidence_epoch, completed_at DESC, sequence DESC);
CREATE TABLE study_recombination_changes (
    command_id text PRIMARY KEY, digest text NOT NULL,
    change_json jsonb NOT NULL, receipt_json jsonb NOT NULL
);
CREATE TABLE study_recombination_presentations (
    presentation_id text PRIMARY KEY,
    command_id text NOT NULL REFERENCES study_recombination_changes(command_id),
    result_json jsonb NOT NULL
);
CREATE TABLE study_recombination_current (
    route_id text NOT NULL, decision_id text NOT NULL,
    presentation_id text NOT NULL REFERENCES study_recombination_presentations(presentation_id),
    PRIMARY KEY (route_id, decision_id)
);

CREATE INDEX study_recombination_route_changes ON study_recombination_changes ((change_json->>'route_id'));
CREATE INDEX study_recombination_route_presentations ON study_recombination_presentations ((result_json->'presentation'->>'route_id'));
CREATE INDEX study_recombination_decision_target ON study_recombination_current(decision_id);
CREATE INDEX study_game_feedback_decision_owed ON study_game_feedback(decision_id) WHERE status IN ('pending','applied');
INSERT INTO tempo_schema_migrations(version) VALUES(53);
