-- Application state belongs to the existing operation receipt, never to a preview.
CREATE TABLE prefix_transition_applications (
    operation_id TEXT PRIMARY KEY REFERENCES operation_receipts(operation_id),
    repertoire_id TEXT NOT NULL,
    plan_id TEXT NOT NULL, plan_json TEXT NOT NULL,
    graph_generation BIGINT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('staging','staged','publishing','complete','rejected','recovery_required')),
    staging_task_id TEXT, graph_task_id TEXT, integrity_task_id TEXT, integrity_generation BIGINT,
    queue_task_id TEXT, queue_generation BIGINT, queue_date TEXT,
    result_json TEXT, last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX prefix_transition_active_repertoire ON prefix_transition_applications(repertoire_id)
WHERE state NOT IN ('complete','rejected');
CREATE TABLE prefix_transition_card_fences (
    card_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES prefix_transition_applications(operation_id)
);
CREATE TABLE prefix_transition_repertoire_fences (
    repertoire_id TEXT PRIMARY KEY REFERENCES repertoires(id),
    operation_id TEXT NOT NULL REFERENCES prefix_transition_applications(operation_id)
);
ALTER TABLE opening_evidence_attempts ADD COLUMN retired_operation_id TEXT REFERENCES operation_receipts(operation_id);
ALTER TABLE study_attempts ADD COLUMN retired_operation_id TEXT REFERENCES operation_receipts(operation_id);

-- All structural producers, including ON CONFLICT and direct maintenance writes,
-- participate. The owner bypass is transaction-local and validated against its
-- persisted application. Schedules/reviews are deliberately outside this guard.
CREATE FUNCTION guard_prefix_transition_scope(scope_kind TEXT, scope_id TEXT) RETURNS void LANGUAGE plpgsql AS $$
DECLARE owning_operation TEXT;
BEGIN
    IF scope_id IS NULL THEN RETURN; END IF;
    IF scope_kind='card' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('tempo:card-edit:'||scope_id,0));
        SELECT fence.operation_id INTO owning_operation FROM prefix_transition_card_fences fence WHERE fence.card_id=scope_id;
    ELSE
        PERFORM pg_advisory_xact_lock(hashtextextended('tempo:opening-graph:'||scope_id,0));
        SELECT fence.operation_id INTO owning_operation FROM prefix_transition_repertoire_fences fence WHERE fence.repertoire_id=scope_id;
    END IF;
    IF owning_operation IS NOT NULL AND (owning_operation IS DISTINCT FROM current_setting('tempo.prefix_transition_operation',true) OR NOT EXISTS(SELECT 1 FROM prefix_transition_applications application WHERE application.operation_id=owning_operation AND application.state IN ('staging','staged','publishing','recovery_required'))) THEN
        RAISE EXCEPTION USING ERRCODE='P0080', MESSAGE='Prefix transition '||owning_operation||' is in progress; retry this structural edit after publication or recovery.';
    END IF;
