-- Review candidate: twelve exclusively retired stores; all shared state remains.
-- RESTRICT aborts the transaction if an unreviewed dependency remains.
-- Stopped-writer preflight: the runner provides the verified approved selection
-- as a parameterized transaction-local setting, never as SQL text or argv keys.
SET LOCAL TIME ZONE 'UTC';
SET LOCAL DateStyle = 'ISO, YMD';
SET LOCAL extra_float_digits=3;
SET LOCAL join_collapse_limit=1;
SET LOCAL jit=off;
SET LOCAL statement_timeout='30s';
DO $$ DECLARE manifest jsonb; actual_count bigint; actual_digest text;
BEGIN
 IF NOT (
-- BEGIN CUT62 DEFENSE FK LEDGER
WITH expected(target_table,source_schema,source_table,source_column,delete_rule) AS (VALUES
('cards','public','cards','unlock_after_card_id','n'),
('cards','public','daily_queue','card_id','c'),
('cards','public','defense_attempts','card_id','c'),
('cards','public','gameplay_card_priorities','card_id','c'),
('cards','public','opening_card_schedule_seeds','card_id','c'),
('cards','public','opening_card_schedule_seeds','source_card_id','c'),
('cards','public','opening_graph_legacy_mappings','decision_card_id','c'),
('cards','public','opening_graph_legacy_mappings','legacy_card_id','c'),
('cards','public','prefix_splits','continuation_card_id','c'),
('cards','public','prefix_splits','shortened_card_id','c'),
('cards','public','prefix_splits','source_card_id','c'),
('cards','public','repertoire_card_introduction_priorities','card_id','c'),
('cards','public','repertoire_card_priority_generations','card_id','c'),
('cards','public','repertoire_cards','card_id','c'),
('cards','public','repertoire_decision_events_legacy','card_id','n'),
('cards','public','repertoire_decision_events_staged','card_id','n'),
('cards','public','repertoire_integrity_card_blocks','card_id','c'),
('cards','public','repertoire_opportunities','card_id','n'),
('cards','public','repertoire_priority_prepared_rows','card_id','c'),
('cards','public','reviews','card_id','c'),
('cards','public','study_attempts','card_id','a'),
('cards','public','tactic_captures','card_id','c'),
('cards','public','teaching_states','card_id','c'),
('cards','public','threat_training_candidates','card_id','n'),
('daily_queue','public','defense_recognition_submissions','queue_entry_id','c'),
('daily_queue','public','study_attempts','queue_entry_id','a')
), actual AS (
 SELECT target.relname::text,n.nspname::text,source.relname::text,
        CASE WHEN cardinality(con.conkey)=1 AND cardinality(con.confkey)=1 AND target_col.attname='id' THEN source_col.attname::text ELSE '<unexpected-composite>' END,
        con.confdeltype::text
 FROM pg_constraint con JOIN pg_class target ON target.oid=con.confrelid
 JOIN pg_class source ON source.oid=con.conrelid JOIN pg_namespace n ON n.oid=source.relnamespace
 LEFT JOIN pg_attribute source_col ON source_col.attrelid=source.oid AND source_col.attnum=con.conkey[1]
 LEFT JOIN pg_attribute target_col ON target_col.attrelid=target.oid AND target_col.attnum=con.confkey[1]
 WHERE con.contype='f' AND con.confrelid IN ('public.cards'::regclass,'public.daily_queue'::regclass)
)
SELECT NOT EXISTS ((SELECT * FROM actual EXCEPT ALL SELECT * FROM expected) UNION ALL (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual))
-- END CUT62 DEFENSE FK LEDGER
 ) THEN RAISE EXCEPTION 'cut62 Defense foreign-key ledger changed'; END IF;
 IF NOT (
-- BEGIN CUT62 DEFENSE TRIGGER LEDGER
WITH expected(table_name,trigger_name,enabled,trigger_type,function_name) AS (VALUES
('cards','canonical_card_delete_source','O',9,'advance_canonical_prefix_source'),
('cards','canonical_card_insert_source','O',5,'advance_canonical_prefix_source'),
('cards','canonical_card_promote','O',19,'promote_canonical_card_source'),
('cards','canonical_card_source','O',17,'advance_canonical_prefix_source'),
('cards','deleted_card_recreation_guard','O',23,'prevent_deleted_card_recreation'),
('cards','frozen_guard_cards','O',28,'frozen_invalidate_pending'),
('cards','opening_evidence_01_saved_presentation','O',21,'opening_evidence_capture_presentation'),
('cards','opening_evidence_edited_scope','O',17,'opening_evidence_capture_edited_scope'),
('cards','prefix_transition_card_write','O',31,'guard_prefix_transition_write'),
('cards','priority_source_cards','O',29,'priority_card_source_changed'),
('cards','queue_attempt_origin_revision','O',17,'retain_queue_attempt_revision'),
('cards','study_position_guard_legacy_fsrs_id','O',23,'study_position_guard_legacy_fsrs_id'),
('daily_queue','frozen_guard_daily_queue','O',28,'frozen_invalidate_pending'),
('daily_queue','opening_evidence_queue_scope','O',21,'opening_evidence_capture_queue_context'),
('daily_queue','prefix_transition_queue_write','O',30,'reserve_prefix_transition_queue_write'),
('daily_queue','queue_attempt_origin_delete','O',11,'retain_queue_attempt_origin'),
('daily_queue','queue_attempt_origin_insert','O',5,'retain_queue_attempt_origin'),
('daily_queue','queue_attempt_origin_update','O',17,'retain_queue_attempt_origin'),
('queue_attempt_origins','frozen_guard_queue_attempt_origins','O',28,'frozen_invalidate_pending'),
('queue_attempt_origins','queue_attempt_abandoned_origin_immutable','O',27,'protect_abandoned_queue_origin')
), actual AS (
 SELECT c.relname::text,t.tgname::text,t.tgenabled::text,t.tgtype::int,p.proname::text
 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
 WHERE n.nspname='public' AND NOT t.tgisinternal AND c.relname IN ('cards','daily_queue','queue_attempt_origins','threat_training_candidates','priority_repertoire_source_epochs','frozen_opening_runs')
)
SELECT NOT EXISTS ((SELECT * FROM actual EXCEPT ALL SELECT * FROM expected) UNION ALL (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual))
-- END CUT62 DEFENSE TRIGGER LEDGER
 ) THEN RAISE EXCEPTION 'cut62 Defense trigger ledger changed'; END IF;
 manifest := current_setting('tempo.cut62_defense_manifest', true)::jsonb;
 IF jsonb_typeof(manifest) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'Missing or invalid approved Defense selection'; END IF;
 IF (SELECT count(*) FROM jsonb_object_keys(manifest))<>5 OR jsonb_typeof(manifest->'version') IS DISTINCT FROM 'number' OR manifest->>'version' IS DISTINCT FROM '1' OR jsonb_typeof(manifest->'target') IS DISTINCT FROM 'string' OR COALESCE(manifest->>'target','')='' OR manifest->>'cohort' IS DISTINCT FROM 'defense-no-activity' OR jsonb_typeof(manifest->'keys') IS DISTINCT FROM 'array' OR jsonb_typeof(manifest->'expected') IS DISTINCT FROM 'object' THEN
  RAISE EXCEPTION 'Missing or invalid approved Defense selection';
 END IF;
 IF jsonb_array_length(manifest->'keys')>11 OR (SELECT count(*) FROM jsonb_object_keys(manifest->'expected'))<>2 OR jsonb_typeof(manifest->'expected'->'count') IS DISTINCT FROM 'number' OR COALESCE(manifest->'expected'->>'count','') !~ '^(0|[1-9][0-9]*)$' OR jsonb_typeof(manifest->'expected'->'reference_digest') IS DISTINCT FROM 'string' OR COALESCE(manifest->'expected'->>'reference_digest','') !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'Missing or invalid approved Defense selection';
 END IF;
 IF EXISTS (SELECT 1 FROM jsonb_array_elements(manifest->'keys') value WHERE CASE WHEN jsonb_typeof(value)='array' THEN jsonb_array_length(value)<>2 OR jsonb_typeof(value->0) IS DISTINCT FROM 'string' OR jsonb_typeof(value->1) IS DISTINCT FROM 'string' OR COALESCE(value->>0,'')='' OR COALESCE(value->>1,'')='' ELSE true END) THEN
  RAISE EXCEPTION 'Missing or invalid approved Defense selection';
 END IF;
 IF EXISTS (SELECT 1 FROM (SELECT value->>0 AS card_id,value->>1 AS candidate_id,lag(value->>0) OVER (ORDER BY ord) AS prior_card,lag(value->>1) OVER (ORDER BY ord) AS prior_candidate FROM jsonb_array_elements(manifest->'keys') WITH ORDINALITY AS x(value,ord)) ordered WHERE prior_card IS NOT NULL AND (card_id COLLATE "C",candidate_id COLLATE "C") <= (prior_card COLLATE "C",prior_candidate COLLATE "C")) THEN
  RAISE EXCEPTION 'Missing or invalid approved Defense selection';
 END IF;
 SELECT selected_count,reference_digest INTO actual_count,actual_digest FROM (
-- BEGIN CUT62 REFERENCE DIGEST

        WITH approved AS MATERIALIZED (
          SELECT value->>0 AS card_id, value->>1 AS candidate_id
          FROM jsonb_array_elements((current_setting('tempo.cut62_defense_manifest', true)::jsonb->'keys')) value
        ), eligible AS MATERIALIZED (
-- BEGIN CUT62 DEFENSE SELECTOR
WITH typed_receipts AS MATERIALIZED (
 SELECT CASE WHEN pg_input_is_valid(payload_json,'jsonb') THEN payload_json::jsonb END AS payload,
        CASE WHEN pg_input_is_valid(response_json,'jsonb') THEN response_json::jsonb END AS response
 FROM public.operation_receipts WHERE command_name IN
 ('cards.review','cards.review.reconcile','queue.attempt_failed','queue.bury','queue.issue_head','opening_evidence.checkpoint')
)
SELECT c.id AS card_id, t.id AS candidate_id, c.repertoire_id
FROM public.cards c JOIN public.threat_training_candidates t ON t.card_id=c.id
WHERE c.content_type='defense'
AND c.repertoire_id IS NOT NULL
AND EXISTS (SELECT 1 FROM public.priority_repertoire_source_epochs e WHERE e.repertoire_id=c.repertoire_id)
AND NOT EXISTS (SELECT 1 FROM public.threat_training_candidates other WHERE other.card_id=c.id AND other.id<>t.id)
AND EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id)
AND EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.content_type='defense')
AND NOT EXISTS (SELECT 1 FROM public.defense_attempts a WHERE a.card_id=c.id OR a.candidate_id=t.id)
AND NOT EXISTS (SELECT 1 FROM public.defense_recognition_submissions r WHERE r.candidate_id=t.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND (q.status NOT IN ('queued','blocked') OR q.review_result_json IS NOT NULL))
AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.review_result_json IS NOT NULL)
AND NOT EXISTS (SELECT 1 FROM typed_receipts r WHERE
 r.payload->>'card_id'=c.id OR r.payload#>>'{review,prepared_next,card_id}'=c.id OR r.payload#>>'{checkpoint,manifest,card_id}'=c.id OR r.response->>'card_id'=c.id OR
 EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND q.id::text IN
 (r.payload->>'entry_id',r.payload->>'queue_entry_id',r.payload#>>'{review,queue_entry_id}',r.payload#>>'{review,prepared_next,queue_entry_id}',r.payload#>>'{checkpoint,origin_queue_entry_id}',r.payload#>>'{checkpoint,queue_entry_id}',r.response->>'entry_id',r.response->>'queue_entry_id')))
AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.issuance_sequence IS NOT NULL)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND ((q.admission_source IS NOT NULL AND q.admission_source<>'defense:' || t.id) OR NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.queue_entry_id=q.id AND o.card_id=c.id AND o.revision=c.revision AND o.queue_date=q.queue_date AND o.cycle=q.cycle AND o.content_type='defense' AND o.start_fen=c.start_fen AND o.moves_json=c.moves_json)))
AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND (o.revision<>c.revision OR o.content_type<>'defense' OR o.start_fen<>c.start_fen OR o.moves_json<>c.moves_json OR NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.id=o.queue_entry_id AND q.card_id=c.id AND q.queue_date=o.queue_date AND q.cycle=o.cycle)))
AND NOT EXISTS (SELECT 1 FROM public.cards r WHERE r.unlock_after_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.cards r WHERE r.superseded_by=c.id)
AND NOT EXISTS (SELECT 1 FROM public.gameplay_card_priorities r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_card_schedule_seeds r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_card_schedule_seeds r WHERE r.source_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_graph_legacy_mappings r WHERE r.decision_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_graph_legacy_mappings r WHERE r.legacy_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.prefix_splits r WHERE r.continuation_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.prefix_splits r WHERE r.shortened_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.prefix_splits r WHERE r.source_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_card_introduction_priorities r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_card_priority_generations r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_cards r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_decision_events_legacy r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_decision_events_staged r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_integrity_card_blocks r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_opportunities r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_priority_prepared_rows r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.reviews r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_attempts r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.tactic_captures r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.teaching_states r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.card_revisions r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.queue_projection_diagnostics r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.endgame_templates r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.tactic_progress r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.game_findings r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.game_repertoire_matches_legacy r WHERE r.deviation_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_graph_steps r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_graph_steps r WHERE r.parent_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_opportunities r WHERE r.admitted_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.discovery_admission_intents r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.game_repertoire_matches_staged r WHERE r.deviation_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.review_attempt_receipts r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_segmentation_occurrences r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_segmentation_sources r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_evidence_presentations r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_evidence_attempts r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.deleted_cards r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.prefix_transition_card_fences r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.frozen_opening_ranks r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.frozen_opening_queue_changes r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_cards r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_incident_versions r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_contributions r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_obligation_heads r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_attempts r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_settlements r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_game_feedback r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_conflict_holds r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_conflict_queue_holds r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_position_states r WHERE r.donor_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_position_seed_outcomes r WHERE r.donor_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_position_issued_plans r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_abandonments r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.defense_attempts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.defense_recognition_submissions r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.frozen_opening_queue_changes r ON r.queue_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.opening_evidence_attempts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.opening_evidence_queue_contexts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.queue_attempt_abandonments r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.remediation_cards r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.review_attempt_receipts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.study_attempts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.study_conflict_queue_holds r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.study_position_issued_plans r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
-- END CUT62 DEFENSE SELECTOR
), chosen AS MATERIALIZED (
          SELECT e.* FROM eligible e JOIN approved a USING(card_id,candidate_id)
        ), evidence AS (
          SELECT jsonb_build_object(
            'cards', COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id) FROM public.cards c JOIN chosen s ON s.card_id=c.id),'[]'::jsonb),
            'candidates', COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM public.threat_training_candidates t JOIN chosen s ON s.candidate_id=t.id),'[]'::jsonb),
            'queues', COALESCE((SELECT jsonb_agg(to_jsonb(q) ORDER BY q.id) FROM public.daily_queue q JOIN chosen s ON s.card_id=q.card_id),'[]'::jsonb),
            'origins', COALESCE((SELECT jsonb_agg(to_jsonb(o) ORDER BY o.queue_entry_id,o.card_id,o.revision) FROM public.queue_attempt_origins o JOIN chosen s ON s.card_id=o.card_id),'[]'::jsonb),
            'request_links', COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.candidate_id,r.request_id,r.role) FROM public.threat_candidate_requests r JOIN chosen s ON s.candidate_id=r.candidate_id),'[]'::jsonb),
            'requests', COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id) FROM public.threat_analysis_requests r WHERE EXISTS (SELECT 1 FROM public.threat_candidate_requests l JOIN chosen s ON s.candidate_id=l.candidate_id WHERE l.request_id=r.id)),'[]'::jsonb),
            'games', COALESCE((SELECT jsonb_agg(to_jsonb(g) ORDER BY g.id) FROM public.imported_games g WHERE EXISTS (SELECT 1 FROM public.threat_training_candidates t JOIN chosen s ON s.candidate_id=t.id WHERE t.game_id=g.id)),'[]'::jsonb),
            'findings', COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY f.id) FROM public.game_findings f WHERE EXISTS (SELECT 1 FROM public.threat_training_candidates t JOIN chosen s ON s.candidate_id=t.id WHERE t.finding_id=f.id)),'[]'::jsonb),
            'epochs', COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.repertoire_id) FROM public.priority_repertoire_source_epochs e WHERE EXISTS (SELECT 1 FROM chosen s WHERE s.repertoire_id=e.repertoire_id)),'[]'::jsonb)
          ) AS value
        ) SELECT (SELECT count(*) FROM chosen) AS selected_count, encode(sha256(convert_to(value::text,'UTF8')),'hex') AS reference_digest FROM evidence

