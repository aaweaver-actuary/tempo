-- Module-local input. A registers ordering; no global migration number or backfill.
CREATE TABLE remediation_deliveries (
    workspace_id text NOT NULL, delivery_id text NOT NULL, payload_hash text NOT NULL,
    response_json jsonb NOT NULL, PRIMARY KEY(workspace_id, delivery_id)
);
CREATE TABLE remediation_evidence (
    workspace_id text NOT NULL, evidence_id text NOT NULL, evidence_revision bigint NOT NULL,
    game_id text NOT NULL, game_revision bigint NOT NULL, payload_hash text NOT NULL,
    learning_revision bigint NOT NULL, PRIMARY KEY(workspace_id,evidence_id,evidence_revision)
);
CREATE TABLE remediation_source_heads (
    workspace_id text NOT NULL, evidence_id text NOT NULL, game_id text NOT NULL,
    evidence_revision bigint NOT NULL, game_revision bigint NOT NULL,
    PRIMARY KEY(workspace_id,evidence_id)
);
CREATE TABLE remediation_game_heads (
    workspace_id text NOT NULL, game_id text NOT NULL, game_revision bigint NOT NULL,
    PRIMARY KEY(workspace_id,game_id)
);
CREATE TABLE remediation_workspaces (
    workspace_id text PRIMARY KEY, revision bigint NOT NULL DEFAULT 1
);
CREATE TABLE remediation_cards (
    workspace_id text NOT NULL, card_id text NOT NULL, sequence bigint NOT NULL DEFAULT 0,
    work_state text NOT NULL DEFAULT 'pending' CHECK(work_state IN ('pending','deferred','admitted')),
    reason text, queue_entry_id bigint, PRIMARY KEY(workspace_id,card_id)
);
CREATE TABLE remediation_incident_versions (
    workspace_id text NOT NULL, incident_id text NOT NULL, logical_hash text NOT NULL,
    card_id text NOT NULL, work_sequence bigint NOT NULL, accepted_at timestamptz NOT NULL,
    PRIMARY KEY(workspace_id,incident_id,logical_hash), UNIQUE(workspace_id,card_id,work_sequence)
);
CREATE TABLE remediation_contributions (
    workspace_id text NOT NULL, obligation_id text NOT NULL, obligation_revision bigint NOT NULL,
    evidence_id text NOT NULL, evidence_revision bigint NOT NULL, game_id text NOT NULL,
    incident_id text NOT NULL, logical_hash text NOT NULL, card_id text NOT NULL,
    card_revision bigint NOT NULL, publication_id text NOT NULL, publication_generation bigint NOT NULL,
    kind text NOT NULL CHECK(kind IN ('real_game_miss','observed_encounter')),
    occurred_at timestamptz, payload_json text NOT NULL, game_revision bigint NOT NULL,
    PRIMARY KEY(workspace_id,obligation_id,obligation_revision),
    FOREIGN KEY(workspace_id,incident_id,logical_hash)
      REFERENCES remediation_incident_versions(workspace_id,incident_id,logical_hash)
);
CREATE INDEX remediation_contributions_card ON remediation_contributions(workspace_id,card_id,obligation_id,obligation_revision);
CREATE TABLE remediation_obligation_heads (
    workspace_id text NOT NULL, obligation_id text NOT NULL, obligation_revision bigint NOT NULL,
    evidence_id text NOT NULL, game_id text NOT NULL, incident_id text NOT NULL,
    card_id text NOT NULL, active boolean NOT NULL DEFAULT true,
    PRIMARY KEY(workspace_id,obligation_id),
    FOREIGN KEY(workspace_id,obligation_id,obligation_revision)
      REFERENCES remediation_contributions(workspace_id,obligation_id,obligation_revision)
);
CREATE INDEX remediation_heads_card ON remediation_obligation_heads(workspace_id,card_id) WHERE active;
CREATE TABLE remediation_attempts (
    workspace_id text NOT NULL, attempt_id text NOT NULL, card_id text NOT NULL,
    card_revision bigint NOT NULL, publication_id text NOT NULL, publication_generation bigint NOT NULL,
    issued_at timestamptz NOT NULL, frontier_sequence bigint NOT NULL,
    PRIMARY KEY(workspace_id,attempt_id)
);
CREATE TABLE remediation_review_receipts (
    workspace_id text NOT NULL, review_id text NOT NULL, payload_json text NOT NULL,
    settled boolean NOT NULL, PRIMARY KEY(workspace_id,review_id)
);
CREATE INDEX remediation_attempts_frontier ON remediation_attempts
    (workspace_id,card_id,card_revision,frontier_sequence DESC);
CREATE TABLE remediation_settlements (
    workspace_id text NOT NULL, review_id text NOT NULL, attempt_id text NOT NULL,
    card_id text NOT NULL, reviewed_at timestamptz NOT NULL,
    invalidated boolean NOT NULL DEFAULT false, invalidation_reason text,
    PRIMARY KEY(workspace_id,review_id),
    FOREIGN KEY(workspace_id,attempt_id) REFERENCES remediation_attempts(workspace_id,attempt_id)
);
CREATE INDEX remediation_settlements_card ON remediation_settlements(workspace_id,card_id,reviewed_at DESC) WHERE NOT invalidated;
CREATE INDEX remediation_settlements_attempt ON remediation_settlements(workspace_id,attempt_id) WHERE NOT invalidated;