END $$;
CREATE FUNCTION guard_prefix_transition_write() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE before_row JSONB; after_row JSONB; affected_id TEXT; affected_repertoire TEXT;
BEGIN
    IF TG_OP<>'INSERT' THEN before_row=to_jsonb(OLD); END IF;
    IF TG_OP<>'DELETE' THEN after_row=to_jsonb(NEW); END IF;
    IF TG_TABLE_NAME='cards' THEN
        IF TG_OP='UPDATE' AND jsonb_build_array(before_row->'id',before_row->'repertoire_id',before_row->'kind',before_row->'start_fen',before_row->'moves_json',before_row->'trained_color',before_row->'revision',before_row->'archived',before_row->'superseded_by',before_row->'content_type',before_row->'canonical_route_source',before_row->'source_fen',before_row->'study_exercise_id')
            IS NOT DISTINCT FROM jsonb_build_array(after_row->'id',after_row->'repertoire_id',after_row->'kind',after_row->'start_fen',after_row->'moves_json',after_row->'trained_color',after_row->'revision',after_row->'archived',after_row->'superseded_by',after_row->'content_type',after_row->'canonical_route_source',after_row->'source_fen',after_row->'study_exercise_id') THEN RETURN NEW; END IF;
        PERFORM guard_prefix_transition_scope('repertoire',before_row->>'repertoire_id');
        PERFORM guard_prefix_transition_scope('repertoire',after_row->>'repertoire_id');
        PERFORM guard_prefix_transition_scope('card',before_row->>'id');
        PERFORM guard_prefix_transition_scope('card',after_row->>'id');
    ELSIF TG_TABLE_NAME='repertoire_line_training_depths' THEN
        FOR affected_repertoire IN SELECT DISTINCT repertoire_id FROM repertoire_lines WHERE id IN (before_row->>'line_id',after_row->>'line_id') ORDER BY repertoire_id LOOP
            PERFORM guard_prefix_transition_scope('repertoire',affected_repertoire);
        END LOOP;
    ELSIF TG_TABLE_NAME='background_tasks' THEN
        IF (after_row->>'kind'='opening_graph_rebuild' OR before_row->>'kind'='opening_graph_rebuild') AND
            (TG_OP<>'UPDATE' OR ROW(before_row->>'kind',before_row->>'generation',before_row->>'deduplication_key') IS DISTINCT FROM ROW(after_row->>'kind',after_row->>'generation',after_row->>'deduplication_key')) THEN
            IF before_row->>'kind'='opening_graph_rebuild' THEN PERFORM guard_prefix_transition_scope('repertoire',before_row->>'deduplication_key'); END IF;
            IF after_row->>'kind'='opening_graph_rebuild' THEN PERFORM guard_prefix_transition_scope('repertoire',after_row->>'deduplication_key'); END IF;
        END IF;
    ELSIF TG_TABLE_NAME='prefix_splits' THEN
        FOR affected_id IN SELECT DISTINCT identity FROM unnest(ARRAY[before_row->>'source_card_id',after_row->>'source_card_id',before_row->>'shortened_card_id',after_row->>'shortened_card_id',before_row->>'continuation_card_id',after_row->>'continuation_card_id']) identity WHERE identity IS NOT NULL ORDER BY identity LOOP
            PERFORM guard_prefix_transition_scope('card',affected_id);
        END LOOP;
    ELSE
        affected_repertoire=CASE WHEN TG_TABLE_NAME='repertoires' THEN COALESCE(after_row->>'id',before_row->>'id') ELSE COALESCE(after_row->>'repertoire_id',before_row->>'repertoire_id') END;
        IF TG_TABLE_NAME='repertoires' AND TG_OP='UPDATE' AND
            ROW(before_row->>'canonical_prefix_moves_json',before_row->>'canonical_prefix_revision',before_row->>'scope_source_revision',before_row->>'is_main') IS NOT DISTINCT FROM
            ROW(after_row->>'canonical_prefix_moves_json',after_row->>'canonical_prefix_revision',after_row->>'scope_source_revision',after_row->>'is_main') THEN RETURN NEW; END IF;
        PERFORM guard_prefix_transition_scope('repertoire',CASE WHEN TG_TABLE_NAME='repertoires' THEN before_row->>'id' ELSE before_row->>'repertoire_id' END);
        PERFORM guard_prefix_transition_scope('repertoire',affected_repertoire);
        IF TG_TABLE_NAME='repertoire_cards' THEN
            PERFORM guard_prefix_transition_scope('card',before_row->>'card_id');
            PERFORM guard_prefix_transition_scope('card',COALESCE(after_row->>'card_id',before_row->>'card_id'));
        END IF;
        IF TG_TABLE_NAME='opening_graph_steps' THEN
            PERFORM guard_prefix_transition_scope('card',before_row->>'card_id');
            PERFORM guard_prefix_transition_scope('card',after_row->>'card_id');
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
CREATE TRIGGER prefix_transition_card_write BEFORE INSERT OR UPDATE OR DELETE ON cards FOR EACH ROW EXECUTE FUNCTION guard_prefix_transition_write();
CREATE TRIGGER prefix_transition_line_write BEFORE INSERT OR UPDATE OR DELETE ON repertoire_lines FOR EACH ROW EXECUTE FUNCTION guard_prefix_transition_write();
CREATE TRIGGER prefix_transition_depth_write BEFORE INSERT OR UPDATE OR DELETE ON repertoire_line_training_depths FOR EACH ROW EXECUTE FUNCTION guard_prefix_transition_write();
CREATE TRIGGER prefix_transition_membership_write BEFORE INSERT OR UPDATE OR DELETE ON repertoire_cards FOR EACH ROW EXECUTE FUNCTION guard_prefix_transition_write();
CREATE TRIGGER prefix_transition_repertoire_write BEFORE UPDATE OR DELETE ON repertoires FOR EACH ROW EXECUTE FUNCTION guard_prefix_transition_write();
CREATE TRIGGER prefix_transition_graph_request BEFORE INSERT OR UPDATE ON background_tasks FOR EACH ROW EXECUTE FUNCTION guard_prefix_transition_write();
CREATE TRIGGER prefix_transition_publication_write BEFORE INSERT OR UPDATE OR DELETE ON opening_graph_publications FOR EACH ROW EXECUTE FUNCTION guard_prefix_transition_write();
CREATE TRIGGER prefix_transition_split_write BEFORE INSERT OR UPDATE OR DELETE ON prefix_splits FOR EACH ROW EXECUTE FUNCTION guard_prefix_transition_write();
CREATE TRIGGER prefix_transition_steps_write BEFORE INSERT OR UPDATE OR DELETE ON opening_graph_steps FOR EACH ROW EXECUTE FUNCTION guard_prefix_transition_write();
INSERT INTO tempo_schema_migrations(version) VALUES(37);
