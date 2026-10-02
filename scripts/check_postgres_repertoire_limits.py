"""Verify repertoire limits, daily reset, and receipt replay on disposable PostgreSQL."""
from datetime import date, timedelta
import os
from pathlib import Path
import sys
import uuid

import chess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import main, postgres_store, repertoire_commands  # registers production handlers
from app.command_gateway import execute_command, read_operation
from app.queue_commands import request_queue_refresh_in_transaction
from app.services.durable_tasks import lock_current_slice
from app.services.postgres_queue_refresh import (
    _admit_one_prioritized_opening, _prepare_unseen_reconciliation, _reconcile_one_unseen_entry,
)


def snapshot_queue_environment(database, queue_dates):
    """Capture only shared rows this proof can mutate while consumers are stopped."""
    task = database.execute(
        "SELECT * FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'",
    ).fetchone()
    # Enqueue prunes to 100 events: an ID boundary alone cannot recover history.
    events = database.execute(
        'SELECT * FROM background_task_events WHERE task_id=? ORDER BY id',
        (task['id'],),
    ).fetchall() if task else []
    projections = database.execute(
        'SELECT * FROM queue_projections WHERE queue_date IN (?,?) ORDER BY queue_date',
        queue_dates,
    ).fetchall()
    stale_introductions = database.execute(
        """SELECT id,state,introduced_at FROM cards
           WHERE content_type='opening' AND state='learning' AND introduced_at<?
             AND NOT EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=cards.id)
             AND NOT EXISTS(SELECT 1 FROM daily_queue queue
                            WHERE queue.card_id=cards.id AND queue.queue_date=?)
           ORDER BY id""",
        (queue_dates[1], queue_dates[1]),
    ).fetchall()
    return {
        'queue_dates': tuple(queue_dates),
        'task': dict(task) if task else None,
        'events': [dict(event) for event in events],
        'projections': [dict(projection) for projection in projections],
        'stale_introductions': [dict(card) for card in stale_introductions],
    }


def restore_queue_environment(database, snapshot):
    """Restore exact task/event identities and absent rows in the caller's transaction."""
    database.execute(
        "DELETE FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'",
    )  # The task FK cascades its events; no other table references this task.
    database.execute('DELETE FROM queue_projections WHERE queue_date IN (?,?)', snapshot['queue_dates'])
    for table_name, saved_rows in (
        ('background_tasks', [snapshot['task']] if snapshot['task'] else []),
        ('background_task_events', snapshot['events']),
        ('queue_projections', snapshot['projections']),
    ):
        for saved_row in saved_rows:
            # Column names come from SELECT * on these three internal tables.
            column_names = ','.join(saved_row)
            placeholders = ','.join('?' for _ in saved_row)
            database.execute(
                f'INSERT INTO {table_name}({column_names}) VALUES({placeholders})',
                tuple(saved_row.values()),
            )
    for saved_card in snapshot['stale_introductions']:
        database.execute('UPDATE cards SET state=?,introduced_at=? WHERE id=?',
                         (saved_card['state'], saved_card['introduced_at'], saved_card['id']))


def reconcile_fixture_entries(queue_date, repertoire_ids):
    processed_ids = []
    introduced_counts = {}
    while True:
        candidate, starting_count = _prepare_unseen_reconciliation(queue_date, processed_ids, introduced_counts)
        if candidate is None:
            break
        processed_ids.append(candidate['id'])
        if candidate['repertoire_id'] not in repertoire_ids:
            continue
        with postgres_store.connection(background=True) as database:
            kept = _reconcile_one_unseen_entry(database, queue_date, candidate, starting_count)
        introduced_counts[candidate['repertoire_id']] = starting_count + int(kept)