-- END CUT62 REFERENCE DIGEST
 ) reference_evidence;
 IF actual_count<>jsonb_array_length(manifest->'keys') OR actual_count IS DISTINCT FROM (manifest->'expected'->>'count')::bigint OR actual_digest IS DISTINCT FROM manifest->'expected'->>'reference_digest' THEN
  RAISE EXCEPTION 'Approved Defense reference evidence changed';
 END IF;
END $$;
CREATE TEMP TABLE cut62_defense_selected ON COMMIT DROP AS
WITH eligible AS MATERIALIZED (
-- BEGIN CUT62 DEFENSE SELECTOR
WITH typed_receipts AS MATERIALIZED (
 SELECT CASE WHEN pg_input_is_valid(payload_json,'jsonb') THEN payload_json::jsonb END AS payload,
        CASE WHEN pg_input_is_valid(response_json,'jsonb') THEN response_json::jsonb END AS response
 FROM public.operation_receipts WHERE command_name IN
 ('cards.review','cards.review.reconcile','queue.attempt_failed','queue.bury','queue.issue_head','opening_evidence.checkpoint')
)
SELECT c.id AS card_id, t.id AS candidate_id, c.repertoire_id
FROM public.cards c JOIN public.threat_training_candidates t ON t.card_id=c.id
WHERE c.content_type='defense'
AND c.repertoire_id IS NOT NULL
AND EXISTS (SELECT 1 FROM public.priority_repertoire_source_epochs e WHERE e.repertoire_id=c.repertoire_id)
AND NOT EXISTS (SELECT 1 FROM public.threat_training_candidates other WHERE other.card_id=c.id AND other.id<>t.id)
AND EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id)
AND EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.content_type='defense')
AND NOT EXISTS (SELECT 1 FROM public.defense_attempts a WHERE a.card_id=c.id OR a.candidate_id=t.id)
AND NOT EXISTS (SELECT 1 FROM public.defense_recognition_submissions r WHERE r.candidate_id=t.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND (q.status NOT IN ('queued','blocked') OR q.review_result_json IS NOT NULL))
AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.review_result_json IS NOT NULL)
AND NOT EXISTS (SELECT 1 FROM typed_receipts r WHERE
 r.payload->>'card_id'=c.id OR r.payload#>>'{review,prepared_next,card_id}'=c.id OR r.payload#>>'{checkpoint,manifest,card_id}'=c.id OR r.response->>'card_id'=c.id OR
 EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND q.id::text IN
 (r.payload->>'entry_id',r.payload->>'queue_entry_id',r.payload#>>'{review,queue_entry_id}',r.payload#>>'{review,prepared_next,queue_entry_id}',r.payload#>>'{checkpoint,origin_queue_entry_id}',r.payload#>>'{checkpoint,queue_entry_id}',r.response->>'entry_id',r.response->>'queue_entry_id')))
AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.issuance_sequence IS NOT NULL)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND ((q.admission_source IS NOT NULL AND q.admission_source<>'defense:' || t.id) OR NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.queue_entry_id=q.id AND o.card_id=c.id AND o.revision=c.revision AND o.queue_date=q.queue_date AND o.cycle=q.cycle AND o.content_type='defense' AND o.start_fen=c.start_fen AND o.moves_json=c.moves_json)))
AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND (o.revision<>c.revision OR o.content_type<>'defense' OR o.start_fen<>c.start_fen OR o.moves_json<>c.moves_json OR NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.id=o.queue_entry_id AND q.card_id=c.id AND q.queue_date=o.queue_date AND q.cycle=o.cycle)))
AND NOT EXISTS (SELECT 1 FROM public.cards r WHERE r.unlock_after_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.cards r WHERE r.superseded_by=c.id)
AND NOT EXISTS (SELECT 1 FROM public.gameplay_card_priorities r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_card_schedule_seeds r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_card_schedule_seeds r WHERE r.source_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_graph_legacy_mappings r WHERE r.decision_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_graph_legacy_mappings r WHERE r.legacy_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.prefix_splits r WHERE r.continuation_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.prefix_splits r WHERE r.shortened_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.prefix_splits r WHERE r.source_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_card_introduction_priorities r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_card_priority_generations r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_cards r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_decision_events_legacy r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_decision_events_staged r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_integrity_card_blocks r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_opportunities r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_priority_prepared_rows r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.reviews r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_attempts r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.tactic_captures r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.teaching_states r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.card_revisions r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.queue_projection_diagnostics r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.endgame_templates r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.tactic_progress r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.game_findings r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.game_repertoire_matches_legacy r WHERE r.deviation_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_graph_steps r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_graph_steps r WHERE r.parent_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.repertoire_opportunities r WHERE r.admitted_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.discovery_admission_intents r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.game_repertoire_matches_staged r WHERE r.deviation_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.review_attempt_receipts r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_segmentation_occurrences r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_segmentation_sources r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_evidence_presentations r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.opening_evidence_attempts r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.deleted_cards r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.prefix_transition_card_fences r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.frozen_opening_ranks r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.frozen_opening_queue_changes r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_cards r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_incident_versions r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_contributions r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_obligation_heads r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_attempts r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.remediation_settlements r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_game_feedback r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_conflict_holds r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_conflict_queue_holds r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_position_states r WHERE r.donor_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_position_seed_outcomes r WHERE r.donor_card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.study_position_issued_plans r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_abandonments r WHERE r.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.defense_attempts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.defense_recognition_submissions r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.frozen_opening_queue_changes r ON r.queue_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.opening_evidence_attempts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.opening_evidence_queue_contexts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.queue_attempt_abandonments r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.remediation_cards r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.review_attempt_receipts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.study_attempts r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.study_conflict_queue_holds r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q JOIN public.study_position_issued_plans r ON r.queue_entry_id=q.id WHERE q.card_id=c.id)
-- END CUT62 DEFENSE SELECTOR
), approved AS (SELECT value->>0 AS card_id,value->>1 AS candidate_id FROM jsonb_array_elements(current_setting('tempo.cut62_defense_manifest',true)::jsonb->'keys') value)
SELECT e.* FROM eligible e JOIN approved a USING(card_id,candidate_id);
DO $$ DECLARE selected_count bigint; queue_count bigint; origin_count bigint; affected bigint;
BEGIN
 SELECT count(*) INTO selected_count FROM cut62_defense_selected;
 IF selected_count<>jsonb_array_length(current_setting('tempo.cut62_defense_manifest',true)::jsonb->'keys') THEN RAISE EXCEPTION 'Approved Defense selection changed'; END IF;
 IF selected_count>0 THEN
  SELECT count(*) INTO queue_count FROM daily_queue q JOIN cut62_defense_selected s ON s.card_id=q.card_id;
  SELECT count(*) INTO origin_count FROM queue_attempt_origins o JOIN cut62_defense_selected s ON s.card_id=o.card_id;
  UPDATE threat_training_candidates t SET card_id=NULL FROM cut62_defense_selected s WHERE t.id=s.candidate_id AND t.card_id=s.card_id;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected<>selected_count THEN RAISE EXCEPTION 'Defense candidate count changed'; END IF;
  DELETE FROM daily_queue q USING cut62_defense_selected s WHERE q.card_id=s.card_id;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected<>queue_count THEN RAISE EXCEPTION 'Defense queue count changed'; END IF;
  DELETE FROM queue_attempt_origins o USING cut62_defense_selected s WHERE o.card_id=s.card_id AND o.content_type='defense';
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected<>origin_count THEN RAISE EXCEPTION 'Defense origin count changed'; END IF;
  DELETE FROM cards c USING cut62_defense_selected s WHERE c.id=s.card_id AND c.content_type='defense';
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected<>selected_count THEN RAISE EXCEPTION 'Defense card count changed'; END IF;
 END IF;
