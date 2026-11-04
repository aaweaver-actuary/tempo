CREATE TABLE content.final_generation_authority (
    singleton smallint PRIMARY KEY CHECK (singleton = 1),
    database_authority_id uuid UNIQUE NOT NULL
);

CREATE TABLE content.final_generation_reservations (
    reservation_id text PRIMARY KEY,
    source_id text NOT NULL REFERENCES content.sources(id),
    workspace_id text NOT NULL,
    transfer_id text NOT NULL,
    epoch bigint NOT NULL CHECK (epoch > 0),
    publication_id text NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    approved_operation_id text NOT NULL REFERENCES content.bridge_operations(id),
    approved_draft_digest text NOT NULL,
    request_digest char(64) NOT NULL,
    state text NOT NULL CHECK (state IN ('reserved','captured','accepted','aborted','recovery-held')),
    capture_id text,
    resolution_json jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, epoch), UNIQUE (source_id, transfer_id)
);
CREATE UNIQUE INDEX final_generation_one_active_source ON content.final_generation_reservations(source_id)
    WHERE state IN ('reserved','captured','recovery-held');

CREATE TABLE content.final_generation_captures (
    capture_id text PRIMARY KEY,
    reservation_id text UNIQUE NOT NULL REFERENCES content.final_generation_reservations(reservation_id),
    certificate_bytes bytea NOT NULL,
    export_bytes bytea NOT NULL,
    inventory_bytes bytea NOT NULL,
    proof_bytes bytea NOT NULL,
    certificate_digest char(64) NOT NULL,
    export_sha256 char(64) NOT NULL,
    inventory_sha256 char(64) NOT NULL,
    proof_sha256 char(64) NOT NULL,
    export_byte_length bigint NOT NULL CHECK (export_byte_length >= 0),
    captured_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION content.reject_final_capture_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'final generation capture is immutable';
END;
$$;
CREATE TRIGGER final_capture_immutable BEFORE UPDATE OR DELETE ON content.final_generation_captures
    FOR EACH ROW EXECUTE FUNCTION content.reject_final_capture_mutation();
