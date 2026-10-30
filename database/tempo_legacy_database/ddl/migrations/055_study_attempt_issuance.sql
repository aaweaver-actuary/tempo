-- Actual first head authority, separate from queue admission/prefetch.
-- Existing issued attempts deliberately retain unknown sequence/time: neither
-- migration nor a later reload can turn them into post-reset remediation.
CREATE SEQUENCE study_attempt_issuance_sequence;
ALTER TABLE queue_attempt_origins
    ADD COLUMN issuance_sequence BIGINT,
    ADD COLUMN issued_at TIMESTAMPTZ,
    ADD CONSTRAINT study_attempt_issuance_facts CHECK (
        (issuance_sequence IS NULL AND issued_at IS NULL)
        OR (issued_as_head=1 AND issuance_sequence>0 AND issued_at IS NOT NULL)
    );
CREATE UNIQUE INDEX study_attempt_issuance_order
    ON queue_attempt_origins(issuance_sequence) WHERE issuance_sequence IS NOT NULL;
CREATE INDEX study_attempt_issuance_time
    ON queue_attempt_origins(issued_at,issuance_sequence) WHERE issued_at IS NOT NULL;

INSERT INTO tempo_schema_migrations(version) VALUES(55);
