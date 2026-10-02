"""Daily opening overrides and unused-allowance regression protection."""
from datetime import date, timedelta
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from app import database, main

FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'


def test_repertoire_limit_migration_has_unique_number_and_matches_schema_readiness():
    from app.schema_version import POSTGRES_SCHEMA_VERSION

    migration_directory = Path(__file__).resolve().parents[1] / 'migrations'
    migration_paths = sorted(migration_directory.glob('[0-9][0-9][0-9]_*.sql'))
    migration_numbers = [int(path.name[:3]) for path in migration_paths]
    assert migration_numbers == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
    limit_migration, = migration_directory.glob('*_repertoire_daily_limits.sql')
    recorded_version = re.search(
        r'INSERT INTO tempo_schema_migrations\(version\) VALUES \((\d+)\)',
        limit_migration.read_text(),
    )
    assert recorded_version is not None
    assert int(recorded_version.group(1)) == int(limit_migration.name[:3])


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'overrides.db')
    database.initialize()
    with database.connection() as connection:
        connection.execute('UPDATE settings SET new_cards_per_day=10,tactics_new_per_day=0,study_new_per_day=0 WHERE id=1')
        for repertoire_id in ('first', 'second'):
            connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
                               (repertoire_id, repertoire_id.title(), 'synthetic', date.today().isoformat()))
            for card_index in range(20):
                card_id = f'{repertoire_id}-{card_index:02}'
                connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES(?,?,'prefix',?,'[\"e2e4\"]','new',?)",
                                   (card_id, repertoire_id, FEN, date.today().isoformat()))
                connection.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)',
                                   (repertoire_id, card_id))
    from app.services.database_executor import database_writer
    database_writer.start()
    try:
        yield TestClient(main.app)
    finally:
        database_writer.stop()


def queue_counts(connection, queue_date):
    return dict(connection.execute("SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(*) FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=? AND q.status='queued' GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id)", (queue_date,)))


def complete_cards(connection, queue_date, repertoire_id, count):
    cards = connection.execute("SELECT q.id,q.card_id FROM daily_queue q WHERE q.queue_date=? AND q.admission_repertoire_id=? ORDER BY q.id LIMIT ?", (queue_date, repertoire_id, count)).fetchall()
    assert len(cards) == count
    for entry in cards:
        connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct',?,0,7)", (entry['card_id'], f'{queue_date}T12:00:00'))
        connection.execute("UPDATE cards SET state='mature',due_date=? WHERE id=?", ((date.fromisoformat(queue_date) + timedelta(days=7)).isoformat(), entry['card_id']))
        connection.execute("UPDATE daily_queue SET status='complete' WHERE id=?", (entry['id'],))


def test_repertoire_daily_overrides_apply_independently_and_reset_to_default(workspace):
    response = workspace.put('/api/repertoires/second/settings', json={'new_cards_per_day': 5})
    assert response.status_code == 200
    assert response.json() == {'repertoire_id': 'second', 'new_cards_per_day': 5, 'effective_new_cards_per_day': 5}
    with database.connection() as connection:
        main.seed_queue(connection, date.today().isoformat())
        assert queue_counts(connection, date.today().isoformat()) == {'first': 10, 'second': 5}
    response = workspace.put('/api/repertoires/second/settings', json={'new_cards_per_day': None})
    assert response.status_code == 200
    assert response.json()['effective_new_cards_per_day'] == 10
    with database.connection() as connection:
        main.seed_queue(connection, date.today().isoformat())
        assert queue_counts(connection, date.today().isoformat()) == {'first': 10, 'second': 10}
    records = workspace.get('/api/repertoires').json()['repertoires']
    assert all(record['new_cards_per_day'] is None for record in records)
    assert all(record['effective_new_cards_per_day'] == 10 for record in records)


