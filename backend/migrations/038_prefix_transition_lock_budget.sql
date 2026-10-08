-- Keep 037 immutable: upgrade existing application/fence rows without rewriting history.
-- The shared/exclusive reservation barrier serializes fence installation and all
-- structural/queue writes. Per-identity advisory locks are redundant under it.
CREATE OR REPLACE FUNCTION guard_prefix_transition_scope(scope_kind TEXT, scope_id TEXT) RETURNS void LANGUAGE plpgsql AS $$
DECLARE owning_operation TEXT;
BEGIN
    IF scope_id IS NULL THEN RETURN; END IF;
    -- One shared lock per writing transaction closes the fence-installation race
    -- without allocating a lock for every unfenced identity in a bulk import.
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0));
    IF scope_kind='card' THEN
        SELECT fence.operation_id INTO owning_operation FROM prefix_transition_card_fences fence WHERE fence.card_id=scope_id;
    ELSE
        SELECT fence.operation_id INTO owning_operation FROM prefix_transition_repertoire_fences fence WHERE fence.repertoire_id=scope_id;
    END IF;
    IF owning_operation IS NOT NULL AND (owning_operation IS DISTINCT FROM current_setting('tempo.prefix_transition_operation',true) OR NOT EXISTS(SELECT 1 FROM prefix_transition_applications application WHERE application.operation_id=owning_operation AND application.state IN ('staging','staged','publishing','recovery_required'))) THEN
        RAISE EXCEPTION USING ERRCODE='P0080', MESSAGE='Prefix transition '||owning_operation||' is in progress; retry this structural edit after publication or recovery.';
    END IF;
END $$;

-- Queue participation is a statement boundary, before row traversal. It does
-- not prohibit study while an application is staged/publishing: only its short
-- acceptance/activation transaction takes the exclusive counterpart.
CREATE FUNCTION reserve_prefix_transition_queue_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0));
    RETURN NULL;
END $$;
CREATE TRIGGER prefix_transition_queue_write BEFORE INSERT OR UPDATE OR DELETE ON daily_queue
FOR EACH STATEMENT EXECUTE FUNCTION reserve_prefix_transition_queue_write();
CREATE TRIGGER prefix_transition_queue_day_write BEFORE INSERT OR UPDATE OR DELETE ON daily_queue_days
FOR EACH STATEMENT EXECUTE FUNCTION reserve_prefix_transition_queue_write();
CREATE TRIGGER prefix_transition_queue_projection_write BEFORE INSERT OR UPDATE OR DELETE ON queue_projections
FOR EACH STATEMENT EXECUTE FUNCTION reserve_prefix_transition_queue_write();
INSERT INTO tempo_schema_migrations(version) VALUES(38);
