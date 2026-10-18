from __future__ import annotations
from .postgres_store import PostgresConnection
from .durable_tasks import enqueue_task_in_transaction
from .review_service import preserve_daily_queue_order
from typing import Any


def presentation_rows(database, day):
    """Runtime adapter supplies the same explicit facts to the pure mixer."""
    statement = """SELECT q.id,q.card_id,q.position,c.content_type,
                  q.admission_kind,
                  q.gameplay_priority_reason,q.cycle,q.attempt_state,q.attempt_failed,
                  (q.review_result_json IS NOT NULL) review_completed,c.revision,
                  EXISTS(SELECT 1 FROM queue_attempt_origins origin
                    WHERE origin.queue_entry_id=q.id AND origin.card_id=c.id
                      AND origin.revision=c.revision AND origin.issued_as_head=1) issued,
                  EXISTS(SELECT 1 FROM opening_evidence_attempts attempt
                    WHERE attempt.queue_entry_id=q.id) presented,
                  EXISTS(SELECT 1 FROM teaching_states taught WHERE taught.card_id=c.id) taught
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=? AND q.status='queued' ORDER BY q.id"""
    rows = [dict(row) for row in database.execute(statement, (day,)).fetchall()]
    if not rows:
        return rows
    placeholders = ','.join('?' for _ in rows)
    reviewed = {row[0] for row in database.execute(
        "SELECT DISTINCT card_id FROM reviews WHERE invalidated_at IS NULL AND card_id IN (" + placeholders + ")",
        tuple(row['card_id'] for row in rows),
    ).fetchall()}
    eligible = {row[0] for row in database.execute(available_attempt_sql() + " AND q.id IN (" + placeholders + ")",
                                                 (day, *(row['id'] for row in rows))).fetchall()}
    for row in rows:
        row['admission_kind'] = ('explicit' if row['admission_kind']=='explicit'
                                 else 'review' if row.get('cycle',0)>0 or row['card_id'] in reviewed else 'new')
        row['eligible'] = row['id'] in eligible
    return rows


def requeue(db, day, cid, after, attempt):
    from .review_service import capture_daily_queue_order
    previous_order = capture_daily_queue_order(db, day)
    cycle = db.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM ("
        "SELECT cycle FROM daily_queue WHERE queue_date=? AND card_id=? UNION ALL "
        "SELECT cycle FROM queue_attempt_origins WHERE queue_date=? AND card_id=?) cycles",
        (day, cid, day, cid),
    ).fetchone()[0]
    if after is None:
        position = db.execute(
            "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
            (day,),
        ).fetchone()[0]
    else:
        row = db.execute(
            available_attempt_sql().replace(
                "SELECT q.id FROM", "SELECT q.position FROM", 1
            )
            + " ORDER BY q.position,q.id LIMIT 1 OFFSET ?",
            (day, max(0, after)),
        ).fetchone()
        position = (
            row[0]
            if row
            else db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
                (day,),
            ).fetchone()[0]
        )
        db.execute(
            "UPDATE daily_queue SET position=position+1 WHERE queue_date=? AND status='queued' AND position>=?",
            (day, position),
        )
    inserted_entry_id = db.execute(
        "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) "
        "VALUES(?,?,?,?,?) RETURNING id",
        (day, cid, cycle, position, attempt),
    ).fetchone()[0]
    db.execute(
        """UPDATE daily_queue SET
               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),
               admission_kind='review'
           WHERE id=?""",
        (inserted_entry_id,),
    )
    preserve_daily_queue_order(db, day, previous=previous_order)
    return inserted_entry_id


def request_queue_refresh_in_transaction(
    database: PostgresConnection,
    queue_date: str,
    *,
    preserve_through_entry_id: int | None = None,
) -> dict[str, Any]:
    """Checkpoint a foreground mutation and its follow-up queue refresh together."""

    payload = {"queue_date": queue_date}
    if preserve_through_entry_id is not None:
        payload["preserve_through_entry_id"] = preserve_through_entry_id
    task = enqueue_task_in_transaction(
        database,
        "daily_queue",
        "current",
        payload,
        priority=10,
    )
    from .remediation_adapter import request_remediation
    request_remediation(database, queue_date)
    database.execute(
        _QUEUE_REFRESH_PROJECTION_SQL,
        (queue_date,),
    )
    return task


_QUEUE_REFRESH_PROJECTION_SQL = """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
           VALUES(?,'refreshing',0,1) ON CONFLICT(queue_date) DO UPDATE SET
           state='refreshing',refresh_pending=1,last_error=NULL"""


_AVAILABLE_ATTEMPT_SQL = """SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id
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


def available_attempt_sql() -> str:
    # Frozen publications are PostgreSQL-authoritative. The optional legacy
    # exposure sources do not exist in SQLite's recovery compatibility schema.
    return _AVAILABLE_ATTEMPT_SQL + " AND c.serving_active=1"


from . import frozen_opening_order

_AVAILABLE_ATTEMPT_SQL += (
    frozen_opening_order.stale_queue_predicate()
    + frozen_opening_order.legacy_queue_predicate()
)
