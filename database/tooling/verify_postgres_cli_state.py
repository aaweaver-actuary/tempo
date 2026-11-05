"""Fingerprint unchanged study history during a stopped-writer schema upgrade."""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))

import argparse
import json
import os

import psycopg

from database.tooling.migrate_sqlite_to_postgres import destination_fingerprint
from database.tooling.verify_postgres_backup import table_layout


# Protect membership, ordering, learner results, and admission provenance.
# Queue bucket/priority labels are derived metadata: migration 025 normalizes
# the legacy game-tactics bucket. Receipt retry/lease ownership is deliberately
# transformed by recovery migrations (notably 018). New physical columns do not
# automatically become immutable history merely because they exist.
STUDY_HISTORY_COLUMNS = {
    "reviews": ("id", "card_id", "rating", "reviewed_at", "previous_interval", "next_interval",
                "internal_rating", "guided", "source_kind", "source_ref", "invalidated_at", "invalidation_reason"),
    "daily_queue": ("id", "queue_date", "card_id", "cycle", "position", "status", "attempt_state",
                    "review_result_json", "attempt_failed", "admission_kind", "admission_repertoire_id", "admission_source"),
    "operation_receipts": ("operation_id", "command_name", "request_hash", "state",
                           "response_json", "error_json", "created_at", "updated_at"),
}


def study_fingerprints(database, expected=None):
    layout = table_layout(database)
    fingerprints = {}
    for table, historical_columns in STUDY_HISTORY_COLUMNS.items():
        primary_key, columns = layout[table]
        names = list(historical_columns)
        if not set(names).issubset(column[0] for column in columns):
            raise RuntimeError(f"Required historical columns are missing from {table}")
        if expected and expected[table]["columns"] != names:
            raise RuntimeError(f"Historical fingerprint contract differs for {table}")
        count, digest = destination_fingerprint(database, table, names, list(primary_key), set())
        fingerprints[table] = {"columns": names, "count": count, "digest": digest}
    return fingerprints



# Frozen from the schema-61 serving ledger. Any missing or extra table is a stop.
CUT62_PROTECTED_TABLES = frozenset((
    'analysis_refresh_requests',
    'background_activity',
    'background_diagnostic_metadata',
    'background_metric_buckets',
    'background_scheduling_turns',
    'background_task_events',
    'background_tasks',
    'canonical_prefix_positions',
    'canonical_prefix_previews',
    'canonical_prefix_results',
    'card_revisions',
    'cards',
    'coverage_discovery_recommendation_requests',
    'daily_queue',
    'daily_queue_days',
    'defense_attempts',
    'defense_recognition_submissions',
    'deleted_cards',
    'discovery_admission_intents',
    'discovery_recommendation_requests',
    'endgame_templates',
    'explorer_position_cache',
    'frozen_opening_activation',
    'frozen_opening_pending',
    'frozen_opening_queue_changes',
    'frozen_opening_ranks',
    'frozen_opening_runs',
    'game_accounts',
    'game_derivation_jobs',
    'game_feature_rows',
    'game_finding_preparations',
    'game_finding_publication_items',
    'game_findings',
    'game_move_analysis_candidates_legacy',
    'game_move_analysis_candidates_staged',
    'game_move_analysis_legacy',
    'game_move_analysis_staged',
    'game_position_occurrences_legacy',
    'game_position_occurrences_staged',
    'game_repertoire_matches_legacy',
    'game_repertoire_matches_staged',
    'game_sync_jobs',
    'game_sync_state',
    'game_sync_windows',
    'gameplay_card_priorities',
    'gameplay_events_legacy',
    'gameplay_events_staged',
    'guided_review_attempts',
    'guided_review_sessions',
    'imported_games',
    'internal_migrations',
    'legacy_content_historical_origins',
    'legacy_learning_identity',
    'opening_card_schedule_seeds',
    'opening_evidence_attempts',
    'opening_evidence_clean_days',
    'opening_evidence_events',
    'opening_evidence_observations',
    'opening_evidence_presentations',
    'opening_evidence_queue_contexts',
    'opening_evidence_summaries',
    'opening_graph_legacy_mappings',
    'opening_graph_publications',
    'opening_graph_steps',
    'opening_segmentation_groups',
    'opening_segmentation_occurrences',
    'opening_segmentation_parts',
    'opening_segmentation_preferences',
    'opening_segmentation_recommendations',
    'opening_segmentation_runs',
    'opening_segmentation_sources',
    'opening_segmentation_state',
    'operation_receipts',
    'position_annotations',
    'prefix_splits',
    'prefix_transition_applications',
    'prefix_transition_card_fences',
    'prefix_transition_repertoire_fences',
    'priority_repertoire_source_epochs',
    'priority_source_epoch',
    'product_feedback_captures',
    'product_feedback_configuration',
    'product_feedback_deliveries',
    'product_feedback_jobs',
    'product_feedback_managed_mode',
    'product_feedback_pages',
    'product_feedback_retry_requests',
    'product_feedback_sources',
    'product_feedback_sync_requests',
    'product_repertoire_change_receipts',
    'product_repertoire_previews',
    'puzzle_decks',
    'puzzles',
    'queue_attempt_abandonments',
    'queue_attempt_origins',
    'queue_projection_diagnostics',
    'queue_projections',
    'remediation_attempts',
    'remediation_cards',
    'remediation_contributions',
    'remediation_deliveries',
    'remediation_evidence',
    'remediation_game_heads',
    'remediation_incident_versions',
    'remediation_obligation_heads',
    'remediation_review_receipts',
    'remediation_settlements',
    'remediation_source_heads',
    'remediation_workspaces',
    'repertoire_card_introduction_priorities',
    'repertoire_card_priority_generations',
    'repertoire_cards',
    'repertoire_comparisons_legacy',
    'repertoire_comparisons_staged',
    'repertoire_coverage_candidates',
    'repertoire_coverage_nodes',
    'repertoire_coverage_runs',
    'repertoire_decision_events_legacy',
    'repertoire_decision_events_staged',
    'repertoire_decision_gameplay_summaries',
    'repertoire_game_scope',
    'repertoire_integrity_card_blocks',
    'repertoire_integrity_issue_candidates',
    'repertoire_integrity_issues',
    'repertoire_integrity_jobs',
    'repertoire_integrity_position_accumulators',
    'repertoire_integrity_source_runs',
    'repertoire_integrity_state',
    'repertoire_line_training_depths',
    'repertoire_lines',
    'repertoire_opportunities',
    'repertoire_priority_jobs',
    'repertoire_priority_preparations',
    'repertoire_priority_prepared_rows',
    'repertoire_priority_publications',
    'repertoires',
    'review_attempt_receipts',
    'review_schedule_snapshots',
    'reviews',
    'settings',
    'studies',
    'study_attempts',
    'study_chapters',
    'study_conflict_holds',
    'study_conflict_queue_holds',
    'study_conflict_receipts',
    'study_exercise_revisions',
    'study_exercises',
    'study_game_feedback',
    'study_links',
    'study_position_epochs',
    'study_position_first_responses',
    'study_position_grades',
    'study_position_issued_plans',
    'study_position_owners',
    'study_position_seed_outcomes',
    'study_position_seed_runs',
    'study_position_states',
    'study_positions',
    'study_recombination_changes',
    'study_recombination_completions',
    'study_recombination_current',
    'study_recombination_events',
    'study_recombination_presentations',
    'study_recombination_streaks',
    'study_sibling_burials',
    'study_sources',
    'tablebase_cache',
    'tactic_captures',
    'tactic_discovery_attempts',
    'tactic_introductions',
    'tactic_pack_activation',
    'tactic_progress',
    'tactic_rotation',
    'tactical_opportunities',
    'teaching_states',
    'tempo_migration_progress',
    'threat_analysis_report_history',
    'threat_analysis_requests',
    'threat_candidate_requests',
    'threat_training_candidates',
))
CUT62_RETIRED_TABLES = frozenset(('daily_chess_snapshots', 'daily_chess_insights', 'daily_statistics_jobs',
                                  'game_insight_recommendations', 'endgame_attempts',
                                  'next_opponent_accounts', 'next_opponent_snapshots',
                                  'game_analysis_jobs', 'game_analysis_position_reports',
                                  'game_analysis_position_errors', 'game_analysis_publications',
                                  'engine_scheduling_state'))
CUT62_LEDGER = 'tempo_schema_migrations'
CUT62_EVIDENCE_VERSION = 5
CUT62_PROTECTED_SEQUENCES = frozenset((
    'background_task_events_id_seq', 'card_revisions_id_seq', 'daily_queue_id_seq',
    'opening_evidence_presentations_id_seq',
    'reviews_id_seq', 'study_attempt_issuance_sequence', 'study_position_fsrs_id_seq',
    'study_recombination_completions_sequence_seq', 'threat_analysis_report_history_id_seq',
))
CUT62_RETIRED_SEQUENCES = frozenset(('game_analysis_position_errors_id_seq',))


def _cut62_mac(secret, domain, values):
    import hashlib
    import hmac
    mac = hmac.new(secret, domain.encode('utf-8') + b'\0', hashlib.sha256)
    count = 0
    for value in values:
        encoded = value.encode('utf-8')
        mac.update(len(encoded).to_bytes(8, 'big'))
        mac.update(encoded)
        count += 1
    return {'count': count, 'digest': mac.hexdigest()}


def _cut62_table(database, table, primary_key, secret):
    from psycopg import sql
    query = sql.SQL('SELECT row_to_json(t)::text FROM {}.{} t ORDER BY {}').format(
        sql.Identifier('public'), sql.Identifier(table),
        sql.SQL(',').join(sql.Identifier(key) for key in primary_key))
    with database.cursor(name='cut62_' + table) as cursor:
        cursor.execute(query)
        return _cut62_mac(secret, 'cut62:rows:' + table, (row[0] for row in cursor))



def load_cut62_defense_selection(path):
    """Read the private, owner-approved selection without disclosing its keys."""
    import hashlib
    import re
    import stat
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise RuntimeError('Defense selection must be a private regular file')
        raw = stream.read()
    selection = json.loads(raw)
    if not isinstance(selection, dict) or set(selection) != {'version','target','cohort','keys','expected'} or type(selection['version']) is not int or selection['version'] != 1 or selection['cohort'] != 'defense-no-activity':
        raise RuntimeError('unsupported Defense selection')
    if not isinstance(selection['target'], str) or not selection['target'] or not isinstance(selection['keys'], list) or len(selection['keys']) > 11:
        raise RuntimeError('invalid Defense selection')
    previous = None
    for pair in selection['keys']:
        if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(v, str) or not v for v in pair):
            raise RuntimeError('invalid Defense selection key')
        key = tuple(pair)
        if previous is not None and key <= previous:
            raise RuntimeError('Defense selection keys are not unique and sorted')
        previous = key
    expected = selection['expected']
    if not isinstance(expected, dict) or set(expected) != {'count','reference_digest'} or type(expected['count']) is not int or expected['count'] != len(selection['keys']) or not isinstance(expected['reference_digest'], str) or re.fullmatch('[0-9a-f]{64}', expected['reference_digest']) is None:
        raise RuntimeError('invalid Defense selection reference evidence')
    return selection, hashlib.sha256(raw).hexdigest()


