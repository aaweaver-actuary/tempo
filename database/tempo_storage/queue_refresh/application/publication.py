"""Existing queue plan, quarantine facts and projection writes; no policy or clocks."""


def apply_position(db, position, bucket, kind, entry_id):
    db.execute(
            'UPDATE daily_queue SET position=%s,card_bucket=%s,admission_kind=%s WHERE id=%s',
            (
                position,
                bucket,
                kind,
                entry_id,
            ),
        )


def save_plan(db, day, seed, membership_hash, generated_at):
    db.execute(
        'INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)\n           VALUES(%s,%s,%s,%s) ON CONFLICT(queue_date) DO UPDATE SET\n           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at',
        (day, seed, membership_hash, generated_at),
    )


def quarantine_candidates(database, queue_date):
    return database.execute(
        "SELECT q.id queue_entry_id,c.id,c.start_fen,c.moves_json,c.content_type,\n                          COALESCE(c.trained_color,(SELECT l.trained_color FROM repertoire_lines l\n                           WHERE l.repertoire_id=c.repertoire_id ORDER BY l.created_at LIMIT 1)) trained_color\n                   FROM daily_queue q JOIN cards c ON c.id=q.card_id\n                   WHERE q.queue_date=%s AND q.status='queued' AND c.archived=0\n                     AND (c.content_type!='opening' OR EXISTS(SELECT 1 FROM repertoires r_ok\n                                WHERE (r_ok.id=c.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc_ok WHERE rc_ok.card_id=c.id AND rc_ok.repertoire_id=r_ok.id))\n                                  AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block\n                                                 WHERE block.repertoire_id=r_ok.id AND block.card_id=c.id)))",
        (queue_date,),
    ).fetchall()


def lock_incomplete_card(database, card_id):
    database.execute("UPDATE cards SET state='locked' WHERE id=%s", (card_id,))


def skip_incomplete_entry(database, queue_entry_id):
    database.execute(
            "UPDATE daily_queue SET status='skipped' WHERE id=%s",
            (queue_entry_id,),
        )


def begin_projection(database, queue_date):
    database.execute(
        "INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)\n           VALUES(%s,'refreshing',0,1)\n           ON CONFLICT(queue_date) DO UPDATE SET state='refreshing',refresh_pending=1,\n               last_error=NULL",
        (queue_date,),
    )


def clear_diagnostics(database, queue_date):
    database.execute(
        'DELETE FROM queue_projection_diagnostics WHERE queue_date=%s', (queue_date,)
    )


def record_diagnostics(database, records):
    database.executemany(
        'INSERT INTO queue_projection_diagnostics(queue_date,card_id,message) VALUES(%s,%s,%s)',
        records,
    )


def blocked_count(database, queue_date):
    return database.execute(
        "SELECT COUNT(*) FROM (\n               SELECT q.card_id FROM daily_queue q\n               WHERE q.queue_date=%s AND q.status='blocked'\n               UNION\n               SELECT c.id FROM cards c\n               WHERE c.content_type='opening' AND c.archived=0\n                 AND c.state IN ('learning','mature') AND c.due_date<=%s\n                 AND EXISTS(\n                     SELECT 1 FROM repertoire_integrity_card_blocks block\n                     WHERE block.card_id=c.id\n                       AND (block.repertoire_id=c.repertoire_id OR EXISTS(\n                           SELECT 1 FROM repertoire_cards rc\n                           WHERE rc.card_id=c.id\n                             AND rc.repertoire_id=block.repertoire_id\n                       ))\n                 )\n           )",
        (queue_date, queue_date),
    ).fetchone()[0]


def publish_projection(database, queue_date, now, blocked_count):
    database.execute(
        "UPDATE queue_projections SET state='ready',generation=generation+1,\n               updated_at=%s,refresh_pending=0,last_error=NULL,blocked_count=%s\n           WHERE queue_date=%s",
        (now, blocked_count, queue_date),
    )
