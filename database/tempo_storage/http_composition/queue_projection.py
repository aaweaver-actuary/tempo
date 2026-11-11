"""Complete queue HTTP projections and entry writes on the caller transaction."""
from collections.abc import Callable
from tempo_storage import frozen_order, postgres
from tempo_storage.http_composition import availability

def _interactive_read_profile(database):
    database.execute_native("SELECT set_config('jit', 'off', true)")

def issued_head(database, entry_id, card_id, revision):
    return database.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id=%s AND revision=%s', (entry_id, card_id, revision)).fetchone()

def evidence_timezone(database):
    return database.execute('SELECT timezone FROM settings WHERE id=1').fetchone()

def queue_projection(database, day: str):
    return database.execute('SELECT * FROM queue_projections WHERE queue_date=%s', (day,)).fetchone()

def queue_diagnostics(database, day: str):
    rows = database.execute('SELECT card_id,message FROM queue_projection_diagnostics WHERE queue_date=%s ORDER BY card_id', (day,)).fetchall()
    rows = [*rows, *database.execute("SELECT DISTINCT c.id AS card_id, 'Retired Defense or Endgames attempt held' AS message\n           FROM daily_queue q JOIN cards c ON c.id=q.card_id\n           WHERE q.queue_date=%s AND q.status IN ('queued','blocked') AND NOT (1=1 " + availability.RETIRED_QUEUE_PREDICATE + ')', (day,)).fetchall()]
    from tempo_storage.http_composition.retained_position_availability import retained_holds
    rows = [*rows, *retained_holds(database, day)]
    return rows

