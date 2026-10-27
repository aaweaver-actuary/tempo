"""Existing queue presentation, quarantine and projection operations."""
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.http_composition.availability import available_attempt_sql


_QUARANTINE_OPENING_ELIGIBILITY = """q.queue_date=%s AND q.status='queued'
    AND c.archived=0 AND c.content_type='opening'
    AND EXISTS(SELECT 1 FROM repertoires repertoire
               WHERE (repertoire.id=c.repertoire_id OR EXISTS(
                   SELECT 1 FROM repertoire_cards link
                   WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))
                 AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                WHERE block.repertoire_id=repertoire.id AND block.card_id=c.id))"""


_PROJECTION_BLOCKED_COUNT_SQL = """SELECT COUNT(*) FROM (
    SELECT queue.card_id FROM daily_queue queue
    WHERE queue.queue_date=%s AND queue.status='blocked'
    UNION
    SELECT card.id FROM cards card
    WHERE card.content_type='opening' AND card.archived=0
      AND card.state IN ('learning','mature') AND card.due_date<=%s
      AND EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                 WHERE block.card_id=card.id
                   AND (block.repertoire_id=card.repertoire_id OR EXISTS(
                       SELECT 1 FROM repertoire_cards link
                       WHERE link.card_id=card.id
                         AND link.repertoire_id=block.repertoire_id)))
) blocked"""


def saved_plan(database, queue_date):
    return database.execute(
            "SELECT seed,membership_hash FROM daily_queue_days WHERE queue_date=?", (queue_date,),
        ).fetchone()


def current_membership(database, queue_date):
    return database.execute_native(
        "SELECT id,card_id FROM daily_queue WHERE queue_date=%s AND status='queued' ORDER BY id",
        (queue_date,),
    ).fetchall()


def locked_membership_hash(database, queue_date):
    return database.execute_native(
        "SELECT membership_hash FROM daily_queue_days WHERE queue_date=%s FOR UPDATE",
        (queue_date,),
    ).fetchone()


def apply_plan(database, entry_ids, positions, buckets, kinds, queue_date):
    return database.execute_native(
        """UPDATE daily_queue AS queue
           SET position=planned.position,card_bucket=planned.bucket,
               admission_kind=planned.kind
           FROM unnest(%s::bigint[],%s::bigint[],%s::text[],%s::text[])
                AS planned(id,position,bucket,kind)
           WHERE queue.id=planned.id AND queue.queue_date=%s AND queue.status='queued'""",
        (entry_ids, positions,
         buckets, kinds,
         queue_date),
    ).rowcount


