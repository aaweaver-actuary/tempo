"""Fixed feedback facts, locks and queue writes on the caller's transaction.

No transaction is opened/completed here. Application queue ordering, typed fact
mapping, introduction/review callbacks and accepted-priority choices live in the
existing backend feedback store composition.
"""
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_query

BATCH_SIZE = 128


def is_postgres(database):
    return isinstance(database, PostgresConnection)


def lock_card(database, card_id):
    if isinstance(database, PostgresConnection):
        database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                                      (f'tempo:card-edit:{card_id}',))
        database.execute('SELECT id FROM cards WHERE id=? FOR UPDATE', (card_id,))


def outstanding_cards(database, card_ids):
    """One complete, parameter-bound projection for at most one batch."""
    if len(card_ids) > BATCH_SIZE:
        raise ValueError('feedback card batch exceeds 128 identities')
    if not card_ids:
        return ()
    placeholders = ','.join('?' for _ in card_ids)
    return database.execute(outstanding_miss_query(
        database, columns='DISTINCT card.id,card.state,card.introduced_at',
        additional_where=f'AND card.id IN ({placeholders}) ORDER BY card.id'), card_ids).fetchall()


def is_buried(database, card_id, queue_day):
    return database.execute(
        "SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=? AND status='buried' LIMIT 1",
        (queue_day, card_id)).fetchone() is not None


def lock_queue_day(database, queue_day):
    if isinstance(database, PostgresConnection):
        lock_queue_date_for_position(database, queue_day)


def set_queue_position(database, position, entry_id):
    database.execute('UPDATE daily_queue SET position=? WHERE id=?', (position, entry_id))


def event_card(database, event_id):
    row = database.execute(
        "SELECT card_id FROM current_repertoire_decision_events WHERE id=? AND outcome='miss'", (event_id,)).fetchone()
    return row['card_id'] if row else None


def replace_miss_priority(database, card_id, previous, replacement):
    database.execute('UPDATE daily_queue SET gameplay_priority_reason=? '
                           'WHERE card_id=? AND gameplay_priority_reason=?', (replacement, card_id, previous))


def missed_event_batches(database, game_id):
    # One query retains the original selection snapshot and order. Fetch in
    # bounded batches rather than issuing a new query/snapshot per page.
    cursor = database.execute(
        "SELECT id FROM current_repertoire_decision_events WHERE game_id=? AND outcome='miss' ORDER BY ply,id", (game_id,))
    while rows := cursor.fetchmany(BATCH_SIZE):
        yield rows


def repertoire_miss_batches(database, repertoire_id):
    cursor = database.execute(outstanding_miss_query(
        database, columns='event.id,event.card_id,event.game_id,event.played_at',
        additional_where='AND event.repertoire_id=?'), (repertoire_id,))
    while rows := cursor.fetchmany(BATCH_SIZE):
        yield rows
