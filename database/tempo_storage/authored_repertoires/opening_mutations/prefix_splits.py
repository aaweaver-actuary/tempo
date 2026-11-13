"""Existing prefix split card/review/queue mutations on the caller transaction."""

from tempo_storage.connections import card_columns


def update_queue_positions(database, values):
    database.executemany(
        'UPDATE daily_queue SET position=%s WHERE id=%s',
        values,
    )


def insert_revision(database, values):
    database.execute(
        'INSERT INTO card_revisions(\n               card_id,revision,start_fen,moves_json,history_mode,created_at\n           ) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
        values,
    )


def reassign_reviews(database, shortened_card_id, source_card_id):
    database.execute(
        '''UPDATE reviews SET card_id=%s WHERE card_id=%s
           AND NOT EXISTS (SELECT 1 FROM review_attempt_receipts r WHERE r.review_id=reviews.id)''',
        (shortened_card_id, source_card_id),
    )


def archive_source_card(database, shortened_card_id, source_card_id):
    database.execute(
        'UPDATE cards SET archived=1,superseded_by=%s WHERE id=%s',
        (shortened_card_id, source_card_id),
    )


def delete_source_memberships(database, source_card_id):
    database.execute('DELETE FROM repertoire_cards WHERE card_id=%s', (source_card_id,))


def insert_split(database, values):
    database.execute(
        'INSERT INTO prefix_splits(\n               source_card_id,source_revision,shortened_card_id,\n               continuation_card_id,created_at\n           ) VALUES(%s,%s,%s,%s,%s)',
        values,
    )


def first_line_color(database, repertoire_id):
    return database.execute(
        'SELECT trained_color FROM repertoire_lines\n           WHERE repertoire_id=%s ORDER BY created_at,id LIMIT 1',
        (repertoire_id,),
    ).fetchone()


def card_row(database, identifier):
    return database.execute('SELECT * FROM cards WHERE id=%s', (identifier,)).fetchone()


def queued_positions(database, today):
    return database.execute(
        "SELECT id,position FROM daily_queue WHERE queue_date=%s AND status='queued' ORDER BY position,id",
        (today,),
    ).fetchall()


def prior_split(database, source_card_id):
    return database.execute(
        'SELECT * FROM prefix_splits WHERE source_card_id=%s', (source_card_id,)
    ).fetchone()


def source_queue_entries(database, source_card_id):
    return database.execute(
        '''SELECT q.id,q.queue_date,q.cycle FROM daily_queue q WHERE q.card_id=%s
           AND NOT EXISTS (SELECT 1 FROM queue_attempt_origins o
                           WHERE o.queue_entry_id=q.id AND o.card_id=q.card_id AND o.issued_as_head=1)
           AND NOT EXISTS (SELECT 1 FROM opening_evidence_attempts a WHERE a.queue_entry_id=q.id)
           AND NOT EXISTS (SELECT 1 FROM study_position_issued_plans p WHERE p.queue_entry_id=q.id)
           AND NOT EXISTS (SELECT 1 FROM review_attempt_receipts r WHERE r.queue_entry_id=q.id)''',
        (source_card_id,),
    ).fetchall()


def has_active_issued_attempt(database, source_card_id):
    """Read under the caller's card lock, shared with queue issuance and review."""
    return database.execute_native(
        '''SELECT 1 FROM daily_queue q WHERE q.card_id=%s AND q.status='queued'
           AND (EXISTS (SELECT 1 FROM queue_attempt_origins o WHERE o.queue_entry_id=q.id
                        AND o.card_id=q.card_id AND o.issued_as_head=1)
             OR EXISTS (SELECT 1 FROM opening_evidence_attempts a WHERE a.queue_entry_id=q.id)
             OR EXISTS (SELECT 1 FROM study_position_issued_plans p WHERE p.queue_entry_id=q.id)) LIMIT 1''',
        (source_card_id,),
    ).fetchone() is not None


def matches_queue_entry(database, source_card_id, queue_entry_id):
    return database.execute_native(
        'SELECT 1 FROM daily_queue WHERE id=%s AND card_id=%s',
        (queue_entry_id, source_card_id),
    ).fetchone() is not None


def complete_queue_entry(database, queue_entry_id):
    database.execute(
        "UPDATE daily_queue SET status='complete' WHERE id=%s",
        (queue_entry_id,),
    )


def reassign_queue_entry(database, shortened_card_id, queue_entry_id):
    database.execute(
        'UPDATE daily_queue SET card_id=%s WHERE id=%s',
        (shortened_card_id, queue_entry_id),
    )


def card_present(database, continuation_card_id):
    return database.execute(
        'SELECT 1 FROM cards WHERE id=%s', (continuation_card_id,)
    ).fetchone()


def upsert_membership(database, repertoire_id, child_card_id, membership_source):
    database.execute(
        'INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,%s) ON CONFLICT(repertoire_id,card_id) DO UPDATE SET canonical_route_source=1 WHERE repertoire_cards.canonical_route_source=0 AND excluded.canonical_route_source=1',
        (repertoire_id, child_card_id, membership_source),
    )


def promote_card_source(database, child_card_id):
    database.execute('UPDATE cards SET canonical_route_source=1 WHERE id=%s', (child_card_id,))


def queued_followup(database, today, card_id):
    return database.execute(
        "SELECT id FROM daily_queue WHERE queue_date=%s AND card_id=%s\n               AND status='queued' ORDER BY position,id LIMIT 1",
        (today, card_id),
    ).fetchone()


def membership_rows(database, source_card_id):
    return database.execute(
        'SELECT repertoire_id,canonical_route_source FROM repertoire_cards WHERE card_id=%s',
        (source_card_id,),
    )


def queue_collision(database, values):
    return database.execute(
        'SELECT 1 FROM daily_queue WHERE queue_date=%s AND card_id=%s AND cycle=%s',
        values,
    ).fetchone()


def insert_followup(database, values):
    return database.execute(
        "INSERT INTO daily_queue(\n                       queue_date,card_id,cycle,position,attempt_state,card_bucket,admission_kind\n                   ) VALUES(%s,%s,%s,%s,%s,'opening','review') RETURNING id",
        values,
    ).fetchone()


def next_queue_cycle(database, today, card_id):
    return database.execute(
        'SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=%s AND card_id=%s',
        (today, card_id),
    ).fetchone()[0]


def copy_card(
    database,
    source_card,
    values: dict,
) -> None:
    columns = card_columns(database)
    source_values = dict(source_card)
    source_values.update(values)
    database.execute(
        f"INSERT INTO cards({','.join(columns)}) VALUES({','.join('%s' for _ in columns)})",
        tuple(source_values.get(column) for column in columns),
    )