def validate_cut62_history_selection(selection):
    """Validate a draft historical selection; this does not authorize a cut.

    Version 2 is deliberately separate from the operational version 1 loader.
    Its digest must be derived from the fenced original rows and independently
    checked by the eventual migration before this format can be accepted.
    """
    import re
    groups = ('no_activity', 'recognition_only', 'reviewed')
    if (not isinstance(selection, dict)
            or set(selection) != {'version', 'target', 'cohort', 'keys', 'expected'}
            or type(selection['version']) is not int or selection['version'] != 2
            or selection['cohort'] != 'defense-history'
            or not isinstance(selection['target'], str) or not selection['target']
            or not isinstance(selection['keys'], list) or len(selection['keys']) > 15):
        raise RuntimeError('invalid Defense history selection')
    counts = dict.fromkeys(groups, 0)
    previous = None
    card_ids, candidate_ids = set(), set()
    for item in selection['keys']:
        if (not isinstance(item, list) or len(item) != 3
                or any(not isinstance(value, str) or not value for value in item)
                or item[2] not in counts):
            raise RuntimeError('invalid Defense history key')
        identity = tuple(item[:2])
        if previous is not None and identity <= previous:
            raise RuntimeError('Defense history keys are not unique and sorted')
        if item[0] in card_ids or item[1] in candidate_ids:
            raise RuntimeError('Defense history keys are not unique and sorted')
        card_ids.add(item[0])
        candidate_ids.add(item[1])
        previous = identity
        counts[item[2]] += 1
    expected = selection['expected']
    if (not isinstance(expected, dict) or set(expected) != {'count', 'groups', 'reference_digest'}
            or type(expected['count']) is not int or expected['count'] != len(selection['keys'])
            or not isinstance(expected['groups'], dict) or set(expected['groups']) != set(groups)
            or any(type(expected['groups'][group]) is not int or expected['groups'][group] != counts[group] for group in groups)
            or not isinstance(expected['reference_digest'], str)
            or re.fullmatch('[0-9a-f]{64}', expected['reference_digest']) is None):
        raise RuntimeError('invalid Defense history reference evidence')
    return counts