def test_repertoire_limit_changes_today_preserve_completed_work_and_due_reviews(workspace):
    today = date.today().isoformat()
    with database.connection() as connection:
        main.seed_queue(connection, today)
        complete_cards(connection, today, 'first', 3)
        connection.execute("UPDATE cards SET due_date=? WHERE id='first-00'", (today,))
    assert workspace.put('/api/repertoires/first/settings', json={'new_cards_per_day': 5}).status_code == 200
    with database.connection() as connection:
        main.seed_queue(connection, today)
        assert queue_counts(connection, today)['first'] == 2
        assert connection.execute("SELECT COUNT(*) FROM daily_queue WHERE status='complete'").fetchone()[0] == 3
    assert workspace.put('/api/repertoires/first/settings', json={'new_cards_per_day': 0}).status_code == 200
    with database.connection() as connection:
        main.seed_queue(connection, today)
        assert queue_counts(connection, today).get('first', 0) == 0
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        main.seed_queue(connection, tomorrow)
        assert queue_counts(connection, tomorrow)['first'] == 1  # due review survives zero


def test_seven_of_ten_learned_allows_ten_new_tomorrow_without_rollover(workspace):
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert workspace.put('/api/repertoires/first/settings', json={'new_cards_per_day': 10}).status_code == 200
    with database.connection() as connection:
        main.seed_queue(connection, today)
        complete_cards(connection, today, 'first', 7)
        main.seed_queue(connection, tomorrow)
        main.seed_queue(connection, tomorrow)
        assert queue_counts(connection, tomorrow)['first'] == 10
        assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 7


@pytest.mark.parametrize('payload', [{'new_cards_per_day': -1}, {'new_cards_per_day': 101}, {'new_cards_per_day': 1.5}, {'new_cards_per_day': True}, {'new_cards_per_day': '5'}, {}, {'new_cards_per_day': 5, 'unknown': 1}])
def test_repertoire_override_rejects_invalid_payloads(workspace, payload):
    assert workspace.put('/api/repertoires/first/settings', json=payload).status_code == 422


def test_repertoire_override_rejects_missing_and_system_repertoires(workspace):
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__defense__','Defensive tactics','synthetic',?)", (date.today().isoformat(),))
    for repertoire_id in ('missing', '__tactics__', '__defense__'):
        assert workspace.put(f'/api/repertoires/{repertoire_id}/settings', json={'new_cards_per_day': 5}).status_code == 404

    assert {record['id'] for record in workspace.get('/api/repertoires').json()['repertoires']} == {'first', 'second'}

def test_shared_card_is_deduplicated_and_charged_to_admitting_repertoire(workspace):
    assert workspace.put('/api/repertoires/first/settings', json={'new_cards_per_day': 0}).status_code == 200
    assert workspace.put('/api/repertoires/second/settings', json={'new_cards_per_day': 1}).status_code == 200
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('second','first-00')")
        connection.execute("UPDATE cards SET archived=1 WHERE id!='first-00'")
        main.seed_queue(connection, date.today().isoformat())
        main.seed_queue(connection, date.today().isoformat())
        assert queue_counts(connection, date.today().isoformat()) == {'second': 1}
        complete_cards(connection, date.today().isoformat(), 'second', 1)
        main.seed_queue(connection, date.today().isoformat())
        assert connection.execute('SELECT COUNT(*) FROM daily_queue').fetchone()[0] == 1


