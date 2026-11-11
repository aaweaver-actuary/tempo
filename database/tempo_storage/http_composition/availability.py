"""Fixed queue-attempt availability SQL; semantic miss reason remains caller-owned."""

from tempo_storage import frozen_order, postgres


# Applied only when selecting or creating a new issuance. Retained attempt
# reads deliberately keep their original availability and receipt semantics.
RETIRED_QUEUE_PREDICATE = """ AND c.content_type NOT IN ('defense','endgame')
    AND substr(COALESCE(q.admission_source,''),1,8) != 'defense:'
    AND substr(COALESCE(q.admission_source,''),1,8) != 'endgame:'"""


_AVAILABLE_ATTEMPT_BASE_SQL = "SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id\n            WHERE q.queue_date=%s AND q.status='queued' AND c.archived=0\n              AND c.pending_validation=0 AND c.superseded_by IS NULL\n              AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=c.id)\n              AND (c.content_type!='opening' OR EXISTS(\n                  SELECT 1 FROM repertoires repertoire\n                  WHERE (repertoire.id=c.repertoire_id OR EXISTS(\n                      SELECT 1 FROM repertoire_cards link\n                      WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))\n                    AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks blocked\n                        WHERE blocked.card_id=c.id AND blocked.repertoire_id=repertoire.id)))\n              AND (c.content_type!='defense' OR\n                   (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)\n              AND (c.content_type!='study_exercise' OR EXISTS(\n                  SELECT 1 FROM study_exercises exercise JOIN studies study ON study.id=exercise.study_id\n                  WHERE exercise.id=c.study_exercise_id AND exercise.status='published' AND study.archived=0\n                    AND (q.admission_kind='explicit' OR NOT EXISTS(\n                        SELECT 1 FROM study_sibling_burials burial\n                        WHERE burial.exercise_id=exercise.id AND burial.study_day=q.queue_date))))"


def base_available_attempt_sql(miss_reason: str) -> str:
    return _AVAILABLE_ATTEMPT_BASE_SQL + frozen_order.stale_queue_predicate()


def available_attempt_sql(miss_reason: str) -> str:
    from tempo_storage.http_composition.retained_position_availability import availability_predicate
    return (base_available_attempt_sql(miss_reason) + frozen_order.legacy_queue_predicate()
            + availability_predicate())
