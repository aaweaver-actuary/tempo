"""Existing queue presentation, quarantine and projection operations."""


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