END $$;
DROP TRIGGER next_opponent_input_change ON imported_games;
DROP INDEX idx_next_opponent_account_time;
DROP INDEX idx_next_opponent_speed_rating_time;
DROP FUNCTION invalidate_next_opponent_inputs() RESTRICT;
DROP FUNCTION next_opponent_game_time(text) RESTRICT;
DROP TABLE next_opponent_accounts RESTRICT;
DROP TABLE next_opponent_snapshots RESTRICT;
DROP FUNCTION protect_next_opponent_snapshot() RESTRICT;
-- Preserve original Defense attempt identities, moves, grades and replay facts.
DROP TABLE endgame_attempts RESTRICT;
DROP TABLE daily_chess_snapshots RESTRICT;
DROP TABLE daily_chess_insights RESTRICT;
DROP TABLE daily_statistics_jobs RESTRICT;
DROP TABLE game_insight_recommendations RESTRICT;
-- The errors table owns game_analysis_position_errors_id_seq internally.
-- Keep the shared bump_priority_source_epoch() and its retained triggers.
-- The unreferenced game_analysis_age_origins() function is held for now until
-- dynamic retained callers are ruled out by its owner.
DROP TABLE game_analysis_publications RESTRICT;
DROP TABLE game_analysis_position_reports RESTRICT;
DROP TABLE game_analysis_position_errors RESTRICT;
DROP TABLE game_analysis_jobs RESTRICT;
DROP TABLE engine_scheduling_state RESTRICT;
INSERT INTO tempo_schema_migrations(version) VALUES (62);