def main_check():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Repertoire limit proof requires disposable PostgreSQL')
    os.environ['TEMPO_DATABASE_WRITE_URL'] = 'postgresql://postgres@postgres:5432/tempo'
    os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
    prefix = f'limit-proof-{uuid.uuid4()}'
    repertoire_ids = [f'{prefix}-first', f'{prefix}-second']
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    operation_ids = []
    environment_snapshot = None
    fixture_card_ids = [f'{repertoire_id}-{card_index:02}'
                        for repertoire_id in repertoire_ids for card_index in range(20)]

    def set_limit(repertoire_id, limit):
        operation_id = str(uuid.uuid4())
        operation_ids.append(operation_id)
        payload = {'repertoire_id': repertoire_id, 'settings': {'new_cards_per_day': limit}}
        result = execute_command(operation_id, 'repertoires.settings.update', payload)
        assert result['new_cards_per_day'] == limit
        assert execute_command(operation_id, 'repertoires.settings.update', payload) == result
        assert read_operation(operation_id)['state'] == 'complete'
        return result

    def publish_cards(queue_date):
        for repertoire_id in repertoire_ids:
            for card_index in range(20):
                with postgres_store.connection(background=True) as database:
                    _admit_one_prioritized_opening(database, queue_date, {
                        'repertoire_id': repertoire_id, 'card_id': f'{repertoire_id}-{card_index:02}', 'reason': None,
                    })

    def counts(queue_date):
        with postgres_store.connection(read_only=True) as database:
            return dict(database.execute(
                "SELECT admission_repertoire_id,COUNT(*) FROM daily_queue WHERE queue_date=? "
                "AND admission_repertoire_id IN (?,?) AND status='queued' GROUP BY admission_repertoire_id",
                (queue_date, *repertoire_ids),
            ).fetchall())

    try:
        with postgres_store.connection() as database:
            environment_snapshot = snapshot_queue_environment(database, (today, tomorrow))
            for repertoire_id in repertoire_ids:
                database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)',
                                 (repertoire_id, 'Synthetic limits', 'synthetic', today))
                for card_index in range(20):
                    card_id = f'{repertoire_id}-{card_index:02}'
                    database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES(?,?,'prefix',?,'[\"e2e4\"]','new',?)",
                                     (card_id, repertoire_id, chess.STARTING_FEN, today))
                    database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)', (repertoire_id, card_id))
            older_checkpoint = request_queue_refresh_in_transaction(database, today)
            older_checkpoint['lease_token'] = str(uuid.uuid4())
            database.execute("UPDATE background_tasks SET state='leased',lease_token=? WHERE id=?",
                             (older_checkpoint['lease_token'], older_checkpoint['id']))
            assert lock_current_slice(database, older_checkpoint)
        assert set_limit(repertoire_ids[0], 10)['effective_new_cards_per_day'] == 10
        with postgres_store.connection() as database:
            assert not lock_current_slice(database, older_checkpoint)
            refreshed_task = database.execute('SELECT * FROM background_tasks WHERE id=?', (older_checkpoint['id'],)).fetchone()
            assert refreshed_task['generation'] == older_checkpoint['generation'] + 1
            assert refreshed_task['state'] == 'queued' and refreshed_task['lease_token'] is None
        assert set_limit(repertoire_ids[1], 5)['effective_new_cards_per_day'] == 5
        publish_cards(today)
        assert counts(today) == dict(zip(repertoire_ids, (10, 5)))
        with postgres_store.connection() as database:
            for card_index in range(7):
                card_id = f'{repertoire_ids[0]}-{card_index:02}'
                database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct',?,0,7)", (card_id, f'{today}T12:00:00'))
                database.execute("UPDATE cards SET state='mature',due_date=? WHERE id=?", ((date.today() + timedelta(days=7)).isoformat(), card_id))
                database.execute("UPDATE daily_queue SET status='complete' WHERE queue_date=? AND card_id=?", (today, card_id))
            main._reset_stale_opening_introductions(database, tomorrow)
        publish_cards(tomorrow)
        publish_cards(tomorrow)
        assert counts(tomorrow) == dict(zip(repertoire_ids, (10, 5)))
        # A plan prepared before this change must not publish another card.
        set_limit(repertoire_ids[1], 0)
        publish_cards(tomorrow)
        assert counts(tomorrow)[repertoire_ids[1]] == 5
        reconcile_fixture_entries(tomorrow, repertoire_ids)
        assert counts(tomorrow) == {repertoire_ids[0]: 10}
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT introduced_at FROM cards WHERE id=?', (f'{repertoire_ids[1]}-19',)).fetchone()[0] is None
        inherited = set_limit(repertoire_ids[1], None)
        with postgres_store.connection(read_only=True) as database:
            assert inherited['effective_new_cards_per_day'] == database.execute('SELECT new_cards_per_day FROM settings WHERE id=1').fetchone()[0]
    finally:
        with postgres_store.connection() as database:
            for repertoire_id in repertoire_ids:
                database.execute('DELETE FROM repertoires WHERE id=?', (repertoire_id,))
            for operation_id in operation_ids:
                database.execute('DELETE FROM operation_receipts WHERE operation_id=?', (operation_id,))
            if environment_snapshot is not None:
                restore_queue_environment(database, environment_snapshot)
            card_placeholders = ','.join('?' for _ in fixture_card_ids)
            for table_name, card_column in (('cards', 'id'), ('repertoire_cards', 'card_id'),
                                            ('reviews', 'card_id'), ('daily_queue', 'card_id')):
                assert database.execute(
                    f'SELECT COUNT(*) FROM {table_name} WHERE {card_column} IN ({card_placeholders})',
                    fixture_card_ids,
                ).fetchone()[0] == 0, f'Leaked fixture rows in {table_name}'
            if environment_snapshot is not None:
                assert snapshot_queue_environment(database, (today, tomorrow)) == environment_snapshot
    print('PASS PostgreSQL independent 10/5 repertoire limits, seven-of-ten daily reset, stale plan rejection, inheritance, receipt replay, generation invalidation, and environment restoration')


if __name__ == '__main__':
    main_check()
