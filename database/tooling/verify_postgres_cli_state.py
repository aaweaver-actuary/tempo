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


# Draft only: static typed ownership predicates need catalog and owner review.
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
AND EXISTS (SELECT 1 FROM public.priority_repertoire_source_epochs e WHERE e.repertoire_id=c.repertoire_id)
AND NOT EXISTS (SELECT 1 FROM public.threat_training_candidates other WHERE other.card_id=c.id AND other.id<>t.id)
AND EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id)
AND EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.content_type='defense')
AND NOT EXISTS (SELECT 1 FROM public.defense_attempts a WHERE a.card_id=c.id OR a.candidate_id=t.id)
AND NOT EXISTS (SELECT 1 FROM public.defense_recognition_submissions r WHERE r.candidate_id=t.id)
AND NOT EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND (q.status NOT IN ('queued','blocked') OR q.review_result_json IS NOT NULL))
AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.review_result_json IS NOT NULL)
AND NOT EXISTS (SELECT 1 FROM typed_receipts r WHERE
 r.payload->>'card_id'=c.id OR r.payload#>>'{review,prepared_next,card_id}'=c.id OR r.response->>'card_id'=c.id OR
 EXISTS (SELECT 1 FROM public.daily_queue q WHERE q.card_id=c.id AND q.id::text IN
 (r.payload->>'entry_id',r.payload->>'queue_entry_id',r.payload#>>'{review,queue_entry_id}',r.payload#>>'{review,prepared_next,queue_entry_id}',r.response->>'entry_id',r.response->>'queue_entry_id')))
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

def _cut62_defense_reference_digest(database, keys):
    """Return only the count and digest of exact original approved references."""
    from psycopg import sql
    query = sql.SQL("""
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
    """).format(selector=sql.SQL(CUT62_DRAFT_DEFENSE_SELECTOR))
    return database.execute(query, (json.dumps(keys, separators=(',', ':')),)).fetchone()


CUT62_DEFENSE_PROJECTED_TABLES = frozenset(('cards', 'daily_queue', 'queue_attempt_origins', 'threat_training_candidates', 'priority_repertoire_source_epochs', 'frozen_opening_runs'))

# Draft projection primitive; the reviewed fixed selector and migration wiring
# are still required before this can be used for a cutover witness.
def _cut62_projected_defense_table(database, table, primary_key, secret, cohort_sql):
    from psycopg import sql
    views = {
        'cards': "SELECT t.* FROM public.cards t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.id)",
        'daily_queue': "SELECT t.* FROM public.daily_queue t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.card_id)",
        'queue_attempt_origins': "SELECT t.* FROM public.queue_attempt_origins t WHERE NOT EXISTS (SELECT 1 FROM cohort c WHERE c.card_id=t.card_id)",
        'threat_training_candidates': "SELECT (json_populate_record(t, CASE WHEN EXISTS (SELECT 1 FROM cohort c WHERE c.candidate_id=t.id) THEN '{\"card_id\":null}'::json ELSE '{}'::json END)).* FROM public.threat_training_candidates t",
        'priority_repertoire_source_epochs': "SELECT (json_populate_record(t, json_build_object('version',t.version+(SELECT count(*) FROM cohort c WHERE c.repertoire_id=t.repertoire_id)))).* FROM public.priority_repertoire_source_epochs t",
        'frozen_opening_runs': "SELECT (json_populate_record(t, CASE WHEN EXISTS (SELECT 1 FROM cohort) AND EXISTS (SELECT 1 FROM public.frozen_opening_pending p WHERE p.run_id=t.run_id) AND t.invalidated=0 THEN '{\"invalidated\":1}'::json ELSE '{}'::json END)).* FROM public.frozen_opening_runs t",
    }
    if table not in views:
        raise ValueError('unsupported Defense projection table')
    query = sql.SQL('WITH cohort AS ({cohort}), projected AS ({projection}) SELECT row_to_json(t)::text FROM projected t ORDER BY {keys}').format(
        cohort=sql.SQL(cohort_sql), projection=sql.SQL(views[table]),
        keys=sql.SQL(',').join(sql.Identifier(key) for key in primary_key))
    with database.cursor(name='cut62_projected_' + table) as cursor:
        cursor.execute(query)
        return _cut62_mac(secret, 'cut62:rows:' + table, (row[0] for row in cursor))


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

def verify_pre62_59(database, witness, secret):
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError('pre62 secret must be 32 bytes')
    if (witness.get('domain'),witness.get('version'),witness.get('from'),witness.get('through')) != ('pre62-source-59',PRE62_EVIDENCE_VERSION,59,61):
        raise RuntimeError('unsupported pre62 witness')
    if set(witness.get('layout',{})) != PRE62_SOURCE_TABLES or set(witness.get('rows',{})) != PRE62_SOURCE_TABLES - {CUT62_LEDGER}:
        raise RuntimeError('pre62 witness coverage changed')
    witness_id = authenticate_pre62_59(witness,secret)
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
