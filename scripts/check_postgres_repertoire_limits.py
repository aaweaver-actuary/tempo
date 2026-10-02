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
from app.services.postgres_queue_refresh import (
    _admit_one_prioritized_opening, _prepare_unseen_reconciliation, _reconcile_one_unseen_entry,
)


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
            for repertoire_id in repertoire_ids:
                database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)',
                                 (repertoire_id, 'Synthetic limits', 'synthetic', today))
                for card_index in range(20):
                    card_id = f'{repertoire_id}-{card_index:02}'
                    database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES(?,?,'prefix',?,'[\"e2e4\"]','new',?)",
                                     (card_id, repertoire_id, chess.STARTING_FEN, today))
                    database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)', (repertoire_id, card_id))
        assert set_limit(repertoire_ids[0], 10)['effective_new_cards_per_day'] == 10
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
        processed_ids = []
        introduced_counts = {}
        while True:
            candidate, starting_count = _prepare_unseen_reconciliation(tomorrow, processed_ids, introduced_counts)
            if candidate is None:
                break
            with postgres_store.connection(background=True) as database:
                kept = _reconcile_one_unseen_entry(database, tomorrow, candidate, starting_count)
            processed_ids.append(candidate['id'])
            introduced_counts[candidate['repertoire_id']] = starting_count + int(kept)
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
    print('PASS PostgreSQL independent 10/5 repertoire limits, seven-of-ten daily reset, stale plan rejection, inheritance, and receipt replay')


if __name__ == '__main__':
    main_check()