def queue_rows(database, day: str, limit: int | None, *, miss_reason: str):
    _interactive_read_profile(database)
    queue_sql = 'SELECT q.id queue_entry_id,q.position,q.cycle,q.attempt_state,q.attempt_failed,\n                                  (SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=c.id AND invalidated_at IS NULL) latest_review_id,\n                                  q.gameplay_priority_reason,q.admission_kind,\n                                  CASE WHEN c.kind=\'prefix\'\n                                              AND position(\'"again"\' in c.recent_attempts_json)>0\n                                    THEN COALESCE((SELECT MAX(failed_review.id) FROM reviews failed_review\n                                      WHERE failed_review.card_id=c.id AND failed_review.rating=\'again\'\n                                        AND failed_review.source_kind IN (\'study\',\'game_lapse\')\n                                        AND failed_review.invalidated_at IS NULL),0)\n                                    ELSE 0 END latest_failed_review_id,\n                                  EXISTS(SELECT 1 FROM reviews study_review\n                                         WHERE study_review.card_id=c.id AND study_review.source_kind=\'study\'\n                                           AND study_review.invalidated_at IS NULL) has_study_review,\n                                  (SELECT COUNT(*) FROM reviews study_review_count\n                                   WHERE study_review_count.card_id=c.id\n                                     AND study_review_count.source_kind=\'study\'\n                                     AND study_review_count.invalidated_at IS NULL) study_review_count,\n                                  COALESCE(q.admission_source,\n                                    (SELECT \'defense:\' || candidate.id\n                                     FROM threat_training_candidates candidate\n                                     WHERE candidate.card_id=c.id LIMIT 1)) admission_source,\n                                  c.id,c.repertoire_id,c.kind,c.start_fen,c.moves_json,c.state,c.due_date,\n                                  c.interval_days,c.ease,c.repetitions,c.lapses,c.unlock_after_card_id,\n                                  c.fsrs_card_json,c.first_correct_at,c.reinforcement_pending,c.stability,\n                                  c.guided_review,c.maximum_interval,c.content_type,c.scheduling_mode,\n                                  c.hard_correct_streak,c.recent_attempts_json,c.archived,c.superseded_by,\n                                  c.source_ref,c.source_fen,c.revision,c.introduced_at,c.trained_color,\n                                  c.pending_validation,c.prefix_split_rejected_after_review_id,\n                                  c.study_exercise_id,c.canonical_route_source,\n                                  COALESCE(r.name,study.title,\'Study\') repertoire_name,\n                                  COALESCE(r.source_name,\'Study\') repertoire_source,\n                                  COALESCE(r.is_main,0) is_main,study.id study_id,\n                                  COALESCE(c.trained_color,(SELECT e.trained_color FROM endgame_templates e WHERE e.card_id=c.id), (SELECT l.trained_color FROM repertoire_lines l WHERE l.repertoire_id=r.id ORDER BY l.created_at LIMIT 1)) effective_trained_color\n                           FROM daily_queue q JOIN cards c ON c.id=q.card_id\n                           LEFT JOIN repertoires r ON r.id=COALESCE(\n                               (SELECT linked.id FROM repertoires linked\n                                WHERE ((linked.id=c.repertoire_id AND EXISTS(\n                                    SELECT 1 FROM repertoire_cards primary_link\n                                    WHERE primary_link.repertoire_id=linked.id)) OR EXISTS(\n                                    SELECT 1 FROM repertoire_cards rc\n                                    WHERE rc.card_id=c.id AND rc.repertoire_id=linked.id))\n                                  AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block\n                                                 WHERE block.repertoire_id=linked.id AND block.card_id=c.id)\n                                ORDER BY linked.is_main DESC,linked.created_at DESC LIMIT 1),\n                               c.repertoire_id)\n                           LEFT JOIN study_exercises exercise ON exercise.id=c.study_exercise_id\n                           LEFT JOIN studies study ON study.id=exercise.study_id\n                           WHERE q.queue_date=%s AND q.status=\'queued\' AND c.archived=0\n                             AND c.superseded_by IS NULL\n                             AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=c.id)\n                             ' + availability.RETIRED_QUEUE_PREDICATE + "\n                             AND COALESCE(c.pending_validation,0)=0\n                             AND (c.content_type!='study_exercise' OR\n                                  (exercise.status='published' AND study.archived=0\n                                   AND (q.admission_kind='explicit' OR NOT EXISTS(\n                                     SELECT 1 FROM study_sibling_burials burial\n                                     WHERE burial.exercise_id=exercise.id AND burial.study_day=q.queue_date))))\n                             AND (c.content_type!='opening' OR EXISTS(\n                                 SELECT 1 FROM repertoires repertoire\n                                 WHERE (repertoire.id=c.repertoire_id OR EXISTS(\n                                     SELECT 1 FROM repertoire_cards link\n                                     WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))\n                                   AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block\n                                       WHERE block.repertoire_id=repertoire.id AND block.card_id=c.id)))\n                           ORDER BY q.position,q.id"
    queue_sql = queue_sql.replace('ORDER BY q.position,q.id', frozen_order.stale_queue_predicate() + frozen_order.legacy_queue_predicate() + ' ORDER BY q.position,q.id')
    original_lookup = queue_sql[queue_sql.index('LEFT JOIN repertoires r ON'):queue_sql.index('LEFT JOIN study_exercises exercise')]
    repertoire_lookup = 'LEFT JOIN ranked_repertoires choice ON choice.card_id=c.id AND choice.rank=1 LEFT JOIN repertoires r ON r.id=COALESCE(choice.repertoire_id,c.repertoire_id) '
    candidate_window = availability.available_attempt_sql(miss_reason).replace('SELECT q.id FROM', 'SELECT q.* FROM', 1)
    candidate_window += availability.RETIRED_QUEUE_PREDICATE
    candidate_window += ' ORDER BY q.position,q.id' + (' LIMIT %s' if limit is not None else '')
    queue_sql = 'WITH active_queue AS MATERIALIZED (' + candidate_window + '), candidate_repertoires AS MATERIALIZED (SELECT c.id card_id,linked.id repertoire_id,linked.is_main,linked.created_at FROM active_queue queue_card JOIN cards c ON c.id=queue_card.card_id JOIN repertoire_cards rc ON rc.card_id=c.id JOIN repertoires linked ON linked.id=rc.repertoire_id WHERE NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.repertoire_id=linked.id AND block.card_id=c.id) UNION SELECT c.id card_id,linked.id repertoire_id,linked.is_main,linked.created_at FROM active_queue queue_card JOIN cards c ON c.id=queue_card.card_id JOIN repertoires linked ON linked.id=c.repertoire_id WHERE EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.repertoire_id=linked.id) AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.repertoire_id=linked.id AND block.card_id=c.id)), ranked_repertoires AS MATERIALIZED (SELECT card_id,repertoire_id,ROW_NUMBER() OVER (PARTITION BY card_id ORDER BY is_main DESC,created_at DESC) rank FROM candidate_repertoires) ' + queue_sql.replace(original_lookup, repertoire_lookup).replace('FROM daily_queue q JOIN cards c', 'FROM active_queue q JOIN cards c').replace("WHERE q.queue_date=%s AND q.status='queued' AND c.archived=0", 'WHERE c.archived=0')
    return database.execute(queue_sql + (' LIMIT %s' if limit is not None else ''), (day, limit, limit) if limit is not None else (day, limit) if limit is not None else (day,)).fetchall()

