"""Existing prefix split card/review/queue mutations on the caller transaction."""

from ....connections import card_columns


def update_queue_positions(database, values):
    database.executemany(
        "UPDATE daily_queue SET position=? WHERE id=?",
        values,
    )


def insert_revision(database, values):
    database.execute(
        'INSERT OR IGNORE INTO card_revisions(\n               card_id,revision,start_fen,moves_json,history_mode,created_at\n           ) VALUES(?,?,?,?,?,?)',
        values,
    )


def reassign_reviews(database, shortened_card_id, source_card_id):
    database.execute(
        "UPDATE reviews SET card_id=? WHERE card_id=?",
        (shortened_card_id, source_card_id),
    )


def archive_source_card(database, shortened_card_id, source_card_id):
    database.execute(
        "UPDATE cards SET archived=1,superseded_by=? WHERE id=?",
        (shortened_card_id, source_card_id),
    )


def delete_source_memberships(database, source_card_id):
    database.execute("DELETE FROM repertoire_cards WHERE card_id=?", (source_card_id,))


def insert_split(database, values):
    database.execute(
        'INSERT INTO prefix_splits(\n               source_card_id,source_revision,shortened_card_id,\n               continuation_card_id,created_at\n           ) VALUES(?,?,?,?,?)',
        values,
    )


def first_line_color(database, repertoire_id):
    return database.execute(
        'SELECT trained_color FROM repertoire_lines\n           WHERE repertoire_id=? ORDER BY created_at,id LIMIT 1',
        (repertoire_id,),
    ).fetchone()


def card_row(database, identifier):
    return database.execute("SELECT * FROM cards WHERE id=?", (identifier,)).fetchone()


def queued_positions(database, today):
    return database.execute(
        "SELECT id,position FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id",
        (today,),
    ).fetchall()


def prior_split(database, source_card_id):
    return database.execute(
        "SELECT * FROM prefix_splits WHERE source_card_id=?", (source_card_id,)
    ).fetchone()


def source_queue_entries(database, source_card_id):
    return database.execute(
        "SELECT id,queue_date,cycle FROM daily_queue WHERE card_id=?",
        (source_card_id,),
    ).fetchall()


def complete_queue_entry(database, queue_entry_id):
    database.execute(
        "UPDATE daily_queue SET status='complete' WHERE id=?",
        (queue_entry_id,),
    )


def reassign_queue_entry(database, shortened_card_id, queue_entry_id):
    database.execute(
        "UPDATE daily_queue SET card_id=? WHERE id=?",
        (shortened_card_id, queue_entry_id),
    )


def card_present(database, continuation_card_id):
    return database.execute(
        "SELECT 1 FROM cards WHERE id=?", (continuation_card_id,)
    ).fetchone()


def upsert_membership(database, repertoire_id, child_card_id, membership_source):
    database.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,?) "
        "ON CONFLICT(repertoire_id,card_id) DO UPDATE SET canonical_route_source=1 "
        "WHERE repertoire_cards.canonical_route_source=0 AND excluded.canonical_route_source=1",
        (repertoire_id, child_card_id, membership_source),
    )


def promote_card_source(database, child_card_id):
    database.execute("UPDATE cards SET canonical_route_source=1 WHERE id=?", (child_card_id,))


def queued_followup(database, today, card_id):
    return database.execute(
        "SELECT id FROM daily_queue WHERE queue_date=? AND card_id=?\n               AND status='queued' ORDER BY position,id LIMIT 1",
        (today, card_id),
    ).fetchone()


def membership_rows(database, source_card_id):
    return database.execute(
        "SELECT repertoire_id,canonical_route_source FROM repertoire_cards WHERE card_id=?",
        (source_card_id,),
    )


def queue_collision(database, values):
    return database.execute(
        "SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=? AND cycle=?",
        values,
    ).fetchone()


def insert_followup(database, values):
    return database.execute(
        "INSERT INTO daily_queue(\n                       queue_date,card_id,cycle,position,attempt_state,card_bucket,admission_kind\n                   ) VALUES(?,?,?,?,?,'opening','review') RETURNING id",
        values,
    ).fetchone()


def next_queue_cycle(database, today, card_id):
    return database.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?",
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
        f"INSERT INTO cards({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
        tuple(source_values.get(column) for column in columns),
    )
