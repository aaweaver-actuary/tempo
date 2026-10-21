"""Fixed queue-attempt availability SQL; semantic miss reason remains caller-owned."""

from tempo_legacy_database.adapters import frozen_order, postgres_store


_AVAILABLE_ATTEMPT_BASE_SQL = """SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id
            WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
              AND c.pending_validation=0 AND c.superseded_by IS NULL
              AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=c.id)
              AND (c.content_type!='opening' OR EXISTS(
                  SELECT 1 FROM repertoires repertoire
                  WHERE (repertoire.id=c.repertoire_id OR EXISTS(
                      SELECT 1 FROM repertoire_cards link
                      WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))
                    AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks blocked
                        WHERE blocked.card_id=c.id AND blocked.repertoire_id=repertoire.id)))
              AND (c.content_type!='defense' OR
                   (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
              AND (c.content_type!='study_exercise' OR EXISTS(
                  SELECT 1 FROM study_exercises exercise JOIN studies study ON study.id=exercise.study_id
                  WHERE exercise.id=c.study_exercise_id AND exercise.status='published' AND study.archived=0
                    AND (q.admission_kind='explicit' OR NOT EXISTS(
                        SELECT 1 FROM study_sibling_burials burial
                        WHERE burial.exercise_id=exercise.id AND burial.study_day=q.queue_date))))"""


def base_available_attempt_sql(miss_reason: str) -> str:
    return _AVAILABLE_ATTEMPT_BASE_SQL + frozen_order.stale_queue_predicate(miss_reason)


def available_attempt_sql(miss_reason: str) -> str:
    # Frozen publications are PostgreSQL-authoritative. The optional legacy
    # exposure sources do not exist in SQLite's recovery compatibility schema.
    if postgres_store.configured():
        return base_available_attempt_sql(miss_reason) + frozen_order.legacy_queue_predicate(miss_reason)
    return base_available_attempt_sql(miss_reason)