class NativeSqlite:
    """Execute portable worker SQL to check behavior; not PostgreSQL durability proof."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace('FOR UPDATE', ''), parameters)

    def execute_native(self, statement, parameters=()):
        return self.connection.execute(statement.replace('%s', '?').replace('FOR UPDATE OF q,c', '').replace('FOR UPDATE', ''), parameters)


def test_postgres_opening_publication_rechecks_lowered_limit_and_current_admissions(workspace, monkeypatch):
    from app.services import postgres_queue_refresh as worker
    monkeypatch.setattr(worker, 'lock_queue_date_for_position', lambda *args: None)
    today = date.today().isoformat()
    with database.connection() as connection:
        native = NativeSqlite(connection)
        planned = {'card_id': 'first-00', 'repertoire_id': 'first', 'reason': None}
        connection.execute("UPDATE repertoires SET new_cards_per_day=0 WHERE id='first'")
        worker._admit_one_prioritized_opening(native, today, planned)
        assert queue_counts(connection, today) == {}
        connection.execute("UPDATE repertoires SET new_cards_per_day=1 WHERE id='first'")
        worker._admit_one_prioritized_opening(native, today, planned)
        worker._admit_one_prioritized_opening(native, today, planned)  # idempotent replay
        worker._admit_one_prioritized_opening(native, today, {**planned, 'card_id': 'first-01'})
        assert queue_counts(connection, today) == {'first': 1}
        assert connection.execute("SELECT introduced_at FROM cards WHERE id='first-01'").fetchone()[0] is None


def test_postgres_repertoire_settings_write_invalidates_old_queue_checkpoint(workspace):
    from app.repertoire_commands import update_repertoire_settings
    from app.services.durable_tasks import lock_current_slice
    from app.queue_commands import request_queue_refresh_in_transaction
    today = date.today().isoformat()
    with database.connection() as connection:
        native = NativeSqlite(connection)
        original_task = request_queue_refresh_in_transaction(native, today)
        connection.execute("UPDATE background_tasks SET state='leased',lease_token='original' WHERE id=?", (original_task['id'],))
        original_task = {**original_task, 'lease_token': 'original'}
        assert lock_current_slice(native, original_task)
        result = update_repertoire_settings(native, {'repertoire_id': 'first', 'settings': {'new_cards_per_day': 5}})
        assert result['effective_new_cards_per_day'] == 5
        assert not lock_current_slice(native, original_task)
        refreshed_task = connection.execute('SELECT * FROM background_tasks WHERE id=?', (original_task['id'],)).fetchone()
        assert refreshed_task['generation'] == original_task['generation'] + 1
        assert refreshed_task['state'] == 'queued'
    # Reopen the store: no in-memory settings or task state is required.
    with database.connection() as connection:
        assert connection.execute("SELECT new_cards_per_day FROM repertoires WHERE id='first'").fetchone()[0] == 5
        main.seed_queue(connection, today)
        assert queue_counts(connection, today)['first'] == 5


def test_postgres_repertoire_settings_endpoint_dispatches_durable_command(workspace, monkeypatch):
    from app import command_dispatch, postgres_store
    submitted = []
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda name, payload, **kwargs: submitted.append((name, payload, kwargs)) or {'operation_id': 'op', 'state': 'pending'})
    response = workspace.put('/api/repertoires/first/settings', json={'new_cards_per_day': 5}, headers={'Idempotency-Key': 'op'})
    assert response.status_code in (200, 202)
    assert submitted == [('repertoires.settings.update', {'repertoire_id': 'first', 'settings': {'new_cards_per_day': 5}}, {'idempotency_key': 'op'})]


def test_existing_repertoire_migrates_to_inherited_limit(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'old.db')
    with sqlite3.connect(database.DB_PATH) as connection:
        connection.execute('CREATE TABLE repertoires(id TEXT PRIMARY KEY,name TEXT NOT NULL,source_name TEXT NOT NULL,created_at TEXT NOT NULL)')
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('old','Old','synthetic','2026-10-01')")
    database.initialize()
    database.initialize()
    with database.connection() as connection:
        assert connection.execute("SELECT new_cards_per_day FROM repertoires WHERE id='old'").fetchone()[0] is None


def test_global_daily_limit_changes_only_inheriting_repertoires(workspace):
    assert workspace.put('/api/repertoires/second/settings', json={'new_cards_per_day': 5}).status_code == 200
    settings = workspace.get('/api/settings').json()
    assert workspace.put('/api/settings', json={**settings, 'new_cards_per_day': 3}).status_code == 200
    with database.connection() as connection:
        main.seed_queue(connection, date.today().isoformat())
        assert queue_counts(connection, date.today().isoformat()) == {'first': 3, 'second': 5}


def test_shared_card_review_retries_do_not_consume_owners_new_card_allowance(workspace):
    today = date.today().isoformat()
    assert workspace.put('/api/repertoires/first/settings', json={'new_cards_per_day': 0}).status_code == 200
    assert workspace.put('/api/repertoires/second/settings', json={'new_cards_per_day': 1}).status_code == 200
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('second','first-00')")
        connection.execute("UPDATE cards SET archived=1 WHERE id!='first-00'")
        main.seed_queue(connection, today)
        complete_cards(connection, today, 'second', 1)
        connection.execute("UPDATE cards SET state='learning',due_date=? WHERE id='first-00'", (today,))
        connection.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position) VALUES(?,'first-00',1,100)", (today,))
        connection.execute("UPDATE cards SET archived=0 WHERE id='first-01'")
    assert workspace.put('/api/repertoires/first/settings', json={'new_cards_per_day': 1}).status_code == 200
    with database.connection() as connection:
        main.seed_queue(connection, today)
        assert connection.execute("SELECT COUNT(*) FROM daily_queue WHERE queue_date=? AND card_id='first-01'", (today,)).fetchone()[0] == 1


@pytest.mark.parametrize('reviewed', [False, True])
def test_shared_card_integrity_change_does_not_refund_admitting_repertoire_allowance(workspace, monkeypatch, reviewed):
    from app.services import postgres_queue_refresh as worker
    today = date.today().isoformat()
    with database.connection() as connection:
        connection.execute("UPDATE repertoires SET new_cards_per_day=0 WHERE id='first'")
        connection.execute("UPDATE repertoires SET new_cards_per_day=1 WHERE id='second'")
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('second','first-00')")
        connection.execute("UPDATE cards SET archived=1 WHERE id!='first-00'")
        main.seed_queue(connection, today)
        assert connection.execute("SELECT admission_repertoire_id FROM daily_queue WHERE card_id='first-00'").fetchone()[0] == 'second'
        if reviewed:
            complete_cards(connection, today, 'second', 1)
        connection.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,signature,created_at,updated_at) VALUES('block','first','missing_response','shared',?,?)", (today, today))
        connection.execute("INSERT INTO repertoire_integrity_card_blocks(repertoire_id,card_id,issue_id,scan_generation,published_at) VALUES('first','first-00','block','current',?)", (today,))
        connection.execute("UPDATE cards SET archived=0 WHERE id='second-00'")

        # Exercise the PostgreSQL planning reads against the same persisted fixture.
        # Candidate pages are supplied explicitly; accounting SQL is executed unchanged
        # except for PostgreSQL placeholders (real durability runs separately).
        def bounded_read(statement, parameters=(), *, native=False):
            if statement.startswith("SELECT DISTINCT event.card_id"):
                return []
            if statement.startswith("SELECT id FROM cards"):
                return [('second-00',)] if parameters[0] == '' else []
            if statement.startswith('WITH active_miss'):
                return [{'id': 'second-00', 'repertoire_id': 'second', 'gameplay_priority_reason': 'test', 'priority_date': today}]
            if 'q.id <> ALL' in statement:
                return [{'id': 999, 'card_id': 'second-00', 'repertoire_id': 'second'}]
            return connection.execute(statement.replace('%s', '?'), parameters).fetchall()
        monkeypatch.setattr(worker, '_bounded_read', bounded_read)
        assert worker._prepare_prioritized_openings(today) == []
        if reviewed:
            candidate, reviewed_count = worker._prepare_unseen_reconciliation(today, [], {})
            assert candidate['repertoire_id'] == 'second'
            assert reviewed_count == 1
            # A legacy eager queue entry must also respect the reviewed introduction.
            connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_repertoire_id) VALUES(?,'second-00',99,'second')", (today,))
        main.seed_queue(connection, today)
        assert connection.execute("SELECT introduced_at FROM cards WHERE id='first-00'").fetchone()[0] == today
        assert connection.execute("SELECT introduced_at FROM cards WHERE id='second-00'").fetchone()[0] is None
        assert connection.execute("SELECT COUNT(*) FROM cards WHERE introduced_at=?", (today,)).fetchone()[0] == 1
