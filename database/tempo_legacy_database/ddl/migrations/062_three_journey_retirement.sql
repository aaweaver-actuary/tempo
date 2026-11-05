-- Review candidate: twelve exclusively retired stores; all shared state remains.
-- RESTRICT aborts the transaction if an unreviewed dependency remains.
-- Stopped-writer preflight: the runner provides the verified approved selection
-- as a parameterized transaction-local setting, never as SQL text or argv keys.
SET LOCAL search_path=public,pg_catalog;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL DateStyle = 'ISO, YMD';
SET LOCAL extra_float_digits=3;
SET LOCAL join_collapse_limit=1;
SET LOCAL jit=off;
SET LOCAL statement_timeout='30s';
DO $$ DECLARE manifest jsonb; actual_count bigint; actual_digest text;
BEGIN
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
 IF jsonb_array_length(manifest->'keys')>0 THEN
  IF current_setting('server_version_num')::int<>180006 OR current_setting('session_replication_role')<>'origin' OR current_setting('transaction_isolation')<>'repeatable read' THEN RAISE EXCEPTION 'Unsupported Defense cutover database session'; END IF;
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
-- BEGIN CUT62 DEFENSE ORIGIN FK
SELECT count(*)=1 AND COALESCE(bool_and(n.nspname='public' AND c.relname='queue_attempt_abandonments' AND con.conname='queue_attempt_abandonments_queue_entry_id_card_id_revision_fkey' AND pg_get_constraintdef(con.oid)='FOREIGN KEY (queue_entry_id, card_id, revision) REFERENCES queue_attempt_origins(queue_entry_id, card_id, revision)'),false)
FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE con.contype='f' AND con.confrelid='public.queue_attempt_origins'::regclass
-- END CUT62 DEFENSE ORIGIN FK
 ) THEN RAISE EXCEPTION 'cut62 Defense origin foreign-key ledger changed'; END IF;
 IF NOT (
-- BEGIN CUT62 DEFENSE UPDATED COLUMN FK
SELECT NOT EXISTS (
 SELECT 1 FROM pg_constraint con
 WHERE con.contype='f' AND con.confrelid IN ('public.threat_training_candidates'::regclass,'public.priority_repertoire_source_epochs'::regclass,'public.frozen_opening_runs'::regclass)
 AND EXISTS (SELECT 1 FROM unnest(con.confkey) key_column(attnum) JOIN pg_attribute a ON a.attrelid=con.confrelid AND a.attnum=key_column.attnum WHERE (con.confrelid='public.threat_training_candidates'::regclass AND a.attname='card_id') OR (con.confrelid='public.priority_repertoire_source_epochs'::regclass AND a.attname='version') OR (con.confrelid='public.frozen_opening_runs'::regclass AND a.attname='invalidated'))
)
-- END CUT62 DEFENSE UPDATED COLUMN FK
 ) THEN RAISE EXCEPTION 'cut62 Defense updated-column foreign-key ledger changed'; END IF;
 IF NOT (
-- BEGIN CUT62 DEFENSE TRIGGER LEDGER
WITH expected(table_name,trigger_name,enabled,trigger_type,function_schema,function_name,trigger_digest,function_digest) AS (VALUES
('cards','canonical_card_delete_source','O',9,'public','advance_canonical_prefix_source','19b4fce6943415cb587bd73c801a5bcbe76f08058b91350e9a0226e3f0d96700','0f6124f0b6b634bfe65a1662b8ac29c8776f47591de2f251bf5480d079cbbd0c'),
('cards','canonical_card_insert_source','O',5,'public','advance_canonical_prefix_source','563b9ab171090b6bbb4d74bf9f93f0ba867c3aaf88a52a30eee135bd7d9ae9f5','0f6124f0b6b634bfe65a1662b8ac29c8776f47591de2f251bf5480d079cbbd0c'),
('cards','canonical_card_promote','O',19,'public','promote_canonical_card_source','9772e19ff2fc93a273eaedf36c91bc3f4437f2576ee96f0a6449a0bb78563a6e','c70ab525932736ba6b72b3ce555b25108114d89da35e6ae0d23609694067c35f'),
('cards','canonical_card_source','O',17,'public','advance_canonical_prefix_source','13fb08871e34be4811e47e65bbb0bcf4fb69918c4e1b4e695b6e55a91ee28604','0f6124f0b6b634bfe65a1662b8ac29c8776f47591de2f251bf5480d079cbbd0c'),
('cards','deleted_card_recreation_guard','O',23,'public','prevent_deleted_card_recreation','de57bd558cb711797fab3f915f7041a254c89f4bcfa522874f044fef0bfa9113','7bf4d70e62f2a8822692fc82409f51aa62a23534f322ad8711c735f1d2ec9f13'),
('cards','frozen_guard_cards','O',28,'public','frozen_invalidate_pending','8d1b512e850b91127eb3c9c372b96713ec7df06ddbc5aecb6143c2d8b6ecdbdc','eea3585ca4e8ade215d3fbbe159a37f1e9668d4ee3829cd7dec330615b10ed2d'),
('cards','opening_evidence_01_saved_presentation','O',21,'public','opening_evidence_capture_presentation','53a2f64db450cf68c42367cda8a22807b11e75c9ffaa66ac6b1ccdfedf2df7b1','39302b4c090976a0287764dacb24943bb534f988aff7de8de57b142683a7fb50'),
('cards','opening_evidence_edited_scope','O',17,'public','opening_evidence_capture_edited_scope','f2b8f69a4104579c724296dcedf54727e1054ae57f1d2269222c47c8f31c8300','517e6d6067b1d634fd8631c81fc392245f2d8e064fd747e847f8fd22ba0cf80e'),
('cards','prefix_transition_card_write','O',31,'public','guard_prefix_transition_write','ec04cc6b3a1efa4e0ad48a992ef4e142975ab609bb7b9fdc7a6a1d2df3e493d6','1d7faf0ad60b4ae39388a8c6b16162ef9c7f876731573d4841330d2a42611875'),
('cards','priority_source_cards','O',29,'public','priority_card_source_changed','b696384919a4db0c92b3d7dd1801ecf9633a23785be3185d2d7d390c9a0dd6cd','cab9fd62a0ebc5f5839220a32b2eafd6563a22fdb24715dd6c73c1ff226adbe6'),
('cards','queue_attempt_origin_revision','O',17,'public','retain_queue_attempt_revision','87f4c99899d748adc59bcbb58a83d2c3d47a6bd1a01b0c110a8442d9db875d86','4d95b31f6a4970b9c2a33a6174e298e77158455fb29860ba243fdc930b4dd2c2'),
('cards','study_position_guard_legacy_fsrs_id','O',23,'public','study_position_guard_legacy_fsrs_id','5d7b69e921be8d4fd2fc24de638ac1626f73c3af9434037a1c6158110d792ff0','865a5679fdf7f12e118ed2f8d0a838639b34de52340ac61719ebbd620069455c'),
('daily_queue','frozen_guard_daily_queue','O',28,'public','frozen_invalidate_pending','88d2ef4a7e58980e226386dd25c04d8b8c5634eb73bef1cdc1059a9bb478845d','eea3585ca4e8ade215d3fbbe159a37f1e9668d4ee3829cd7dec330615b10ed2d'),
('daily_queue','opening_evidence_queue_scope','O',21,'public','opening_evidence_capture_queue_context','f25bb2efefad87cedab7464e4eb9c776919c652f89f5cc8979f7eca54196a01e','4ac4225be7f47b8bc682150a2bbf44d2e1b15c7bb0f562aca783fc57b1f70b6e'),
('daily_queue','prefix_transition_queue_write','O',30,'public','reserve_prefix_transition_queue_write','6d5442d186b8e43d7ef16711024e2a2bca309adda2c201312f2de012cdb15655','0bb404f3938c0383b1a1bd62d7693aa31a51d9e04eb7cd7115eba371ec2f5aa1'),
('daily_queue','queue_attempt_origin_delete','O',11,'public','retain_queue_attempt_origin','1956d8bb93ef22a6dfb7f02ea7cc3f1e9c1c6948d777594d699eaecaeaaba83e','dd8c36202d57b8e957db55d3d23c4e5b2c03c37cb8349a1f89160dff979bc674'),
('daily_queue','queue_attempt_origin_insert','O',5,'public','retain_queue_attempt_origin','a088930bdf13320e2824a06e811337b0d4532d19b1c501121efe38d34f0dec3b','dd8c36202d57b8e957db55d3d23c4e5b2c03c37cb8349a1f89160dff979bc674'),
('daily_queue','queue_attempt_origin_update','O',17,'public','retain_queue_attempt_origin','107c3d2b325146700e85aa2aaf20abdf343dcab87a100c2c27e2a001c84a6765','dd8c36202d57b8e957db55d3d23c4e5b2c03c37cb8349a1f89160dff979bc674'),
('queue_attempt_origins','frozen_guard_queue_attempt_origins','O',28,'public','frozen_invalidate_pending','1df7013fef41ed9a4a5898282737f078647cf09851c9bd96813226710709a379','eea3585ca4e8ade215d3fbbe159a37f1e9668d4ee3829cd7dec330615b10ed2d'),
('queue_attempt_origins','queue_attempt_abandoned_origin_immutable','O',27,'public','protect_abandoned_queue_origin','09d467b1e53ef6599d2341247be8629218029cb3caa860827790be9b71a0a0d3','e05f0ee0f613ade4d586d3d65d930f40a6d51753f32b8cf268f3ab43d289a8ae')
), actual AS (
 SELECT c.relname::text,t.tgname::text,t.tgenabled::text,t.tgtype::int,pn.nspname::text,p.proname::text,encode(sha256(convert_to(pg_get_triggerdef(t.oid),'UTF8')),'hex'),encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex')
 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace
 WHERE n.nspname='public' AND NOT t.tgisinternal AND c.relname IN ('cards','daily_queue','queue_attempt_origins','threat_training_candidates','priority_repertoire_source_epochs','frozen_opening_runs')
)
SELECT NOT EXISTS ((SELECT * FROM actual EXCEPT ALL SELECT * FROM expected) UNION ALL (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual))
-- END CUT62 DEFENSE TRIGGER LEDGER
 ) THEN RAISE EXCEPTION 'cut62 Defense trigger ledger changed'; END IF;
 IF NOT (
-- BEGIN CUT62 DEFENSE HELPER LEDGER
WITH expected(function_schema,function_name,arguments,digest) AS (VALUES
('public','bump_priority_repertoire_source','target_repertoire_id text','fb608697361d49ffc403eeb81d33036a1adbc13ea1e5098e8011a8cdf9e31ba5'),
('public','guard_prefix_transition_scope','scope_kind text, scope_id text','5e165e76a49e555c576c20b648072e258915f52a852d7a29d353823861d1ed46'),
('public','opening_evidence_bind_queue','queue_identifier bigint, card_identifier text, admission_repertoire text','a1a451192b69cb037186d4f1f23ff0f5bd139355fcbb838f3ec8b2fa57e4d577')
), actual AS (
 SELECT n.nspname::text,p.proname::text,pg_get_function_identity_arguments(p.oid),encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex')
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname IN ('bump_priority_repertoire_source','guard_prefix_transition_scope','opening_evidence_bind_queue')
)
SELECT NOT EXISTS ((SELECT * FROM actual EXCEPT ALL SELECT * FROM expected) UNION ALL (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual))
-- END CUT62 DEFENSE HELPER LEDGER
 ) THEN RAISE EXCEPTION 'cut62 Defense helper ledger changed'; END IF;
 IF NOT (
-- BEGIN CUT62 DEFENSE RULE LEDGER
SELECT NOT EXISTS (SELECT 1 FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('cards','daily_queue','queue_attempt_origins','threat_training_candidates','priority_repertoire_source_epochs','frozen_opening_runs'))
-- END CUT62 DEFENSE RULE LEDGER
 ) THEN RAISE EXCEPTION 'cut62 Defense rewrite-rule ledger changed'; END IF;
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
DO $$ DECLARE materialized_count bigint; materialized_digest text; manifest jsonb;
BEGIN
 manifest := current_setting('tempo.cut62_defense_manifest',true)::jsonb;
 SELECT selected_count,reference_digest INTO materialized_count,materialized_digest FROM (
-- BEGIN CUT62 MATERIALIZED REFERENCE DIGEST
WITH approved AS MATERIALIZED (
          SELECT value->>0 AS card_id, value->>1 AS candidate_id
          FROM jsonb_array_elements((current_setting('tempo.cut62_defense_manifest', true)::jsonb->'keys')) value
        ), eligible AS MATERIALIZED (SELECT card_id,candidate_id,repertoire_id FROM pg_temp.cut62_defense_selected), chosen AS MATERIALIZED (
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
-- END CUT62 MATERIALIZED REFERENCE DIGEST
 ) evidence;
 IF materialized_count IS DISTINCT FROM (manifest->'expected'->>'count')::bigint OR materialized_digest IS DISTINCT FROM manifest->'expected'->>'reference_digest' THEN RAISE EXCEPTION 'Materialized Defense evidence changed'; END IF;
END $$;
DO $$ DECLARE selected_count bigint; queue_count bigint; origin_count bigint; affected bigint;
BEGIN
 SELECT count(*) INTO selected_count FROM pg_temp.cut62_defense_selected;
 IF selected_count<>jsonb_array_length(current_setting('tempo.cut62_defense_manifest',true)::jsonb->'keys') THEN RAISE EXCEPTION 'Approved Defense selection changed'; END IF;
 IF selected_count>0 THEN
  SELECT count(*) INTO queue_count FROM daily_queue q JOIN cut62_defense_selected s ON s.card_id=q.card_id;
  SELECT count(*) INTO origin_count FROM queue_attempt_origins o JOIN cut62_defense_selected s ON s.card_id=o.card_id;
  UPDATE public.threat_training_candidates t SET card_id=NULL FROM pg_temp.cut62_defense_selected s WHERE t.id=s.candidate_id AND t.card_id=s.card_id;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected<>selected_count THEN RAISE EXCEPTION 'Defense candidate count changed'; END IF;
  DELETE FROM public.daily_queue q USING pg_temp.cut62_defense_selected s WHERE q.card_id=s.card_id;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected<>queue_count THEN RAISE EXCEPTION 'Defense queue count changed'; END IF;
  DELETE FROM public.queue_attempt_origins o USING pg_temp.cut62_defense_selected s WHERE o.card_id=s.card_id AND o.content_type='defense';
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected<>origin_count THEN RAISE EXCEPTION 'Defense origin count changed'; END IF;
  DELETE FROM public.cards c USING pg_temp.cut62_defense_selected s WHERE c.id=s.card_id AND c.content_type='defense';
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