def encounter_badges(database, cards, badge_cutoff):
    """Advisory batch projection on an exclusively owned short read."""
    database.execute_native("SELECT set_config('statement_timeout', '100ms', true)")
    database.execute_native("SELECT set_config('lock_timeout', '25ms', true)")
    keys = sorted({' '.join(card['start_fen'].split()[:4]) for card in cards})
    result = {}
    for offset in range(0, len(keys), 400):
        result.update({row['fen_key']: dict(row) for row in encounter_rows(database, keys[offset:offset + 400], badge_cutoff)})
    return result

def encounter_rows(database, fen_key_batch, badge_cutoff):
    placeholders = ','.join(('%s' for _ in fen_key_batch))
    return database.execute(f'SELECT occurrence.fen_key,\n                           COUNT(DISTINCT occurrence.game_id) encounter_count,\n                           MAX(game.played_at) last_seen_at\n                    FROM game_position_occurrences occurrence\n                    JOIN imported_games game ON game.id=occurrence.game_id\n                    WHERE occurrence.fen_key IN ({placeholders})\n                      AND game.played_at>=%s AND game.adaptive_excluded=0\n                    GROUP BY occurrence.fen_key', (*fen_key_batch, badge_cutoff)).fetchall()

def available_count(database, day: str, *, miss_reason: str):
    _interactive_read_profile(database)
    return database.execute('SELECT COUNT(*) FROM (' + availability.available_attempt_sql(miss_reason) + availability.RETIRED_QUEUE_PREDICATE + ') eligible_queue', (day,)).fetchone()[0]

def exercise_revision(database, exercise_id):
    return database.execute('SELECT exercise.current_revision,revision.specification_json\n                       FROM study_exercises exercise JOIN study_exercise_revisions revision\n                         ON revision.exercise_id=exercise.id AND revision.revision=exercise.current_revision\n                       WHERE exercise.id=%s', (exercise_id,)).fetchone()

def entry_state(database, entry_id: int):
    return database.execute('SELECT queue_date,status,attempt_failed FROM daily_queue WHERE id=%s', (entry_id,)).fetchone()

def available_head(database, current_day: Callable[[], str], *, miss_reason: str):
    return database.execute(availability.available_attempt_sql(miss_reason) + ' ORDER BY q.position,q.id LIMIT 1', (current_day(),)).fetchone()

def retained_entry_issued(database, entry_id, original_day, *, miss_reason):
    return database.execute(availability.available_attempt_sql(miss_reason) + '\n        AND q.id=%s AND EXISTS(SELECT 1 FROM queue_attempt_origins origin\n          WHERE origin.queue_entry_id=q.id AND origin.card_id=c.id\n            AND origin.revision=c.revision AND origin.issued_as_head=1)', (original_day, entry_id)).fetchone()

def mark_entry_failed(database, entry_id: int):
    return database.execute("UPDATE daily_queue SET attempt_failed=1 WHERE id=%s AND status='queued'\n               AND ((SELECT content_type FROM cards WHERE id=card_id)!='defense'\n                    OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)", (entry_id,)).rowcount

def bury_entry(database, day: str, entry_id: int) -> None:
    database.execute("UPDATE daily_queue SET status='buried'\n               WHERE queue_date=%s AND status!='complete'\n                 AND card_id=(SELECT card_id FROM daily_queue WHERE id=%s)", (day, entry_id))

def position_pending_modes(database, cards):
    """Mark position-owned prefetch rows as held until actually issued."""
    ids = [card['queue_entry_id'] for card in cards if card['content_type'] == 'opening']
    if not ids:
        return {}
    rows = database.execute_native("SELECT q.id, EXISTS(\n        SELECT 1 FROM study_position_owners owner\n        WHERE owner.mode IN ('active','staged','paused') AND\n          (owner.repertoire_id=q.admission_repertoire_id OR\n           (q.admission_repertoire_id IS NULL AND (owner.repertoire_id=c.repertoire_id OR\n             EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=c.id\n                 AND rc.repertoire_id=owner.repertoire_id))))) position_owned\n        FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.id=ANY(%s::bigint[])", (ids,)).fetchall()
    return {row['id']: bool(row['position_owned']) for row in rows}

def position_source_revision(database, repertoire_id):
    """Cheap cache invalidation hint; source owner still fences all facts."""
    return database.execute_native('SELECT r.scope_source_revision,r.canonical_prefix_revision,\n        p.generation,p.state FROM repertoires r JOIN opening_graph_publications p\n          ON p.repertoire_id=r.id WHERE r.id=%s', (repertoire_id,)).fetchone()