def save_plan(database, queue_date, seed, membership_hash, generated_at):
    return database.execute_native(
        """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
           VALUES(%s,%s,%s,%s) ON CONFLICT(queue_date) DO UPDATE SET
           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
        (queue_date, seed, membership_hash,
         generated_at),
    )


def locked_quarantine_card(database, queue_date, queue_entry_id, card_id):
    return database.execute_native(
        """SELECT c.start_fen,c.moves_json,
                  COALESCE(c.trained_color,(SELECT line.trained_color FROM repertoire_lines line
                           WHERE line.repertoire_id=c.repertoire_id
                           ORDER BY line.created_at LIMIT 1)) trained_color
           FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE """
        + _QUARANTINE_OPENING_ELIGIBILITY +
        " AND q.id=%s AND c.id=%s FOR UPDATE OF q,c",
        (queue_date, queue_entry_id, card_id),
    ).fetchone()


def lock_incomplete_card(database, card_id):
    return database.execute_native(
        "UPDATE cards SET state='locked' WHERE id=%s", (card_id,),
    )


def skip_incomplete_entry(database, queue_entry_id):
    return database.execute_native(
        "UPDATE daily_queue SET status='skipped' WHERE id=%s",
        (queue_entry_id,),
    )


def record_incomplete_diagnostic(database, queue_date, card_id, message):
    return database.execute_native(
        """INSERT INTO queue_projection_diagnostics(queue_date,card_id,message)
           VALUES(%s,%s,%s) ON CONFLICT(queue_date,card_id) DO UPDATE SET
           message=excluded.message""",
        (queue_date, card_id,
         message),
    )


def publish_projection(database, queue_date, blocked_count, updated_at):
    return database.execute_native(
                """INSERT INTO queue_projections(queue_date,state,generation,
                                                   refresh_pending,last_error,
                                                   blocked_count,updated_at)
                   VALUES(%s,'ready',1,0,NULL,%s,%s)
                   ON CONFLICT(queue_date) DO UPDATE SET state='ready',
                   generation=queue_projections.generation+1,
                   refresh_pending=0,last_error=NULL,
                   blocked_count=excluded.blocked_count,
                   updated_at=excluded.updated_at""",
                (queue_date, blocked_count, updated_at),
            )


def clear_diagnostics(database, queue_date):
    return database.execute_native(
                    "DELETE FROM queue_projection_diagnostics WHERE queue_date=%s",
                    (queue_date,),
                )


_QUEUE_RANDOMIZATION_ROWS_SQL = """SELECT q.id,q.card_id,q.position,c.content_type,
                  CASE WHEN q.admission_kind='explicit' THEN 'explicit'
                       WHEN EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id AND r.invalidated_at IS NULL) THEN 'review'
                       ELSE 'new' END admission_kind,
                  q.gameplay_priority_reason
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=? AND q.status='queued'
           ORDER BY q.id"""


def is_postgres_connection(database) -> bool:
    return isinstance(database, PostgresConnection)


def prepare_presentation_queries(miss_reason: str):
    """Compile immutable availability SQL before opening a short read/write unit.

    Native array parameters keep this one query shape at every queue/page size.
    No data, eligibility result or mutable authority is cached here.
    """
    return postgres_store.postgres_sql(available_attempt_sql(miss_reason))


def presentation_facts(database, day, miss_reason: str, *, read=None, after_entry_id=None, limit=None):
    """Capture eligibility and attempt protection without changing membership."""
    native = read is None and isinstance(database, PostgresConnection)
    if read is None:
        read = (lambda statement, parameters: database.execute_native(
                    statement.replace('?', '%s'), parameters).fetchall()) if native else (
                    lambda statement, parameters: database.execute(statement, parameters).fetchall())
    # Shadow evidence is PostgreSQL-only; SQLite remains the recovery adapter.
    presented = ("EXISTS(SELECT 1 FROM opening_evidence_attempts attempt WHERE attempt.queue_entry_id=q.id)"
                 if postgres_store.configured() else "0")
    statement = """SELECT q.id,q.card_id,q.position,c.content_type,q.admission_kind,
                  q.gameplay_priority_reason,q.cycle,q.attempt_state,q.attempt_failed,
                  (q.review_result_json IS NOT NULL) review_completed,c.revision,
                  EXISTS(SELECT 1 FROM queue_attempt_origins origin
                    WHERE origin.queue_entry_id=q.id AND origin.card_id=c.id
                      AND origin.revision=c.revision AND origin.issued_as_head=1) issued,
                  """ + presented + """ presented,
                  EXISTS(SELECT 1 FROM teaching_states taught WHERE taught.card_id=c.id) taught
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=? AND q.status='queued'"""
    parameters = [day]
    if after_entry_id is not None:
        statement += " AND q.id>?"
        parameters.append(after_entry_id)
    statement += " ORDER BY q.id"
    if limit is not None:
        statement += " LIMIT ?"
        parameters.append(limit)
    rows = [dict(row) for row in read(statement, tuple(parameters))]
    if not rows:
        return rows, set(), set()
    if native:
        reviewed = {row[0] for row in database.execute_native(
            "SELECT DISTINCT card_id FROM reviews WHERE invalidated_at IS NULL AND card_id=ANY(%s)",
            ([row['card_id'] for row in rows],)).fetchall()}
        eligible = {row[0] for row in database.execute_native(
            prepare_presentation_queries(miss_reason) + " AND q.id=ANY(%s)",
            (day, [row['id'] for row in rows])).fetchall()}
        return rows, reviewed, eligible
    placeholders = ','.join('?' for _ in rows)
    reviewed = {row[0] for row in read(
        "SELECT DISTINCT card_id FROM reviews WHERE invalidated_at IS NULL AND card_id IN (" + placeholders + ")",
        tuple(row['card_id'] for row in rows),
    )}
    eligible = {row[0] for row in read(available_attempt_sql(miss_reason) + " AND q.id IN (" + placeholders + ")",
                                     (day, *(row['id'] for row in rows)))}

    return rows, reviewed, eligible


def priority_ownership_state(database, day):
    """Fence queued ownership/identity and issuance, not derived eligibility policy.

    The target's current admission/content/source is independently fenced by Study.
    Ordering never grants eligibility to another card. Changed owners or protected
    attempts invalidate prepared positions; no whole-queue domain query runs here.
    """
    return database.execute_native("""WITH owned AS (
        SELECT q.id,jsonb_build_array(q.id,q.card_id,q.xmin::text,c.xmin::text,
            EXISTS(SELECT 1 FROM queue_attempt_origins o WHERE o.queue_entry_id=q.id
                AND o.card_id=c.id AND o.revision=c.revision AND o.issued_as_head=1),
            EXISTS(SELECT 1 FROM opening_evidence_attempts a WHERE a.queue_entry_id=q.id),
            EXISTS(SELECT 1 FROM teaching_states t WHERE t.card_id=c.id))::text value
        FROM daily_queue q JOIN cards c ON c.id=q.card_id
        WHERE q.queue_date=%s AND q.status='queued'
    ) SELECT encode(sha256(convert_to(
        COALESCE((SELECT string_agg(value,',' ORDER BY id) FROM owned),'') || '|' ||
        COALESCE((SELECT xmin::text FROM frozen_opening_activation WHERE id=1),'') || '|' ||
        COALESCE((SELECT xmin::text FROM settings WHERE id=1),''),'UTF8')),'hex')""",(day,)).fetchone()[0]