def load_cut62_history_selection(path):
    """Read a private v2 selection and preserve its exact byte identity."""
    import hashlib
    import stat
    descriptor = os.open(path,os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(descriptor,'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise RuntimeError('Defense selection must be a private regular file')
        raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise RuntimeError('Defense selection exceeds size limit')
    def unique_object(pairs):
        result = {}
        for key,value in pairs:
            if key in result:
                raise RuntimeError('Defense selection contains duplicate keys')
            result[key] = value
        return result
    selection = json.loads(raw,object_pairs_hook=unique_object)
    validate_cut62_history_selection(selection)
    return selection,hashlib.sha256(raw).hexdigest()


def load_cut62_attempt_binding(path, selection, selection_sha256):
    import re
    import stat
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise RuntimeError('cut62 binding must be a private regular file')
        binding = json.load(stream)
    return _validate_cut62_attempt_binding(binding,selection,selection_sha256)


def _validate_cut62_attempt_binding(binding,selection,selection_sha256):
    import re
    if not isinstance(binding, dict) or set(binding) != {'version','target','attempt_id','backup_filename','backup_sha256','selection_sha256'} or type(binding['version']) is not int or binding['version'] != 1:
        raise RuntimeError('invalid cut62 attempt binding')
    if not isinstance(binding['target'], str) or not binding['target'] or (selection is not None and binding['target'] != selection['target']) or binding['selection_sha256'] != selection_sha256:
        raise RuntimeError('cut62 selection binding changed')
    if any(not isinstance(binding[k], str) or not binding[k] for k in ('attempt_id','backup_filename')) or not isinstance(binding['backup_sha256'], str) or re.fullmatch('[0-9a-f]{64}', binding['backup_sha256']) is None:
        raise RuntimeError('invalid cut62 attempt binding')
    if not isinstance(selection_sha256,str) or re.fullmatch('[0-9a-f]{64}',selection_sha256) is None:
        raise RuntimeError('invalid cut62 selection digest')
    return binding


# Draft only: static typed ownership predicates need catalog and owner review.
CUT62_DEFENSE_FK_LEDGER_SQL = '''
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
'''

CUT62_DEFENSE_TRIGGER_LEDGER_SQL = '''
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
'''

CUT62_DEFENSE_ORIGIN_FK_SQL = '''
SELECT count(*)=1 AND COALESCE(bool_and(n.nspname='public' AND c.relname='queue_attempt_abandonments' AND con.conname='queue_attempt_abandonments_queue_entry_id_card_id_revision_fkey' AND pg_get_constraintdef(con.oid)='FOREIGN KEY (queue_entry_id, card_id, revision) REFERENCES queue_attempt_origins(queue_entry_id, card_id, revision)'),false)
FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE con.contype='f' AND con.confrelid='public.queue_attempt_origins'::regclass
'''

CUT62_DEFENSE_UPDATED_COLUMN_FK_SQL = '''
SELECT NOT EXISTS (
 SELECT 1 FROM pg_constraint con
 WHERE con.contype='f' AND con.confrelid IN ('public.threat_training_candidates'::regclass,'public.priority_repertoire_source_epochs'::regclass,'public.frozen_opening_runs'::regclass)
 AND EXISTS (SELECT 1 FROM unnest(con.confkey) key_column(attnum) JOIN pg_attribute a ON a.attrelid=con.confrelid AND a.attnum=key_column.attnum WHERE (con.confrelid='public.threat_training_candidates'::regclass AND a.attname='card_id') OR (con.confrelid='public.priority_repertoire_source_epochs'::regclass AND a.attname='version') OR (con.confrelid='public.frozen_opening_runs'::regclass AND a.attname='invalidated'))
)
'''

CUT62_DEFENSE_HELPER_LEDGER_SQL = '''
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
'''

CUT62_DEFENSE_RULE_LEDGER_SQL = '''
SELECT NOT EXISTS (SELECT 1 FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('cards','daily_queue','queue_attempt_origins','threat_training_candidates','priority_repertoire_source_epochs','frozen_opening_runs'))
'''

CUT62_DRAFT_DEFENSE_SELECTOR = '''
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
'''

CUT62_DEFENSE_REFERENCE_SQL = """
        WITH approved AS MATERIALIZED (
          SELECT value->>0 AS card_id, value->>1 AS candidate_id
          FROM jsonb_array_elements(%s::jsonb) value
        ), eligible AS MATERIALIZED ({selector}), chosen AS MATERIALIZED (
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
        ) SELECT (SELECT count(*) FROM chosen), encode(sha256(convert_to(value::text,'UTF8')),'hex') FROM evidence
    """


def _cut62_defense_reference_digest(database, keys):
    """Return only the count and digest of exact original approved references."""
    from psycopg import sql
    query = sql.SQL(CUT62_DEFENSE_REFERENCE_SQL).format(selector=sql.SQL(CUT62_DRAFT_DEFENSE_SELECTOR))
    return database.execute(query, (json.dumps(keys, separators=(',', ':')),)).fetchone()


CUT62_DEFENSE_PROJECTED_TABLES = frozenset(('cards', 'daily_queue', 'queue_attempt_origins', 'threat_training_candidates', 'priority_repertoire_source_epochs', 'frozen_opening_runs'))

# Draft projection primitive; the reviewed fixed selector and migration wiring
# are still required before this can be used for a cutover witness.
def _cut62_projected_defense_table(database, table, primary_key, secret, cohort_sql, params=()):
    from psycopg import sql
    views = {
        'cards': "SELECT t.* FROM public.cards t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.id)",
        'daily_queue': "SELECT t.* FROM public.daily_queue t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.card_id)",
        'queue_attempt_origins': "SELECT t.* FROM public.queue_attempt_origins t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.card_id)",
        'threat_training_candidates': "SELECT (json_populate_record(t, CASE WHEN EXISTS (SELECT 1 FROM cohort c WHERE c.candidate_id=t.id) THEN '{\"card_id\":null}'::json ELSE '{}'::json END)).* FROM public.threat_training_candidates t",
        'priority_repertoire_source_epochs': "SELECT (json_populate_record(t, json_build_object('version',t.version+(SELECT count(*) FROM cohort c WHERE c.repertoire_id=t.repertoire_id)))).* FROM public.priority_repertoire_source_epochs t UNION ALL SELECT r.id AS repertoire_id,(SELECT count(*) FROM cohort c WHERE c.repertoire_id=r.id)::bigint AS version FROM public.repertoires r WHERE EXISTS (SELECT 1 FROM cohort c WHERE c.repertoire_id=r.id) AND NOT EXISTS (SELECT 1 FROM public.priority_repertoire_source_epochs e WHERE e.repertoire_id=r.id)",
        'frozen_opening_runs': "SELECT (json_populate_record(t, CASE WHEN EXISTS (SELECT 1 FROM cohort) AND EXISTS (SELECT 1 FROM public.frozen_opening_pending p WHERE p.run_id=t.run_id) AND t.invalidated=0 THEN '{\"invalidated\":1}'::json ELSE '{}'::json END)).* FROM public.frozen_opening_runs t",
    }
    if table not in views:
        raise ValueError('unsupported Defense projection table')
    query = sql.SQL('WITH cohort AS ({cohort}), projected AS ({projection}) SELECT row_to_json(t)::text FROM projected t ORDER BY {keys}').format(
        cohort=sql.SQL(cohort_sql), projection=sql.SQL(views[table]),
        keys=sql.SQL(',').join(sql.Identifier(key) for key in primary_key))
    with database.cursor(name='cut62_projected_' + table) as cursor:
        cursor.execute(query, params)
        return _cut62_mac(secret, 'cut62:rows:' + table, (row[0] for row in cursor))


# Read-only catalog inventory for the three additional DML targets. The
# snapshots table is deliberately excluded: its rows are retained, and its
# inbound references are covered when checking reviews. No captured baseline
# is trusted as a production allowlist; a supported PG18.6 ledger must be
# independently established before enabling history DML.
CUT62_HISTORY_CATALOG_SQL = """
SELECT 'foreign_key', target.relname::text, source_ns.nspname::text,
       source.relname::text, con.conname::text, pg_get_constraintdef(con.oid), con.confdeltype::text
FROM pg_constraint con JOIN pg_class target ON target.oid=con.confrelid
JOIN pg_namespace target_ns ON target_ns.oid=target.relnamespace
JOIN pg_class source ON source.oid=con.conrelid JOIN pg_namespace source_ns ON source_ns.oid=source.relnamespace
WHERE con.contype='f' AND target_ns.nspname='public'
  AND target.relname IN ('defense_attempts','defense_recognition_submissions','reviews')
UNION ALL
SELECT 'trigger', c.relname::text, fn_ns.nspname::text, p.proname::text,
       t.tgname::text, pg_get_triggerdef(t.oid),
       t.tgenabled::text || ':' || t.tgtype::text || ':' || encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex')
FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace fn_ns ON fn_ns.oid=p.pronamespace
WHERE n.nspname='public' AND NOT t.tgisinternal
  AND c.relname IN ('defense_attempts','defense_recognition_submissions','reviews')
UNION ALL
SELECT 'rule', c.relname::text,n.nspname::text,''::text,r.rulename::text,pg_get_ruledef(r.oid),r.ev_enabled::text
FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname IN ('defense_attempts','defense_recognition_submissions','reviews')
UNION ALL
SELECT 'helper',''::text,n.nspname::text,p.proname::text,pg_get_function_identity_arguments(p.oid),
       pg_get_functiondef(p.oid),COALESCE(p.proconfig::text,'')
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND p.proname IN ('priority_review_source_changed','bump_priority_repertoire_source','frozen_invalidate_pending')
ORDER BY 1,2,3,4,5,6,7
"""


def _cut62_history_catalog_snapshot(database):
    """Read public catalog metadata only; never derive authority from this read."""
    return database.execute(CUT62_HISTORY_CATALOG_SQL).fetchall()


# Fixed only for the isolated proof; never derive authority from the database
# being migrated or its witness. Operational integration requires review.
# Candidate observed independently on disposable postgres:18.6-trixie,
# fresh migrations 001-061, search_path pg_catalog,public,pg_temp. It is not
# active authority until the provenance and exact definition receive review.
CUT62_HISTORY_CANDIDATE_PG180006_SHA256 = '370d93023bba48b1bf3422ff8a78341b7b8d7d06bb318497fb0cc486a9c1db6b'
# Isolated proof candidate; not yet approved for operational integration.
CUT62_HISTORY_TRUSTED_PG180006_CATALOG = [('helper',
  '',
  'public',
  'bump_priority_repertoire_source',
  'target_repertoire_id text',
  'CREATE OR REPLACE FUNCTION public.bump_priority_repertoire_source(target_repertoire_id text)\n'
  ' RETURNS void\n'
  ' LANGUAGE plpgsql\n'
  'AS $function$\n'
  'BEGIN\n'
  '    IF target_repertoire_id IS NOT NULL AND EXISTS (\n'
  '        SELECT 1 FROM repertoires WHERE id=target_repertoire_id\n'
  '    ) THEN\n'
  '        INSERT INTO priority_repertoire_source_epochs(repertoire_id,version)\n'
  '        VALUES(target_repertoire_id,1)\n'
  '        ON CONFLICT(repertoire_id) DO UPDATE\n'
  '        SET version=priority_repertoire_source_epochs.version+1;\n'
  '    END IF;\n'
  'END;\n'
  '$function$\n',
  ''),
 ('helper',
  '',
  'public',
  'frozen_invalidate_pending',
  '',
  'CREATE OR REPLACE FUNCTION public.frozen_invalidate_pending()\n'
  ' RETURNS trigger\n'
  ' LANGUAGE plpgsql\n'
  'AS $function$\n'
  'BEGIN\n'
  '    UPDATE frozen_opening_runs SET invalidated=1\n'
  '    WHERE run_id IN (SELECT run_id FROM frozen_opening_pending) AND invalidated=0;\n'
  '    RETURN NULL;\n'
  'END $function$\n',
  ''),
 ('helper',
  '',
  'public',
  'priority_review_source_changed',
  '',
  'CREATE OR REPLACE FUNCTION public.priority_review_source_changed()\n'
  ' RETURNS trigger\n'
  ' LANGUAGE plpgsql\n'
  'AS $function$\n'
  'DECLARE\n'
  '    affected_repertoire_id TEXT;\n'
  '    old_card_id TEXT;\n'
  '    new_card_id TEXT;\n'
  'BEGIN\n'
  "    IF TG_OP='DELETE' THEN\n"
  '        old_card_id := OLD.card_id;\n'
  '    ELSE\n'
  '        new_card_id := NEW.card_id;\n'
  "        IF TG_OP='UPDATE' THEN\n"
  '            old_card_id := OLD.card_id;\n'
  '        END IF;\n'
  '    END IF;\n'
  '    FOR affected_repertoire_id IN\n'
  '        SELECT DISTINCT repertoire_id FROM (\n'
  '            SELECT link.repertoire_id FROM repertoire_cards link\n'
  '            WHERE link.card_id IN (old_card_id,new_card_id)\n'
  '            UNION\n'
  '            SELECT card.repertoire_id FROM cards card\n'
  '            WHERE card.id IN (old_card_id,new_card_id)\n'
  '        ) affected WHERE repertoire_id IS NOT NULL ORDER BY repertoire_id\n'
  '    LOOP\n'
  '        PERFORM bump_priority_repertoire_source(affected_repertoire_id);\n'
  '    END LOOP;\n'
  '    RETURN NULL;\n'
  'END;\n'
  '$function$\n',
  ''),
 ('trigger',
  'reviews',
  'public',
  'frozen_invalidate_pending',
  'frozen_guard_reviews',
  'CREATE TRIGGER frozen_guard_reviews AFTER INSERT OR DELETE OR UPDATE ON public.reviews FOR EACH STATEMENT '
  'EXECUTE FUNCTION frozen_invalidate_pending()',
  'O:28:eea3585ca4e8ade215d3fbbe159a37f1e9668d4ee3829cd7dec330615b10ed2d'),
 ('trigger',
  'reviews',
  'public',
  'priority_review_source_changed',
  'priority_source_reviews',
  'CREATE TRIGGER priority_source_reviews AFTER INSERT OR DELETE OR UPDATE OF card_id, source_kind, '
  'reviewed_at ON public.reviews FOR EACH ROW EXECUTE FUNCTION priority_review_source_changed()',
  'O:29:57e5074b31fd891866020a998b902d2f19505ea576f76edcb5b765a9c8c74408')]


def _cut62_check_history_catalog(database):
    """Fail closed until a fixed supported catalog ledger is reviewed."""
    ledger = CUT62_HISTORY_TRUSTED_PG180006_CATALOG
    if ledger is None:
        raise RuntimeError('missing reviewed Defense history catalog ledger')
    if database.execute("SELECT current_setting('server_version_num')::int").fetchone()[0] != 180006:
        raise RuntimeError('unsupported Defense history PostgreSQL version')
    if _cut62_history_catalog_snapshot(database) != ledger:
        raise RuntimeError('Defense history catalog changed')


def _cut62_draft_history_selector():
    """Fixture-derived source proposal, not wired to the migration or runner."""
    inventory = CUT62_DRAFT_DEFENSE_SELECTOR.split('AND NOT EXISTS (SELECT 1 FROM public.cards r WHERE r.unlock_after_card_id=c.id)', 1)[1]
    inventory = 'AND NOT EXISTS (SELECT 1 FROM public.cards r WHERE r.unlock_after_card_id=c.id)' + inventory
    owned = ('FROM public.reviews r WHERE', 'FROM public.review_attempt_receipts r WHERE',
             'JOIN public.defense_attempts r ON', 'JOIN public.defense_recognition_submissions r ON',
             'JOIN public.review_attempt_receipts r ON')
    inventory = '\n'.join(line for line in inventory.splitlines() if not any(item in line for item in owned))
    receipt_guard = CUT62_DRAFT_DEFENSE_SELECTOR.split('AND NOT EXISTS (SELECT 1 FROM typed_receipts r WHERE', 1)[1].split('AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.issuance_sequence', 1)[0]
    receipt_guard = 'AND NOT EXISTS (SELECT 1 FROM typed_receipts r WHERE' + receipt_guard
    return """WITH typed_receipts AS MATERIALIZED (
        SELECT CASE WHEN pg_input_is_valid(payload_json,'jsonb') THEN payload_json::jsonb END payload,
               CASE WHEN pg_input_is_valid(response_json,'jsonb') THEN response_json::jsonb END response
        FROM public.operation_receipts WHERE command_name IN
        ('cards.review','cards.review.reconcile','queue.attempt_failed','queue.bury','queue.issue_head','opening_evidence.checkpoint')
    ) SELECT c.id card_id,t.id candidate_id,c.repertoire_id,
        CASE WHEN EXISTS (SELECT 1 FROM public.defense_attempts a WHERE a.candidate_id=t.id) THEN 'reviewed' ELSE 'recognition_only' END cohort_group
      FROM public.cards c JOIN public.threat_training_candidates t ON t.card_id=c.id
      WHERE c.content_type='defense' AND c.repertoire_id IS NOT NULL
                AND (SELECT count(*) FROM public.threat_training_candidates t2 WHERE t2.card_id=c.id)=1
        AND EXISTS (SELECT 1 FROM public.defense_recognition_submissions r WHERE r.candidate_id=t.id)
        AND NOT EXISTS (SELECT 1 FROM public.defense_attempts a WHERE a.candidate_id=t.id AND a.card_id<>c.id)
        AND NOT EXISTS (SELECT 1 FROM public.defense_attempts a
          LEFT JOIN public.daily_queue q ON q.id=a.queue_entry_id AND q.card_id=c.id
          LEFT JOIN public.queue_attempt_origins o ON o.queue_entry_id=q.id AND o.card_id=c.id AND o.content_type='defense'
          WHERE a.card_id=c.id AND (a.candidate_id<>t.id OR o.queue_entry_id IS NULL
            OR (SELECT count(*) FROM public.queue_attempt_origins oo WHERE oo.queue_entry_id=q.id AND oo.card_id=c.id AND oo.content_type='defense')<>1
            OR q.status<>'complete' OR o.last_status<>'complete' OR q.review_result_json IS NULL
            OR o.review_result_json IS DISTINCT FROM q.review_result_json OR NOT pg_input_is_valid(q.review_result_json,'jsonb')
            OR (CASE WHEN pg_input_is_valid(q.review_result_json,'jsonb') THEN q.review_result_json::jsonb->>'card_id' END) IS DISTINCT FROM c.id
            OR (SELECT count(*) FROM public.reviews r WHERE r.card_id=c.id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id)<>1))
        AND NOT EXISTS (SELECT 1 FROM public.defense_recognition_submissions r
          LEFT JOIN public.daily_queue q ON q.id=r.queue_entry_id AND q.card_id=c.id
          LEFT JOIN public.queue_attempt_origins o ON o.queue_entry_id=q.id AND o.card_id=c.id AND o.content_type='defense'
          WHERE r.candidate_id=t.id AND (o.queue_entry_id IS NULL OR
            (SELECT count(*) FROM public.queue_attempt_origins oo WHERE oo.queue_entry_id=q.id AND oo.card_id=c.id AND oo.content_type='defense')<>1))
        AND NOT EXISTS (SELECT 1 FROM public.reviews r WHERE r.card_id=c.id AND NOT EXISTS
          (SELECT 1 FROM public.defense_attempts a WHERE a.card_id=c.id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id))
        AND NOT EXISTS (SELECT 1 FROM public.review_attempt_receipts rr WHERE rr.card_id=c.id OR rr.queue_entry_id IN (SELECT q.id FROM public.daily_queue q WHERE q.card_id=c.id))
        AND NOT EXISTS (SELECT 1 FROM public.review_schedule_snapshots ss JOIN public.reviews r ON r.id=ss.review_id WHERE r.card_id=c.id)
        AND NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND q.review_result_json IS NOT NULL AND
          (q.status<>'complete' OR NOT pg_input_is_valid(q.review_result_json,'jsonb') OR
            (CASE WHEN pg_input_is_valid(q.review_result_json,'jsonb') THEN q.review_result_json::jsonb->>'card_id' END) IS DISTINCT FROM c.id OR
            NOT EXISTS (SELECT 1 FROM public.defense_attempts a JOIN public.reviews r ON r.card_id=a.card_id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id WHERE a.card_id=c.id AND a.candidate_id=t.id AND a.queue_entry_id=q.id)))
        AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o JOIN public.daily_queue q ON q.id=o.queue_entry_id AND q.card_id=o.card_id WHERE o.card_id=c.id AND (o.review_result_json IS DISTINCT FROM q.review_result_json OR (o.review_result_json IS NOT NULL AND o.last_status<>'complete')))
        AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.issuance_sequence IS NOT NULL)
        AND NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND q.admission_source IS NOT NULL AND q.admission_source<>'defense:'||t.id)
        AND NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND q.status='complete' AND NOT EXISTS
          (SELECT 1 FROM public.defense_attempts a WHERE a.card_id=c.id AND a.candidate_id=t.id AND a.queue_entry_id=q.id))
        AND NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND
          (SELECT count(*) FROM public.queue_attempt_origins o WHERE o.queue_entry_id=q.id AND o.card_id=c.id AND o.content_type='defense' AND o.queue_date=q.queue_date AND o.cycle=q.cycle)<>1)
        AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND
          (o.content_type<>'defense' OR NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.id=o.queue_entry_id AND q.card_id=c.id AND q.queue_date=o.queue_date AND q.cycle=o.cycle)))
    """ + receipt_guard + inventory


def _cut62_draft_history_reference(database, keys):
    """Proposed original exact-row evidence; pending native/catalog review."""
    from psycopg import sql
    query = sql.SQL("""WITH no_activity AS ({ordinary}), historical AS ({historical}),
        eligible AS (SELECT card_id,candidate_id,repertoire_id,'no_activity'::text cohort_group FROM no_activity
                     UNION ALL SELECT card_id,candidate_id,repertoire_id,cohort_group FROM historical),
        approved AS (SELECT value->>0 card_id,value->>1 candidate_id,value->>2 cohort_group FROM jsonb_array_elements(%s::jsonb) value),
        cohort AS MATERIALIZED (SELECT e.* FROM eligible e JOIN approved a USING(card_id,candidate_id,cohort_group)),
        evidence AS (SELECT jsonb_build_object(
          'cards',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM public.cards t JOIN cohort c ON c.card_id=t.id),'[]'::jsonb),
          'candidates',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM public.threat_training_candidates t JOIN cohort c ON c.candidate_id=t.id),'[]'::jsonb),
          'queues',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM public.daily_queue t JOIN cohort c ON c.card_id=t.card_id),'[]'::jsonb),
          'origins',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.queue_entry_id,t.card_id,t.revision) FROM public.queue_attempt_origins t JOIN cohort c ON c.card_id=t.card_id),'[]'::jsonb),
          'recognitions',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.attempt_id) FROM public.defense_recognition_submissions t JOIN cohort c ON c.candidate_id=t.candidate_id),'[]'::jsonb),
          'attempts',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.attempt_id) FROM public.defense_attempts t JOIN cohort c ON c.candidate_id=t.candidate_id AND c.card_id=t.card_id),'[]'::jsonb),
          'reviews',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id) FROM public.reviews r JOIN public.defense_attempts a ON r.card_id=a.card_id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id JOIN cohort c ON c.candidate_id=a.candidate_id AND c.card_id=a.card_id),'[]'::jsonb),
          'request_links',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.candidate_id,t.request_id,t.role) FROM public.threat_candidate_requests t JOIN cohort c ON c.candidate_id=t.candidate_id),'[]'::jsonb),
          'requests',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM public.threat_analysis_requests t WHERE EXISTS (SELECT 1 FROM public.threat_candidate_requests l JOIN cohort c ON c.candidate_id=l.candidate_id WHERE l.request_id=t.id)),'[]'::jsonb),
          'games',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM public.imported_games t WHERE EXISTS (SELECT 1 FROM public.threat_training_candidates x JOIN cohort c ON c.candidate_id=x.id WHERE x.game_id=t.id)),'[]'::jsonb),
          'findings',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM public.game_findings t WHERE EXISTS (SELECT 1 FROM public.threat_training_candidates x JOIN cohort c ON c.candidate_id=x.id WHERE x.finding_id=t.id)),'[]'::jsonb),
          'epochs',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.repertoire_id) FROM public.priority_repertoire_source_epochs t WHERE EXISTS (SELECT 1 FROM cohort c WHERE c.repertoire_id=t.repertoire_id)),'[]'::jsonb)
        ) value)
        SELECT (SELECT count(*) FROM cohort),
          (SELECT jsonb_object_agg(g, n) FROM (SELECT g,(SELECT count(*) FROM cohort WHERE cohort_group=g) n FROM unnest(ARRAY['no_activity','recognition_only','reviewed']) g) counts),
          encode(sha256(convert_to(value::text,'UTF8')),'hex') FROM evidence""").format(
            ordinary=sql.SQL(CUT62_DRAFT_DEFENSE_SELECTOR), historical=sql.SQL(_cut62_draft_history_selector()))
    return database.execute(query, (json.dumps(keys,separators=(',',':')),)).fetchone()


def _cut62_check_draft_history_selection(database, selection):
    """Revalidate the approved exact set within the caller's fenced RR transaction."""
    validate_cut62_history_selection(selection)
    if database.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise RuntimeError('Defense history check requires an active transaction')
    isolation, read_only = database.execute("SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only')").fetchone()
    if (isolation, read_only) != ('repeatable read', 'off'):
        raise RuntimeError('Defense history check requires repeatable read write transaction')
    count, groups, digest = _cut62_draft_history_reference(database, selection['keys'])
    if (count, groups, digest) != (selection['expected']['count'], selection['expected']['groups'], selection['expected']['reference_digest']):
        raise RuntimeError('approved Defense history reference evidence changed')
    return count, groups, digest


# Source-only history projection. The caller must supply the exact, fenced and
# independently reviewed cohort; this helper alone does not authorize DML.
CUT62_HISTORY_PROJECTED_TABLES = CUT62_DEFENSE_PROJECTED_TABLES | frozenset((
    'defense_recognition_submissions', 'defense_attempts', 'reviews',
))

def _cut62_projected_history_table(database, table, primary_key, secret, cohort_sql, params=()):
    from psycopg import sql
    views = {
        'cards': "SELECT t.* FROM public.cards t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.id)",
        'daily_queue': "SELECT t.* FROM public.daily_queue t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.card_id)",
        'queue_attempt_origins': "SELECT t.* FROM public.queue_attempt_origins t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.card_id)",
        'threat_training_candidates': "SELECT (json_populate_record(t, CASE WHEN EXISTS (SELECT 1 FROM cohort c WHERE c.candidate_id=t.id) THEN '{\"card_id\":null}'::json ELSE '{}'::json END)).* FROM public.threat_training_candidates t",
        'defense_recognition_submissions': "SELECT t.* FROM public.defense_recognition_submissions t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.candidate_id=t.candidate_id)",
        'defense_attempts': "SELECT t.* FROM public.defense_attempts t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.card_id AND c.candidate_id=t.candidate_id)",
        'reviews': "SELECT t.* FROM public.reviews t WHERE NOT EXISTS (SELECT 1 FROM cohort c JOIN public.defense_attempts a ON a.card_id=c.card_id AND a.candidate_id=c.candidate_id WHERE t.card_id=c.card_id AND t.source_kind='study' AND t.source_ref='defense:'||a.attempt_id)",
        'priority_repertoire_source_epochs': "SELECT (json_populate_record(t, json_build_object('version',t.version+(SELECT count(*) FROM cohort c WHERE c.repertoire_id=t.repertoire_id)+(SELECT count(*) FROM cohort c JOIN public.defense_attempts a ON a.card_id=c.card_id AND a.candidate_id=c.candidate_id JOIN public.reviews r ON r.card_id=c.card_id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id WHERE c.repertoire_id=t.repertoire_id)))).* FROM public.priority_repertoire_source_epochs t UNION ALL SELECT r.id AS repertoire_id, ((SELECT count(*) FROM cohort c WHERE c.repertoire_id=r.id)+(SELECT count(*) FROM cohort c JOIN public.defense_attempts a ON a.card_id=c.card_id AND a.candidate_id=c.candidate_id JOIN public.reviews v ON v.card_id=c.card_id AND v.source_kind='study' AND v.source_ref='defense:'||a.attempt_id WHERE c.repertoire_id=r.id))::bigint AS version FROM public.repertoires r WHERE EXISTS (SELECT 1 FROM cohort c WHERE c.repertoire_id=r.id) AND NOT EXISTS (SELECT 1 FROM public.priority_repertoire_source_epochs e WHERE e.repertoire_id=r.id)",
        'frozen_opening_runs': "SELECT (json_populate_record(t, CASE WHEN EXISTS (SELECT 1 FROM cohort) AND EXISTS (SELECT 1 FROM public.frozen_opening_pending p WHERE p.run_id=t.run_id) AND t.invalidated=0 THEN '{\"invalidated\":1}'::json ELSE '{}'::json END)).* FROM public.frozen_opening_runs t",
    }
    if table not in views:
        raise ValueError('unsupported Defense history projection table')
    query = sql.SQL('WITH cohort AS ({cohort}), projected AS ({projection}) SELECT row_to_json(t)::text FROM projected t ORDER BY {keys}').format(
        cohort=sql.SQL(cohort_sql), projection=sql.SQL(views[table]),
        keys=sql.SQL(',').join(sql.Identifier(key) for key in primary_key))
    with database.cursor(name='cut62_history_' + table) as cursor:
        cursor.execute(query, params)
        return _cut62_mac(secret, 'cut62:rows:' + table, (row[0] for row in cursor))


def _validate_cut62_history_binding(binding,selection,selection_sha256,expected_binding):
    import re
    _validate_cut62_attempt_binding(binding,selection,selection_sha256)
    if (not re.fullmatch('[0-9a-fA-F-]{36}', binding['attempt_id'])
            or not re.fullmatch(r'tempo-[0-9A-Za-z-]+\.dump',binding['backup_filename'])
            or not isinstance(expected_binding,dict) or binding != expected_binding):
        raise RuntimeError('Defense history attempt binding changed')
    return binding


def _cut62_history_session(database):
    """Fix name resolution and require normal trigger execution for history."""
    database.execute('SET LOCAL search_path=pg_catalog,public,pg_temp')
    if database.execute("SELECT current_setting('session_replication_role')").fetchone()[0] != 'origin':
        raise RuntimeError('Defense history requires origin replication role')


def _cut62_capture_history_v2(database, selection, binding, secret, selection_sha256, expected_binding):
    """Create the isolated combined original witness under the caller's fence."""
    if database.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise RuntimeError('Defense history capture requires an active transaction')
    if not isinstance(secret,bytes) or len(secret)!=32:
        raise ValueError('Defense history secret must be 32 bytes')
    _validate_cut62_history_binding(binding,selection,selection_sha256,expected_binding)
    _cut62_history_session(database)
    database.execute('SET LOCAL join_collapse_limit=1')
    database.execute('SET LOCAL jit=off')
    database.execute("SET LOCAL statement_timeout='30s'")
    _cut62_check_draft_history_selection(database,selection)
    _cut62_session(database)
    _cut62_check_history_catalog(database)
    for gate in (CUT62_DEFENSE_FK_LEDGER_SQL,CUT62_DEFENSE_ORIGIN_FK_SQL,
                 CUT62_DEFENSE_UPDATED_COLUMN_FK_SQL,CUT62_DEFENSE_TRIGGER_LEDGER_SQL,
                 CUT62_DEFENSE_HELPER_LEDGER_SQL,CUT62_DEFENSE_RULE_LEDGER_SQL):
        if not database.execute(gate).fetchone()[0]:
            raise RuntimeError('Defense history shared catalog changed')
    source = capture_cut62(database,secret)
    layout = source['layout']
    # The selector/count/group/reference digest was just checked in this
    # fenced repeatable-read transaction. Project from those exact bounded
    # approved identities instead of re-planning the full union per table.
    cohort = """SELECT c.id card_id,t.id candidate_id,c.repertoire_id
        FROM jsonb_array_elements(%s::jsonb) approved
        JOIN public.cards c ON c.id=approved->>0
        JOIN public.threat_training_candidates t ON t.id=approved->>1 AND t.card_id=c.id"""
    params=(json.dumps(selection['keys'],separators=(',',':')),)
    projected={name:_cut62_projected_history_table(database,name,layout[name][0],secret,cohort,params) for name in sorted(CUT62_HISTORY_PROJECTED_TABLES)}
    witness={'domain':'cut62-history-draft-v1','selection':selection,'binding':binding,
             'source':source,'projected':projected}
    witness['signature']=_cut62_mac(secret,'cut62:history-witness',[json.dumps(witness,sort_keys=True,separators=(',',':'))])['digest']
    return witness


def authenticate_cut62_history_v2(witness, secret, expected_binding):
    """Authenticate original identity and guard binding without database I/O."""
    import hmac
    if not isinstance(witness,dict) or set(witness) != {'domain','selection','binding','source','projected','signature'} or witness['domain'] != 'cut62-history-draft-v1':
        raise RuntimeError('unsupported Defense history witness')
    if not isinstance(secret,bytes) or len(secret)!=32:
        raise ValueError('Defense history secret must be 32 bytes')
    signed={key:witness[key] for key in ('domain','selection','binding','source','projected')}
    signature=_cut62_mac(secret,'cut62:history-witness',[json.dumps(signed,sort_keys=True,separators=(',',':'))])['digest']
    if not isinstance(witness['signature'],str) or not hmac.compare_digest(signature,witness['signature']):
        raise RuntimeError('Defense history witness authentication failed')
    if not isinstance(expected_binding,dict):
        raise RuntimeError('Defense history attempt binding changed')
    _validate_cut62_history_binding(witness['binding'],witness['selection'],expected_binding.get('selection_sha256'),expected_binding)
    validate_cut62_history_selection(witness['selection'])
    source=witness['source']
    if (not isinstance(source,dict) or (source.get('version'),source.get('from'),source.get('to')) != (CUT62_EVIDENCE_VERSION,61,62)
            or set(source.get('layout',{})) != CUT62_PROTECTED_TABLES | CUT62_RETIRED_TABLES | {CUT62_LEDGER}
            or set(source.get('rows',{})) != CUT62_PROTECTED_TABLES | CUT62_RETIRED_TABLES
            or set(witness.get('projected',{})) != CUT62_HISTORY_PROJECTED_TABLES):
        raise RuntimeError('Defense history witness coverage changed')
    return _cut62_mac(secret,'cut62:history-witness-id',[json.dumps(witness,sort_keys=True,separators=(',',':'))])['digest']


def save_cut62_history_v2(path,database,selection,binding,selection_sha256,expected_binding):
    """Persist one private original; never overwrite or silently rebaseline."""
    import secrets
    secret=secrets.token_bytes(32)
    witness=_cut62_capture_history_v2(database,selection,binding,secret,selection_sha256,expected_binding)
    witness_id=authenticate_cut62_history_v2(witness,secret,expected_binding)
    # Publish by an atomic no-replace hard link after syncing a private temp.
    # If parent sync fails after publication, keep the original: retry must
    # authenticate it or fail closed, never silently create a new witness.
    import tempfile
    directory_path=os.path.dirname(os.path.abspath(path))
    descriptor,temporary=tempfile.mkstemp(prefix='.cut62-history-',dir=directory_path)
    try:
        with os.fdopen(descriptor,'w') as stream:
            json.dump({'secret':secret.hex(),'witness':witness},stream,sort_keys=True,separators=(',',':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary,path,follow_symlinks=False)
        directory=os.open(directory_path,os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
    return witness_id


def load_cut62_history_v2(path,expected_binding):
    import stat
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(descriptor,'rb') as stream:
        metadata=os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise RuntimeError('Defense history witness must be a private regular file')
        raw=stream.read(64*1024*1024+1)
    if len(raw)>64*1024*1024:
        raise RuntimeError('Defense history witness exceeds size limit')
    def unique_object(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise RuntimeError('Defense history witness contains duplicate keys')
            result[key]=value
        return result
    payload=json.loads(raw,object_pairs_hook=unique_object)
    if not isinstance(payload,dict) or set(payload) != {'secret','witness'}:
        raise RuntimeError('invalid Defense history witness envelope')
    try:
        secret=bytes.fromhex(payload['secret'])
    except (TypeError,ValueError):
        raise RuntimeError('invalid Defense history witness secret') from None
    witness_id=authenticate_cut62_history_v2(payload['witness'],secret,expected_binding)
    return payload['witness'],secret,witness_id


def check_captured_cut62_history_v2(database,witness,secret,selection_sha256,expected_binding):
    """Recheck the original at schema 61 without replacing its file."""
    witness_id=authenticate_cut62_history_v2(witness,secret,expected_binding)
    actual=_cut62_capture_history_v2(database,witness['selection'],witness['binding'],secret,selection_sha256,expected_binding)
    if actual != witness:
        raise RuntimeError('Defense history source changed since original capture')
    return {'domain':witness['domain'],'observed_schema':61,'witness_id':witness_id}


def verify_cut62_history_v2(database,witness,secret,expected_binding):
    """Verify the signed projection after 062, inside the caller's transaction."""
    if database.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise RuntimeError('Defense history verification requires an active transaction')
    _cut62_history_session(database)
    if database.execute("SELECT current_setting('transaction_isolation'),current_setting('transaction_read_only')").fetchone() != ('repeatable read','off'):
        raise RuntimeError('Defense history verification requires repeatable read write transaction')
    witness_id=authenticate_cut62_history_v2(witness,secret,expected_binding)
    expected=dict(witness['source'])
    expected['rows']={**expected['rows'],**witness['projected']}
    verify_cut62(database,expected,secret)
    return {'domain':witness['domain'],'observed_schema':62,'witness_id':witness_id}


def apply_cut62_history_v2(database,witness,secret,expected_binding):
    """Run history, fixed 062, and verification in one rollback-safe savepoint.

    Caller must already own the exclusive writer/DDL fence and RR/write
    transaction and must bind its durable guard to the authenticated ID.
    """
    if database.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise RuntimeError('Defense history cut requires an active transaction')
    from database.tooling.apply_postgres_migrations import MIGRATIONS,migration_source
    with database.transaction():
        _cut62_draft_apply_history(database,witness,secret,expected_binding)
        database.execute(migration_source(MIGRATIONS/'062_three_journey_retirement.sql'),prepare=False)
        return verify_cut62_history_v2(database,witness,secret,expected_binding)


def _cut62_draft_apply_history(database, witness, secret, expected_binding):
    """Rollback the entire draft cut on any error, even if caller catches it."""
    # Check before creating the context: on an idle connection that context
    # would start and commit a transaction rather than create a savepoint.
    if database.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise RuntimeError('Defense history cut requires an active transaction')
    # The inner context becomes a savepoint within the required existing
    # transaction; an exception rolls back all mutation and trigger effects.
    with database.transaction():
        return _cut62_draft_apply_history_in_savepoint(database,witness,secret,expected_binding)


def _cut62_draft_apply_history_in_savepoint(database, witness, secret, expected_binding):
    """Draft only: verify a previously authenticated witness, mutate, reverify.

    The caller owns the stopped-writer/DDL fence, repeatable-read write
    transaction and final commit or rollback. This isolated candidate is
    not wired into the operational migration or its launcher.
    """
    from psycopg import sql
    authenticate_cut62_history_v2(witness,secret,expected_binding)
    selection = witness['selection']
    _cut62_history_session(database)
    database.execute('SET LOCAL join_collapse_limit=1')
    database.execute('SET LOCAL jit=off')
    database.execute("SET LOCAL statement_timeout='30s'")
    _cut62_check_draft_history_selection(database,selection)
    _cut62_session(database)
    _cut62_check_history_catalog(database)
    for gate in (CUT62_DEFENSE_FK_LEDGER_SQL, CUT62_DEFENSE_ORIGIN_FK_SQL,
                 CUT62_DEFENSE_UPDATED_COLUMN_FK_SQL, CUT62_DEFENSE_TRIGGER_LEDGER_SQL,
                 CUT62_DEFENSE_HELPER_LEDGER_SQL, CUT62_DEFENSE_RULE_LEDGER_SQL):
        if not database.execute(gate).fetchone()[0]:
            raise RuntimeError('Defense history shared catalog changed')
    if set(witness['projected']) != CUT62_HISTORY_PROJECTED_TABLES:
        raise RuntimeError('Defense history witness coverage changed')
    # Select once under the external fence; no additional card can join later.
    query = sql.SQL("""CREATE TEMP TABLE cut62_history_selected ON COMMIT DROP AS
      WITH ordinary AS ({ordinary}), historical AS ({historical}),
      eligible AS (SELECT card_id,candidate_id,repertoire_id,'no_activity'::text cohort_group FROM ordinary
                   UNION ALL SELECT card_id,candidate_id,repertoire_id,cohort_group FROM historical),
      approved AS (SELECT value->>0 card_id,value->>1 candidate_id,value->>2 cohort_group FROM jsonb_array_elements(%s::jsonb) value)
      SELECT e.* FROM eligible e JOIN approved a USING(card_id,candidate_id,cohort_group)""").format(
        ordinary=sql.SQL(CUT62_DRAFT_DEFENSE_SELECTOR),historical=sql.SQL(_cut62_draft_history_selector()))
    database.execute(query,(json.dumps(selection['keys'],separators=(',',':')),))
    selected = [list(row) for row in database.execute('SELECT card_id,candidate_id,cohort_group FROM pg_temp.cut62_history_selected ORDER BY card_id,candidate_id').fetchall()]
    if selected != selection['keys']:
        raise RuntimeError('approved Defense history keys changed')
    _cut62_check_draft_history_selection(database,selection)
    source = capture_cut62(database,secret)
    if source != witness['source']:
        raise RuntimeError('Defense history original source changed')
    layout = source['layout']
    cohort = 'SELECT card_id,candidate_id,repertoire_id FROM pg_temp.cut62_history_selected'
    for name in sorted(CUT62_HISTORY_PROJECTED_TABLES):
        if _cut62_projected_history_table(database,name,layout[name][0],secret,cohort) != witness['projected'][name]:
            raise RuntimeError('Defense history projection changed in ' + name)
    if selected:
        statements = (
            "UPDATE public.threat_training_candidates t SET card_id=NULL FROM pg_temp.cut62_history_selected c WHERE t.id=c.candidate_id AND t.card_id=c.card_id",
            "DELETE FROM public.reviews r USING public.defense_attempts a,pg_temp.cut62_history_selected c WHERE a.card_id=c.card_id AND a.candidate_id=c.candidate_id AND r.card_id=c.card_id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id",
            "DELETE FROM public.defense_recognition_submissions r USING pg_temp.cut62_history_selected c WHERE r.candidate_id=c.candidate_id",
            "DELETE FROM public.defense_attempts a USING pg_temp.cut62_history_selected c WHERE a.card_id=c.card_id AND a.candidate_id=c.candidate_id",
            "DELETE FROM public.daily_queue q USING pg_temp.cut62_history_selected c WHERE q.card_id=c.card_id",
            "DELETE FROM public.queue_attempt_origins o USING pg_temp.cut62_history_selected c WHERE o.card_id=c.card_id",
            "DELETE FROM public.cards t USING pg_temp.cut62_history_selected c WHERE t.id=c.card_id AND t.content_type='defense'",
        )
        for statement in statements:
            database.execute(statement)
    for name in sorted(CUT62_PROTECTED_TABLES):
        expected = witness['projected'].get(name, witness['source']['rows'][name])
        if _cut62_table(database,name,layout[name][0],secret) != expected:
            raise RuntimeError('Defense history postcondition changed in ' + name)
    if _cut62_catalog(database,secret) != witness['source']['catalog'] or _cut62_sequences(database,secret,True) != witness['source']['sequences']:
        raise RuntimeError('Defense history postcondition catalog or sequences changed')
    return {'selected_count':len(selected)}


def _cut62_catalog(database, secret):
    # Compare the remaining public catalog; exclude only explicitly retired
    # table-owned objects and the named migration-044 profile objects.
    excluded = sorted(CUT62_RETIRED_TABLES)
    rows = database.execute("""
        SELECT 'index', c.relname, pg_get_indexdef(c.oid)
        FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid
        JOIN pg_class owner ON owner.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT (owner.relname=ANY(%s))
          AND c.relname NOT IN ('idx_next_opponent_account_time','idx_next_opponent_speed_rating_time')
        UNION ALL
        SELECT 'constraint', owner.relname || '.' || con.conname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con JOIN pg_class owner ON owner.oid=con.conrelid
        JOIN pg_namespace n ON n.oid=owner.relnamespace
        WHERE n.nspname='public' AND NOT (owner.relname=ANY(%s))
        UNION ALL
        SELECT 'trigger', owner.relname || '.' || t.tgname, pg_get_triggerdef(t.oid)
        FROM pg_trigger t JOIN pg_class owner ON owner.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=owner.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal AND NOT (owner.relname=ANY(%s))
          AND NOT (owner.relname='imported_games' AND t.tgname='next_opponent_input_change')
        UNION ALL
        SELECT 'function', p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
               pg_get_functiondef(p.oid) || COALESCE(p.proacl::text,'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prokind='f'
          AND p.proname NOT IN ('invalidate_next_opponent_inputs','next_opponent_game_time','protect_next_opponent_snapshot')
        UNION ALL
        SELECT 'view', c.relname, pg_get_viewdef(c.oid,true) || COALESCE(c.relacl::text,'')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('v','m')
        UNION ALL
        SELECT 'grant', c.relname, COALESCE(c.relacl::text,'')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT (c.relname=ANY(%s))
        UNION ALL
        SELECT 'default', c.relname || '.' || a.attname, pg_get_expr(d.adbin,d.adrelid)
        FROM pg_attrdef d JOIN pg_class c ON c.oid=d.adrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=d.adnum
        WHERE n.nspname='public' AND NOT (c.relname=ANY(%s))
        ORDER BY 1,2,3
    """, (excluded, excluded, excluded, excluded, excluded))
    return _cut62_mac(secret, 'cut62:catalog',
                      (json.dumps(row, separators=(',', ':'), ensure_ascii=False) for row in rows))



def _cut62_sequences(database, secret, source):
    from psycopg import sql
    definitions = database.execute("""
        SELECT c.relname, pg_get_userbyid(c.relowner), COALESCE(c.relacl::text,''),
               s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_sequence s ON s.seqrelid=c.oid
        WHERE n.nspname='public' ORDER BY c.relname
    """).fetchall()
    expected = CUT62_PROTECTED_SEQUENCES | (CUT62_RETIRED_SEQUENCES if source else frozenset())
    if {row[0] for row in definitions} != expected:
        raise RuntimeError('cut62 sequence coverage changed')
    states = {'protected': [], 'retired': []}
    for definition in definitions:
        value = database.execute(sql.SQL('SELECT last_value,is_called FROM {}.{}').format(
            sql.Identifier('public'), sql.Identifier(definition[0]))).fetchone()
        category = 'retired' if definition[0] in CUT62_RETIRED_SEQUENCES else 'protected'
        states[category].append(json.dumps([*definition, *value], separators=(',', ':')))
    return {category: _cut62_mac(secret, 'cut62:sequences:' + category, values)
            for category, values in states.items()}

def _cut62_profile_objects(database, should_exist):
    for name in ('idx_next_opponent_account_time', 'idx_next_opponent_speed_rating_time'):
        found = database.execute('SELECT to_regclass(%s)', ('public.' + name,)).fetchone()[0] is not None
        if found != should_exist:
            raise RuntimeError('cut62 profile index state differs: ' + name)
    for name in ('invalidate_next_opponent_inputs()', 'next_opponent_game_time(text)', 'protect_next_opponent_snapshot()'):
        found = database.execute('SELECT to_regprocedure(%s)', ('public.' + name,)).fetchone()[0] is not None
        if found != should_exist:
            raise RuntimeError('cut62 profile function state differs: ' + name)
    found = database.execute("SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.imported_games'::regclass AND tgname='next_opponent_input_change' AND NOT tgisinternal)").fetchone()[0]
    if found != should_exist:
        raise RuntimeError('cut62 profile trigger state differs')

def _cut62_layout(database, expected_tables):
    layout = table_layout(database)
    if set(layout) != expected_tables:
        raise RuntimeError('cut62 table coverage changed')
    return {name: [list(key), [list(column) for column in columns]]
            for name, (key, columns) in layout.items()}


def _cut62_versions(database, last):
    versions = [row[0] for row in database.execute(
        'SELECT version FROM public.tempo_schema_migrations ORDER BY version')]
    if versions != list(range(1, last + 1)):
        raise RuntimeError('cut62 migration ledger is not contiguous')



def _cut62_session(database):
    database.execute("SET LOCAL TIME ZONE 'UTC'")
    database.execute("SET LOCAL DateStyle = 'ISO, YMD'")
    database.execute('SET LOCAL extra_float_digits = 3')

def capture_cut62(database, secret):
    """Capture the original schema-61 witness once, while all writers are stopped."""
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError('cut62 secret must be 32 bytes')
    _cut62_session(database)
    _cut62_versions(database, 61)
    _cut62_profile_objects(database, True)
    source_tables = CUT62_PROTECTED_TABLES | CUT62_RETIRED_TABLES | {CUT62_LEDGER}
    layout = _cut62_layout(database, source_tables)
    rows = {name: _cut62_table(database, name, layout[name][0], secret)
            for name in sorted(source_tables - {CUT62_LEDGER})}
    return {'version': CUT62_EVIDENCE_VERSION, 'from': 61, 'to': 62,
            'layout': layout, 'rows': rows, 'catalog': _cut62_catalog(database, secret), 'sequences': _cut62_sequences(database, secret, True)}



def cut62_witness_id(witness, secret):
    canonical = json.dumps(witness, sort_keys=True, separators=(',', ':'))
    return _cut62_mac(secret, 'cut62:witness-id', [canonical])['digest']


def check_captured_cut62(database, witness, secret):
    """Verify an existing original against unchanged schema 61; never rewrite it."""
    if (witness.get('version'), witness.get('from'), witness.get('to')) != (CUT62_EVIDENCE_VERSION, 61, 62):
        raise RuntimeError('unsupported cut62 witness')
    if capture_cut62(database, secret) != witness:
        raise RuntimeError('cut62 source changed since original capture')
    return {'version': CUT62_EVIDENCE_VERSION, 'from': 61, 'to': 62, 'unchanged': True, 'witness_id': cut62_witness_id(witness, secret)}

def verify_cut62(database, witness, secret):
    """Compare against the original witness; never capture a replacement after mutation."""
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError('cut62 secret must be 32 bytes')
    if (witness.get('version'), witness.get('from'), witness.get('to')) != (CUT62_EVIDENCE_VERSION, 61, 62):
        raise RuntimeError('unsupported cut62 witness')
    source_tables = CUT62_PROTECTED_TABLES | CUT62_RETIRED_TABLES | {CUT62_LEDGER}
    if set(witness.get('layout', {})) != source_tables or set(witness.get('rows', {})) != source_tables - {CUT62_LEDGER}:
        raise RuntimeError('cut62 witness coverage changed')
    _cut62_session(database)
    _cut62_versions(database, 62)
    _cut62_profile_objects(database, False)
    for name in sorted(CUT62_RETIRED_TABLES):
        if database.execute('SELECT to_regclass(%s)', ('public.' + name,)).fetchone()[0] is not None:
            raise RuntimeError('cut62 retired relation remains: ' + name)
    for name in sorted(CUT62_RETIRED_SEQUENCES):
        if database.execute('SELECT to_regclass(%s)', ('public.' + name,)).fetchone()[0] is not None:
            raise RuntimeError('cut62 retired sequence remains: ' + name)
    target = CUT62_PROTECTED_TABLES | {CUT62_LEDGER}
    layout = _cut62_layout(database, target)
    if layout != {name: witness['layout'][name] for name in target}:
        raise RuntimeError('cut62 retained table layout changed')
    if _cut62_sequences(database, secret, False)['protected'] != witness.get('sequences', {}).get('protected'):
        raise RuntimeError('cut62 retained sequences changed')
    if _cut62_catalog(database, secret) != witness.get('catalog'):
        raise RuntimeError('cut62 retained catalog changed')
    for name in sorted(CUT62_PROTECTED_TABLES):
        if _cut62_table(database, name, layout[name][0], secret) != witness['rows'][name]:
            raise RuntimeError('cut62 retained rows changed in ' + name)
    return {'version': CUT62_EVIDENCE_VERSION, 'from': 61, 'to': 62,
            'retired': {name: witness['rows'][name] for name in sorted(CUT62_RETIRED_TABLES)},
            'retired_sequences': witness['sequences']['retired'],
            'witness_id': cut62_witness_id(witness, secret)}


def save_cut62_witness(path, database):
    """Create, never overwrite, a private witness and random secret on the caller's volume."""
    import secrets
    secret = secrets.token_bytes(32)
    witness = capture_cut62(database, secret)
    signature = _cut62_mac(secret, 'cut62:witness', [json.dumps(witness, sort_keys=True, separators=(',', ':'))])['digest']
    payload = {'secret': secret.hex(), 'witness': witness, 'signature': signature}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(payload, stream, sort_keys=True, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # An incomplete file must never be treated as a valid original witness.
        os.unlink(path)
        raise
    return cut62_witness_id(witness, secret)


def load_cut62_witness(path):
    import stat
    metadata = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise RuntimeError('cut62 witness must be a private regular file')
    import hmac
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor) as stream:
        actual = os.fstat(stream.fileno())
        if not stat.S_ISREG(actual.st_mode) or actual.st_mode & 0o077:
            raise RuntimeError('cut62 witness must be a private regular file')
        payload = json.load(stream)
    if set(payload) != {'secret', 'witness', 'signature'}:
        raise RuntimeError('invalid cut62 witness envelope')
    secret = bytes.fromhex(payload['secret'])
    if len(secret) != 32:
        raise RuntimeError('invalid cut62 witness secret')
    expected = _cut62_mac(secret, 'cut62:witness', [json.dumps(payload['witness'], sort_keys=True, separators=(',', ':'))])['digest']
    if not hmac.compare_digest(expected, payload['signature']):
        raise RuntimeError('cut62 witness authentication failed')
    return payload['witness'], secret

# Independent witness domain for the source schema before migrations 060/061.
PRE62_EVIDENCE_VERSION = 1
PRE62_NEW_TABLE = 'legacy_content_historical_origins'
PRE62_SOURCE_TABLES = (CUT62_PROTECTED_TABLES | CUT62_RETIRED_TABLES | {CUT62_LEDGER}) - {PRE62_NEW_TABLE}
PRE62_FUNCTIONS = {
    'reject_position_first_response_mutation': ('study_position_first_response_immutable', 60),
    'reject_content_historical_origin_mutation': ('legacy_content_historical_origin_immutable', 61),
}
PRE62_TRIGGERS = {
    'study_position_first_response_immutable_row': ('study_position_first_responses', 'reject_position_first_response_mutation', 27, 60),
    'study_position_first_response_immutable_truncate': ('study_position_first_responses', 'reject_position_first_response_mutation', 34, 60),
    'content_historical_origin_immutable_row': ('legacy_content_historical_origins', 'reject_content_historical_origin_mutation', 27, 61),
    'content_historical_origin_immutable_truncate': ('legacy_content_historical_origins', 'reject_content_historical_origin_mutation', 34, 61),
}


def _pre62_table(database, table, primary_key, secret):
    from psycopg import sql
    query = sql.SQL('SELECT row_to_json(t)::text FROM {}.{} t ORDER BY {}').format(
        sql.Identifier('public'), sql.Identifier(table),
        sql.SQL(',').join(sql.Identifier(key) for key in primary_key))
    with database.cursor(name='pre62_' + table) as cursor:
        cursor.execute(query)
        return _cut62_mac(secret, 'pre62:rows:' + table, (row[0] for row in cursor))


def _pre62_sequences(database, secret):
    from psycopg import sql
    definitions = database.execute("""SELECT c.relname, pg_get_userbyid(c.relowner), COALESCE(c.relacl::text,''),
      s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_sequence s ON s.seqrelid=c.oid
      WHERE n.nspname='public' ORDER BY c.relname""").fetchall()
    if {row[0] for row in definitions} != CUT62_PROTECTED_SEQUENCES | CUT62_RETIRED_SEQUENCES:
        raise RuntimeError('pre62 sequence coverage changed')
    values = []
    for row in definitions:
        state = database.execute(sql.SQL('SELECT last_value,is_called FROM {}.{}').format(
            sql.Identifier('public'), sql.Identifier(row[0]))).fetchone()
        values.append(json.dumps([*row, *state], separators=(',', ':')))
    return _cut62_mac(secret, 'pre62:sequences', values)


def _pre62_catalog(database, secret):
    rows = database.execute("""
      SELECT 'index', c.relname, pg_get_indexdef(c.oid) || ':' || i.indisvalid::text || ':' || i.indisready::text
      FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid JOIN pg_class owner ON owner.oid=i.indrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND owner.relname<>%s
      UNION ALL
      SELECT 'constraint', owner.relname || '.' || con.conname, pg_get_constraintdef(con.oid)
      FROM pg_constraint con JOIN pg_class owner ON owner.oid=con.conrelid JOIN pg_namespace n ON n.oid=owner.relnamespace
      WHERE n.nspname='public' AND owner.relname<>%s
      UNION ALL
      SELECT 'trigger', owner.relname || '.' || t.tgname, pg_get_triggerdef(t.oid) || ':' || t.tgenabled::text
      FROM pg_trigger t JOIN pg_class owner ON owner.oid=t.tgrelid JOIN pg_namespace n ON n.oid=owner.relnamespace
      WHERE n.nspname='public' AND NOT t.tgisinternal AND owner.relname<>%s AND NOT (t.tgname=ANY(%s))
      UNION ALL
      SELECT 'function', p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
             pg_get_functiondef(p.oid) || ':' || pg_get_userbyid(p.proowner) || ':' || COALESCE(p.proacl::text,'')
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.prokind='f' AND NOT (p.proname=ANY(%s))
      UNION ALL
      SELECT 'view', c.relname, pg_get_viewdef(c.oid,true) || COALESCE(c.relacl::text,'')
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('v','m')
      UNION ALL
      SELECT 'grant', c.relname, pg_get_userbyid(c.relowner) || ':' || COALESCE(c.relacl::text,'') || ':' || c.relrowsecurity::text || ':' || c.relforcerowsecurity::text
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname<>%s
      UNION ALL
      SELECT 'defaultacl', pg_get_userbyid(d.defaclrole) || ':' || d.defaclnamespace::text || ':' || d.defaclobjtype::text, d.defaclacl::text FROM pg_default_acl d
      UNION ALL
      SELECT 'default', c.relname || '.' || a.attname, pg_get_expr(d.adbin,d.adrelid)
      FROM pg_attrdef d JOIN pg_class c ON c.oid=d.adrelid JOIN pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=d.adnum
      WHERE n.nspname='public' AND c.relname<>%s ORDER BY 1,2,3
    """, (PRE62_NEW_TABLE, PRE62_NEW_TABLE, PRE62_NEW_TABLE, list(PRE62_TRIGGERS),
           list(PRE62_FUNCTIONS), PRE62_NEW_TABLE, PRE62_NEW_TABLE))
    return _cut62_mac(secret, 'pre62:catalog',
        (json.dumps(row, separators=(',', ':'), ensure_ascii=False) for row in rows))



def _pre62_default_table_acl(database):
    # Effective new-table grants are the owner/global default plus the public
    # schema's additive default grants. Compare normalized ACL entries.
    return database.execute("""
      SELECT grantor,grantee,privilege_type,is_grantable FROM aclexplode(COALESCE(
        (SELECT defaclacl FROM pg_default_acl WHERE defaclrole=current_user::regrole
         AND defaclnamespace=0 AND defaclobjtype='r'), acldefault('r',current_user::regrole)))
      UNION
      SELECT a.grantor,a.grantee,a.privilege_type,a.is_grantable
      FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
      WHERE d.defaclrole=current_user::regrole AND d.defaclnamespace='public'::regnamespace AND d.defaclobjtype='r'
      ORDER BY 1,2,3,4
    """).fetchall()

def _pre62_added_objects(database, version, owner, expected_acl):
    functions = database.execute("""SELECT p.proname, pg_get_userbyid(p.proowner), p.prosrc,
         p.proconfig, p.prosecdef, p.provolatile, pg_get_function_identity_arguments(p.oid), p.prorettype::regtype::text, l.lanname, p.proacl IS NULL
       FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
       WHERE n.nspname='public' AND p.proname=ANY(%s) ORDER BY 1""", (list(PRE62_FUNCTIONS),)).fetchall()
    expected_funcs = {name for name, (_, introduced) in PRE62_FUNCTIONS.items() if version >= introduced}
    if {row[0] for row in functions} != expected_funcs or len(functions) != len(expected_funcs):
        raise RuntimeError('pre62 immutable function inventory changed')
    for name, actual_owner, body, config, security, volatility, args, result, language, default_acl in functions:
        message = PRE62_FUNCTIONS[name][0]
        expected_body = "BEGIN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = '" + message + "'; END;"
        if (actual_owner != owner or ''.join(body.split()).rstrip(';') != ''.join(expected_body.split()).rstrip(';')
                or config != ['search_path=pg_catalog'] or security or volatility != 'v'
                or args != '' or result != 'trigger' or language != 'plpgsql' or not default_acl):
            raise RuntimeError('pre62 immutable function definition changed')
    triggers = database.execute("""SELECT t.tgname, c.relname, p.proname, t.tgtype::int, t.tgenabled,
             n2.nspname,t.tgnargs,t.tgqual IS NULL,t.tgconstraint=0 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
       JOIN pg_namespace n2 ON n2.oid=p.pronamespace
       WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgname=ANY(%s) ORDER BY 1""", (list(PRE62_TRIGGERS),)).fetchall()
    expected_triggers = {name for name, (_, _, _, introduced) in PRE62_TRIGGERS.items() if version >= introduced}
    if {row[0] for row in triggers} != expected_triggers or len(triggers) != len(expected_triggers):
        raise RuntimeError('pre62 immutable trigger inventory changed')
    for name, table, function, kind, enabled, namespace, nargs, no_condition, no_constraint in triggers:
        expected_table, expected_function, expected_kind, _ = PRE62_TRIGGERS[name]
        if (table, function, kind, enabled, namespace, nargs, no_condition, no_constraint) != (expected_table, expected_function, expected_kind, 'O', 'public', 0, True, True):
            raise RuntimeError('pre62 immutable trigger definition changed')
    exists = database.execute('SELECT to_regclass(%s)', ('public.' + PRE62_NEW_TABLE,)).fetchone()[0] is not None
    if exists != (version >= 61):
        raise RuntimeError('pre62 historical origin table state changed')
    if version >= 61:
        relation = database.execute("SELECT c.relkind,pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=%s", (PRE62_NEW_TABLE,)).fetchone()
        if relation != ('r',owner,False,False):
            raise RuntimeError('pre62 historical origin owner or kind changed')
        acl = database.execute("SELECT a.grantor,a.grantee,a.privilege_type,a.is_grantable FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a WHERE c.oid=%s::regclass ORDER BY 1,2,3,4", ('public.' + PRE62_NEW_TABLE,)).fetchall()
        if [list(row) for row in acl] != expected_acl:
            raise RuntimeError('pre62 historical origin ACL changed')
        layout = table_layout(database)[PRE62_NEW_TABLE]
        expected_columns = (('legacy_target_id','text',True),('legacy_workspace_id','text',True),
                            ('export_id','text',True),('receipt_id','text',True),
                            ('payload','bytea',True),('receipt_digest','text',True))
        if layout != (('legacy_target_id','legacy_workspace_id','export_id'), expected_columns):
            raise RuntimeError('pre62 historical origin layout changed')
        if database.execute('SELECT EXISTS(SELECT 1 FROM public.legacy_content_historical_origins)').fetchone()[0]:
            raise RuntimeError('pre62 historical origin table is not empty')
        indexes = database.execute("SELECT x.indexname,i.indisvalid,i.indisready FROM pg_indexes x JOIN pg_class c ON c.relname=x.indexname JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname=x.schemaname JOIN pg_index i ON i.indexrelid=c.oid WHERE x.schemaname='public' AND x.tablename=%s ORDER BY 1", (PRE62_NEW_TABLE,)).fetchall()
        if indexes != [('legacy_content_historical_origins_pkey',True,True),('legacy_content_historical_origins_receipt_id_key',True,True)]:
            raise RuntimeError('pre62 historical origin index inventory changed')
        constraints = database.execute("SELECT contype,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype<>'n' ORDER BY 1,2", ('public.' + PRE62_NEW_TABLE,)).fetchall()
        if constraints != [('p','PRIMARY KEY (legacy_target_id, legacy_workspace_id, export_id)'),('u','UNIQUE (receipt_id)')]:
            raise RuntimeError('pre62 historical origin constraint inventory changed')
        defaults = database.execute("SELECT count(*) FROM pg_attrdef WHERE adrelid=%s::regclass", ('public.' + PRE62_NEW_TABLE,)).fetchone()[0]
        if defaults:
            raise RuntimeError('pre62 historical origin unexpected default')
        unique = database.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='u' ORDER BY 1", ('public.' + PRE62_NEW_TABLE,)).fetchall()
        if unique != [('UNIQUE (receipt_id)',)]:
            raise RuntimeError('pre62 historical origin unique constraint changed')
        extra_triggers = database.execute("SELECT tgname FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal AND NOT (tgname=ANY(%s))", ('public.' + PRE62_NEW_TABLE, list(PRE62_TRIGGERS))).fetchall()
        if extra_triggers:
            raise RuntimeError('pre62 unexpected historical origin trigger')


def _pre62_session(database):
    database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    _cut62_session(database)


def capture_pre62_59(database, secret):
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError('pre62 secret must be 32 bytes')
    _pre62_session(database)
    _cut62_versions(database, 59)
    owner = database.execute('SELECT current_user').fetchone()[0]
    expected_acl = [list(row) for row in _pre62_default_table_acl(database)]
    _pre62_added_objects(database, 59, owner, expected_acl)
    layout = _cut62_layout(database, PRE62_SOURCE_TABLES)
    rows = {name: _pre62_table(database, name, layout[name][0], secret)
            for name in sorted(PRE62_SOURCE_TABLES - {CUT62_LEDGER})}
    return {'domain':'pre62-source-59','version':PRE62_EVIDENCE_VERSION,'from':59,'through':61,
            'owner':owner,'new_table_acl':expected_acl,'layout':layout,'rows':rows,
            'catalog':_pre62_catalog(database,secret),'sequences':_pre62_sequences(database,secret)}



def authenticate_pre62_59(witness, secret):
    """Identify a valid original file without asserting it was verified at 61."""
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError('pre62 secret must be 32 bytes')
    if (witness.get('domain'),witness.get('version'),witness.get('from'),witness.get('through')) != ('pre62-source-59',PRE62_EVIDENCE_VERSION,59,61):
        raise RuntimeError('unsupported pre62 witness')
    if set(witness.get('layout',{})) != PRE62_SOURCE_TABLES or set(witness.get('rows',{})) != PRE62_SOURCE_TABLES - {CUT62_LEDGER}:
        raise RuntimeError('pre62 witness coverage changed')
    for name in ('owner','new_table_acl','catalog','sequences'):
        if name not in witness:
            raise RuntimeError('pre62 witness incomplete')
    canonical=json.dumps(witness,sort_keys=True,separators=(',',':'))
    return _cut62_mac(secret,'pre62:witness-id',[canonical])['digest']

def verify_pre62_59(database, witness, secret, *, in_write_transaction=False):
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError('pre62 secret must be 32 bytes')
    if (witness.get('domain'),witness.get('version'),witness.get('from'),witness.get('through')) != ('pre62-source-59',PRE62_EVIDENCE_VERSION,59,61):
        raise RuntimeError('unsupported pre62 witness')
    if set(witness.get('layout',{})) != PRE62_SOURCE_TABLES or set(witness.get('rows',{})) != PRE62_SOURCE_TABLES - {CUT62_LEDGER}:
        raise RuntimeError('pre62 witness coverage changed')
    witness_id = authenticate_pre62_59(witness,secret)
    if in_write_transaction:
        # Reject implicit transactions and autocommit before issuing a query.
        if database.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
            raise RuntimeError('pre62 write verification requires an active transaction')
        # The caller must already hold its stopped-writer and DDL fence.
        # Never change an established migration transaction's access mode.
        isolation, read_only = database.execute("SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only')").fetchone()
        if (isolation, read_only) != ('repeatable read', 'off'):
            raise RuntimeError('pre62 write verification requires repeatable read write transaction')
        _cut62_session(database)
    else:
        _pre62_session(database)
    version = database.execute('SELECT COALESCE(max(version),0) FROM public.tempo_schema_migrations').fetchone()[0]
    if version not in (59,60,61):
        raise RuntimeError('unsupported pre62 target version')
    _cut62_versions(database, version)
    if database.execute('SELECT current_user').fetchone()[0] != witness['owner']:
        raise RuntimeError('pre62 migration owner changed')
    if [list(row) for row in _pre62_default_table_acl(database)] != witness.get('new_table_acl'):
        raise RuntimeError('pre62 default table ACL changed')
    _pre62_added_objects(database, version, witness['owner'], witness['new_table_acl'])
    expected_tables = PRE62_SOURCE_TABLES | ({PRE62_NEW_TABLE} if version >= 61 else set())
    layout = _cut62_layout(database, expected_tables)
    if any(layout[name] != witness['layout'][name] for name in PRE62_SOURCE_TABLES):
        raise RuntimeError('pre62 retained table layout changed')
    if _pre62_catalog(database,secret) != witness['catalog']:
        raise RuntimeError('pre62 retained catalog changed')
    if _pre62_sequences(database,secret) != witness['sequences']:
        raise RuntimeError('pre62 retained sequences changed')
    for name in sorted(PRE62_SOURCE_TABLES - {CUT62_LEDGER}):
        if _pre62_table(database,name,layout[name][0],secret) != witness['rows'][name]:
            raise RuntimeError('pre62 retained rows changed in ' + name)
    return {'domain':'pre62-source-59','version':PRE62_EVIDENCE_VERSION,'observed_schema':version,'unchanged':True,'witness_id':witness_id}


def save_pre62_59(path, database):
    import secrets
    secret = secrets.token_bytes(32)
    witness = capture_pre62_59(database, secret)
    witness_id = authenticate_pre62_59(witness,secret)
    signature = _cut62_mac(secret,'pre62:witness',[json.dumps(witness,sort_keys=True,separators=(',',':'))])['digest']
    descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(descriptor,'w') as stream:
            json.dump({'secret':secret.hex(),'witness':witness,'signature':signature},stream,sort_keys=True,separators=(',',':'))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        os.unlink(path)
        raise
    return witness_id


def load_pre62_59(path):
    import hmac
    import stat
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(descriptor) as stream:
        metadata=os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise RuntimeError('pre62 witness must be a private regular file')
        payload=json.load(stream)
    if set(payload) != {'secret','witness','signature'}:
        raise RuntimeError('invalid pre62 witness envelope')
    secret=bytes.fromhex(payload['secret'])
    if len(secret)!=32:
        raise RuntimeError('invalid pre62 witness secret')
    expected=_cut62_mac(secret,'pre62:witness',[json.dumps(payload['witness'],sort_keys=True,separators=(',',':'))])['digest']
    if not hmac.compare_digest(expected,payload['signature']):
        raise RuntimeError('pre62 witness authentication failed')
    return payload['witness'],secret

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", help="Previously captured fingerprints, without row contents")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--pre62-capture59', metavar='PRIVATE_WITNESS_PATH')
    group.add_argument('--pre62-verify59', metavar='PRIVATE_WITNESS_PATH')
    group.add_argument('--pre62-authenticate59', metavar='PRIVATE_WITNESS_PATH')
    group.add_argument('--cut62-capture', metavar='PRIVATE_WITNESS_PATH')
    group.add_argument('--cut62-verify', metavar='PRIVATE_WITNESS_PATH')
    group.add_argument('--cut62-check-captured', metavar='PRIVATE_WITNESS_PATH')
    args = parser.parse_args()
    if (args.pre62_capture59 or args.pre62_verify59 or args.pre62_authenticate59 or args.cut62_capture or args.cut62_verify or args.cut62_check_captured) and args.expected:
        parser.error('--expected cannot be combined with a cut62 operation')
    if args.pre62_authenticate59:
        witness,secret=load_pre62_59(args.pre62_authenticate59)
        print(json.dumps({'domain':'pre62-source-59','version':PRE62_EVIDENCE_VERSION,'authenticated':True,'witness_id':authenticate_pre62_59(witness,secret)}))
        return
    if args.pre62_capture59 or args.pre62_verify59:
        with psycopg.connect(os.environ['TEMPO_POSTGRES_ADMIN_URL'],
                             options='-c default_transaction_read_only=on') as database:
            if args.pre62_capture59:
                witness_id=save_pre62_59(args.pre62_capture59,database)
                print(json.dumps({'domain':'pre62-source-59','version':PRE62_EVIDENCE_VERSION,'captured':True,'witness_id':witness_id}))
            else:
                witness,secret=load_pre62_59(args.pre62_verify59)
                print(json.dumps(verify_pre62_59(database,witness,secret)))
        return
    if args.cut62_capture or args.cut62_verify or args.cut62_check_captured:
        with psycopg.connect(os.environ['TEMPO_POSTGRES_ADMIN_URL'],
                             options='-c default_transaction_read_only=on') as database:
            if args.cut62_capture:
                witness_id = save_cut62_witness(args.cut62_capture, database)
                print(json.dumps({'version': CUT62_EVIDENCE_VERSION, 'from': 61, 'to': 62, 'captured': True, 'witness_id': witness_id}))
            elif args.cut62_check_captured:
                witness, secret = load_cut62_witness(args.cut62_check_captured)
                print(json.dumps(check_captured_cut62(database, witness, secret)))
            else:
                witness, secret = load_cut62_witness(args.cut62_verify)
                print(json.dumps(verify_cut62(database, witness, secret)))
        return
    expected = json.loads(args.expected) if args.expected else None
    with psycopg.connect(os.environ["TEMPO_POSTGRES_ADMIN_URL"],
                         options="-c default_transaction_read_only=on") as database:
        actual = study_fingerprints(database, expected)
    if expected and actual != expected:
        raise RuntimeError("Schema upgrade changed review, queue, or operation receipt history; keep writers stopped")
    print(json.dumps(actual))


if __name__ == "__main__":
    main()
