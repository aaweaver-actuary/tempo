"""Read-only omission of one narrowly certified, unissuable retained carrier.

This is queue eligibility, never source/issuance authority. It retains the row
and its explicit hold; a successor must still pass the existing full issuer.
Unknown certificates, bindings, state, or protected work cannot be omitted.
"""

RETAINED_HOLD = 'position_retained_carrier_without_logical_state'

# Caller aliases are the shared availability query's fixed q (queue), c (card).
# No locks, state writes, connection ownership, JSON-supplied SQL or new clock.
# The completed seed manifest already certified the authored definition. Exact
# current header/integrity equality prevents borrowing it across source changes.
_OMIT = """c.content_type='opening' AND q.status='queued'
 AND q.admission_kind='review' AND q.cycle=0
 AND q.attempt_state='clean' AND q.attempt_failed=0
 AND q.review_result_json IS NULL AND q.admission_source IS NULL
 AND COALESCE(q.gameplay_priority_reason,'')=''
 AND COALESCE(c.guided_review,0)=0 AND COALESCE(c.reinforcement_pending,0)=0
 AND EXISTS (
   SELECT 1 FROM queue_attempt_origins origin
   JOIN study_position_owners owner ON owner.repertoire_id=origin.admission_repertoire_id
   JOIN study_position_epochs epoch ON epoch.repertoire_id=owner.repertoire_id AND epoch.epoch=owner.epoch
   JOIN study_position_seed_runs seeded ON seeded.repertoire_id=owner.repertoire_id
      AND seeded.owner_epoch=owner.epoch AND seeded.source_digest=epoch.source_digest
   JOIN repertoires repertoire ON repertoire.id=owner.repertoire_id
   JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
   JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
   JOIN background_tasks graph_task ON graph_task.kind='opening_graph_rebuild'
      AND graph_task.deduplication_key=repertoire.id
   JOIN background_tasks scan_task ON scan_task.kind='integrity_scan'
      AND scan_task.deduplication_key=repertoire.id
   JOIN legacy_learning_identity identity ON identity.id=1
   JOIN opening_graph_steps graph ON graph.repertoire_id=repertoire.id
      AND graph.generation=publication.generation AND graph.card_id=c.id
   JOIN repertoire_lines line ON line.id=graph.line_id AND line.repertoire_id=repertoire.id
   JOIN study_position_seed_outcomes outcome ON outcome.repertoire_id=owner.repertoire_id
      AND outcome.owner_epoch=owner.epoch AND outcome.variant='standard'
      AND outcome.position=graph.decision_fen_key
   WHERE origin.queue_entry_id=q.id AND origin.card_id=c.id AND origin.revision=c.revision
     AND origin.queue_date=q.queue_date AND origin.cycle=q.cycle
     AND origin.admission_kind='review' AND origin.admission_repertoire_id=q.admission_repertoire_id
     AND origin.issued_as_head=0 AND origin.attempt_failed=0
     AND origin.review_result_json IS NULL AND origin.last_status='queued'
     AND origin.start_fen=c.start_fen AND origin.moves_json=c.moves_json
     AND origin.trained_color=c.trained_color AND origin.content_type='opening'
     AND owner.mode='active' AND seeded.status='complete' AND seeded.completed_at IS NOT NULL
     AND seeded.seeded_count+seeded.held_count=seeded.manifest_count
     AND seeded.source_token_json->>'authored_digest'=epoch.source_digest
     AND seeded.source_token_json->>'repertoire_id'=repertoire.id
     AND seeded.source_token_json->'header'=jsonb_build_object(
       'id',repertoire.id,'scope_source_revision',repertoire.scope_source_revision,
       'canonical_prefix_revision',repertoire.canonical_prefix_revision,
       'canonical_prefix_moves_json',repertoire.canonical_prefix_moves_json,
       'graph_generation',publication.generation,'graph_state',publication.state,
       'workspace_id',identity.workspace_id)
     AND seeded.source_token_json->'integrity'=jsonb_build_object(
       'integrity_status',integrity.status,'scan_status',integrity.scan_status,
       'scan_generation',integrity.scan_generation,'checked_at',integrity.checked_at,
       'scan_completed_sources',integrity.scan_completed_sources,
       'scan_total_sources',integrity.scan_total_sources,'scan_error',integrity.scan_error,
       'graph_task_generation',graph_task.generation,'graph_task_state',graph_task.state,
       'scan_task_id',scan_task.id,'scan_task_generation',scan_task.generation,
       'scan_task_state',scan_task.state)
     AND publication.state='ready' AND graph_task.state='complete'
     AND graph_task.generation=publication.generation
     AND integrity.status IN ('clean','needs_repair') AND integrity.scan_status='idle'
     AND integrity.scan_error IS NULL AND integrity.scan_completed_sources=integrity.scan_total_sources
     AND integrity.scan_generation=scan_task.id||':'||scan_task.generation::text
     AND scan_task.state='complete'
     AND jsonb_extract_path_text(CASE WHEN scan_task.payload_json IS JSON OBJECT
       THEN scan_task.payload_json::jsonb ELSE '{}'::jsonb END,'repertoire_id')=repertoire.id
     AND jsonb_extract_path(CASE WHEN scan_task.payload_json IS JSON OBJECT
       THEN scan_task.payload_json::jsonb ELSE '{}'::jsonb END,'graph_generation')=to_jsonb(publication.generation)
     AND EXISTS(SELECT 1 FROM repertoire_cards membership
       WHERE membership.repertoire_id=repertoire.id AND membership.card_id=c.id)
     AND NOT EXISTS(SELECT 1 FROM repertoire_cards other
       WHERE other.card_id=c.id AND other.repertoire_id<>repertoire.id)
     AND (c.repertoire_id IS NULL OR c.repertoire_id=repertoire.id)
     AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks blocked
       WHERE blocked.repertoire_id=repertoire.id AND blocked.card_id=c.id)
     AND graph.starting_fen=c.start_fen AND graph.moves_json=c.moves_json
     AND graph.trained_color=c.trained_color AND line.trained_color=c.trained_color
     AND c.trained_color IN ('white','black')
     AND (SELECT count(*) FROM opening_graph_steps alias
       WHERE alias.repertoire_id=repertoire.id AND alias.generation=publication.generation
         AND alias.card_id=c.id)=1
     AND outcome.disposition='hold' AND outcome.hold_reason='no_valid_current_introduced_donor'
     AND NOT EXISTS(SELECT 1 FROM study_position_states state
       WHERE state.repertoire_id=repertoire.id AND state.variant='standard'
         AND state.position=outcome.position)
     AND CASE WHEN c.moves_json IS JSON ARRAY THEN
       CASE WHEN split_part(c.start_fen,' ',2)=CASE c.trained_color WHEN 'white' THEN 'w' ELSE 'b' END
         THEN jsonb_array_length(c.moves_json::jsonb) IN (1,2)
           AND c.moves_json::jsonb->>0=outcome.expected_uci
         ELSE split_part(c.start_fen,' ',2) IN ('w','b')
           AND jsonb_array_length(c.moves_json::jsonb) IN (2,3)
           AND c.moves_json::jsonb->>1=outcome.expected_uci END
       ELSE FALSE END
 )
 AND NOT EXISTS(SELECT 1 FROM queue_attempt_origins exposed
   WHERE exposed.queue_entry_id=q.id AND (exposed.issued_as_head=1 OR exposed.attempt_failed<>0
     OR exposed.review_result_json IS NOT NULL OR exposed.last_status='complete'))
 AND NOT EXISTS(SELECT 1 FROM study_position_issued_plans plan WHERE plan.queue_entry_id=q.id)
 AND NOT EXISTS(SELECT 1 FROM opening_evidence_attempts attempt WHERE attempt.queue_entry_id=q.id)
 AND NOT EXISTS(SELECT 1 FROM review_attempt_receipts receipt WHERE receipt.queue_entry_id=q.id)
 AND NOT EXISTS(SELECT 1 FROM teaching_states teaching WHERE teaching.card_id=c.id)
 AND NOT EXISTS(SELECT 1 FROM study_game_feedback effect WHERE effect.status IN ('pending','held','applied')
   AND (effect.card_id=c.id OR
     jsonb_extract_path_text(CASE WHEN effect.payload_json IS JSON OBJECT THEN effect.payload_json::jsonb
       ELSE '{}'::jsonb END,'owner','progress','target','card_id')=c.id))"""


def omission_predicate() -> str:
    """Fixed PostgreSQL predicate shared by serving, writer and diagnostics."""
    return _OMIT


def availability_predicate() -> str:
    return ' AND NOT (' + _OMIT + ')'


def retained_holds(database, day):
    """Keep omitted physical rows inspectable without changing their status."""
    return database.execute('SELECT c.id card_id,? message FROM daily_queue q '
        'JOIN cards c ON c.id=q.card_id WHERE q.queue_date=? AND (' + _OMIT + ') '
        'ORDER BY q.position,q.id', (RETAINED_HOLD, day)).fetchall()
