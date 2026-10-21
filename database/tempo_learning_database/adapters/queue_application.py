"""Learning queue facts and bound persistence on the caller's transaction."""
from . import frozen_order
from .postgres_store import PostgresConnection


_QUEUE_REFRESH_PROJECTION_SQL = """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
           VALUES(?,'refreshing',0,1) ON CONFLICT(queue_date) DO UPDATE SET
           state='refreshing',refresh_pending=1,last_error=NULL"""


_AVAILABLE_ATTEMPT_WHERE_SQL = """WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
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


def _available_attempt_where_sql(miss_reason):
    return (_AVAILABLE_ATTEMPT_WHERE_SQL
            + frozen_order.stale_queue_predicate(miss_reason)
            + frozen_order.legacy_queue_predicate(miss_reason)
            + " AND c.serving_active=1")


def available_attempt_sql(miss_reason) -> str:
    # Frozen publications are PostgreSQL-authoritative. The optional legacy
    # exposure sources do not exist in SQLite's recovery compatibility schema.
    return ("SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id "
            + _available_attempt_where_sql(miss_reason))


def presentation_facts(database, day, miss_reason):
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
        return rows, set(), set()
    placeholders = ','.join('?' for _ in rows)
    reviewed = {row[0] for row in database.execute(
        "SELECT DISTINCT card_id FROM reviews WHERE invalidated_at IS NULL AND card_id IN (" + placeholders + ")",
        tuple(row['card_id'] for row in rows),
    ).fetchall()}
    eligible = {row[0] for row in database.execute(available_attempt_sql(miss_reason) + " AND q.id IN (" + placeholders + ")",
                                                 (day, *(row['id'] for row in rows))).fetchall()}
    return rows, reviewed, eligible


def next_cycle(database, day, card_id):
    return database.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM ("
        "SELECT cycle FROM daily_queue WHERE queue_date=? AND card_id=? UNION ALL "
        "SELECT cycle FROM queue_attempt_origins WHERE queue_date=? AND card_id=?) cycles",
        (day, card_id, day, card_id),
    ).fetchone()[0]


def next_position(database, day):
    return database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
        (day,),
    ).fetchone()[0]


def available_position_at_offset(database, day, after, miss_reason):
    return database.execute(
        available_attempt_sql(miss_reason).replace(
            "SELECT q.id FROM", "SELECT q.position FROM", 1
        )
        + " ORDER BY q.position,q.id LIMIT 1 OFFSET ?",
        (day, after),
    ).fetchone()


def shift_queued_positions(database, day, position):
    database.execute(
        "UPDATE daily_queue SET position=position+1 WHERE queue_date=? AND status='queued' AND position>=?",
        (day, position),
    )


def insert_repeated_entry(database, day, card_id, cycle, position, attempt):
    inserted_entry_id = database.execute(
        "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) "
        "VALUES(?,?,?,?,?) RETURNING id",
        (day, card_id, cycle, position, attempt),
    ).fetchone()[0]
    database.execute(
        """UPDATE daily_queue SET
               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),
               admission_kind='review'
           WHERE id=?""",
        (inserted_entry_id,),
    )
    return inserted_entry_id


def mark_queue_refresh_pending_in_transaction(
    database: PostgresConnection, queue_date: str,
) -> None:
    """Mark the projection last, borrowing the caller's refresh transaction."""
    database.execute(_QUEUE_REFRESH_PROJECTION_SQL, (queue_date,))


def window_rows(database, queue_date, limit, miss_reason):
    statement = (
        "SELECT q.id AS queue_entry_id,q.cycle,q.attempt_failed,q.position,"
        "q.attempt_state,q.admission_kind,q.admission_source,q.gameplay_priority_reason,"
        "c.*,COALESCE(r.name,study.title,'Learning') AS repertoire_name,"
        "COALESCE(r.source_name,'accepted-publication') AS repertoire_source,exercise.study_id "
        "FROM daily_queue q JOIN cards c ON c.id=q.card_id "
        "LEFT JOIN repertoires r ON r.id=c.repertoire_id "
        "LEFT JOIN study_exercises exercise ON exercise.id=c.study_exercise_id "
        "LEFT JOIN studies study ON study.id=exercise.study_id "
        + _available_attempt_where_sql(miss_reason)
        + " ORDER BY q.position,q.id LIMIT ?"
    )
    return database.execute(statement, (queue_date, limit)).fetchall()


def latest_review_id(database, card_id):
    return database.execute(
        "SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=? AND invalidated_at IS NULL",
        (card_id,),
    ).fetchone()[0]


def accepted_defense(database, card_id, revision):
    return database.execute(
        "SELECT candidate_id FROM accepted_defense_exercises WHERE card_id=? AND exercise_revision=?",
        (card_id, revision)).fetchone()


def study_specification(database, exercise_id, revision):
    return database.execute(
        "SELECT specification_json FROM study_exercise_revisions WHERE exercise_id=? AND revision=?",
        (exercise_id, revision),
    ).fetchone()


def projection(database, queue_date):
    return database.execute(
        "SELECT * FROM queue_projections WHERE queue_date=?", (queue_date,)
    ).fetchone()


def available_count(database, queue_date, miss_reason):
    return database.execute(
        "SELECT COUNT(*) FROM (" + available_attempt_sql(miss_reason) + ") available",
        (queue_date,),
    ).fetchone()[0]
