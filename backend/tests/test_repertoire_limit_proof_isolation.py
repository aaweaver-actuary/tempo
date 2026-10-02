"""Isolation contract for the disposable PostgreSQL repertoire-limit proof.

Portable SQL tests cover restoration; the Docker proof owns real PostgreSQL,
receipt replay, locking, and publication evidence.
"""
from contextlib import contextmanager
from datetime import date, timedelta
import importlib.util
from pathlib import Path

import pytest

from app import database, postgres_store, repertoire_commands
from app.queue_commands import request_queue_refresh_in_transaction
from app.services.background_metrics import increment


proof_spec = importlib.util.spec_from_file_location(
    'repertoire_limit_proof',
    Path(__file__).resolve().parents[2] / 'scripts/check_postgres_repertoire_limits.py',
)
proof = importlib.util.module_from_spec(proof_spec)
proof_spec.loader.exec_module(proof)


class PortableConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace('FOR UPDATE', ''), parameters)


@pytest.fixture
def proof_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'proof.db')
    monkeypatch.setattr(postgres_store, 'configured', lambda: False)
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    # main_check configures its disposable URLs; restore the process environment.
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', '')
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', '')
    database.initialize()

    @contextmanager
    def connection(**options):
        with database.connection() as sqlite_connection:
            yield PortableConnection(sqlite_connection)

    monkeypatch.setattr(postgres_store, 'connection', connection)
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with connection() as stored:
        stored.execute('CREATE TABLE operation_receipts(operation_id TEXT PRIMARY KEY)')
        stored.execute("INSERT INTO operation_receipts VALUES('unrelated-receipt')")
        # Model migration 21's audited card-update trigger. Real PostgreSQL is
        # covered by make docker-durability; SQLite's optional schema lacks it.
        stored.execute('CREATE TABLE priority_repertoire_source_epochs(repertoire_id TEXT PRIMARY KEY REFERENCES repertoires(id) ON DELETE CASCADE,version INTEGER NOT NULL)')
        stored.execute('CREATE TABLE priority_source_epoch(id INTEGER PRIMARY KEY,version INTEGER NOT NULL)')
        stored.execute('INSERT INTO priority_source_epoch VALUES(1,55)')
        stored.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('unrelated','Unrelated','synthetic',?)", (today,))
        stored.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('shared','Shared','synthetic',?)", (today,))
        stored.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at) VALUES('unrelated-card','unrelated','prefix',?,'[]','learning',?,?)", (proof.chess.STARTING_FEN, today, today))
        stored.execute("INSERT INTO repertoire_cards VALUES('shared','unrelated-card')")
        stored.execute("INSERT INTO priority_repertoire_source_epochs VALUES('unrelated',37)")
        stored.execute("""CREATE TRIGGER priority_card_updated AFTER UPDATE OF state,introduced_at ON cards
            BEGIN INSERT INTO priority_repertoire_source_epochs(repertoire_id,version)
                SELECT id,1 FROM repertoires WHERE id=NEW.repertoire_id
                  OR EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=NEW.id AND link.repertoire_id=repertoires.id)
                ON CONFLICT(repertoire_id) DO UPDATE SET version=version+1;
            END""")
        unrelated_task = request_queue_refresh_in_transaction(stored, '2099-01-01')
        stored.execute("UPDATE background_tasks SET kind='unrelated',deduplication_key='other' WHERE id=?", (unrelated_task['id'],))
        stored.execute("UPDATE queue_projections SET state='failed',last_error='preserve' WHERE queue_date='2099-01-01'")
        increment(stored, 'other', 'unrelated-metric', claims=17)

    def execute_command(operation_id, command_name, payload):
        assert command_name == 'repertoires.settings.update'
        with connection() as stored:
            result = repertoire_commands.update_repertoire_settings(stored, payload)
            stored.execute('INSERT INTO operation_receipts VALUES(?)', (operation_id,))
        return result

    # Stop after the first real settings handler, before PostgreSQL-only reads.
    monkeypatch.setattr(proof, 'execute_command', execute_command)
    monkeypatch.setattr(proof, 'read_operation', lambda operation_id: {'state': 'complete'})
    return connection, today, tomorrow


def environment_rows(connection):
    with connection() as stored:
        return {
            table: [dict(row) for row in stored.execute(
                f'SELECT * FROM {table} ORDER BY ' + ('kind,shard,slot' if table == 'background_metric_buckets' else '1'),
            )]
            for table in ('background_tasks', 'background_task_events', 'background_metric_buckets', 'queue_projections',
                          'repertoires', 'cards', 'repertoire_cards', 'reviews',
                          'daily_queue', 'operation_receipts',
                          'priority_repertoire_source_epochs', 'priority_source_epoch')
        }


def seed_existing_queue(connection, today):
    with connection() as stored:
        original_task = request_queue_refresh_in_transaction(stored, today)
        stored.execute("UPDATE background_tasks SET generation=42,priority=3,state='leased',phase='reconcile',payload_version=7,payload_json=?,attempt_count=2,max_attempts=8,next_attempt_at='next',lease_token='old-lease',lease_expires_at='expires',last_error='prior-error',created_at='created',started_at='started',completed_at='completed',updated_at='updated' WHERE id=?", ('{"queue_date":"old","checkpoint":12}', original_task['id']))
        stored.execute("UPDATE queue_projections SET state='failed',generation=42,updated_at='previous',refresh_pending=0,last_error='projection-error',blocked_count=9 WHERE queue_date=?", (today,))
        stored.execute('DELETE FROM background_task_events WHERE task_id=?', (original_task['id'],))
        for event_index in range(100):
            stored.execute("INSERT INTO background_task_events(task_id,generation,event,phase,detail,created_at) VALUES(?,42,'historical','reconcile',?,?)", (original_task['id'], str(event_index), today))


@pytest.mark.parametrize('preexisting_queue', [True, False])
def test_repertoire_limit_proof_finally_restores_queue_state_after_assertion_failure(
    proof_environment, monkeypatch, preexisting_queue,
):
    connection, today, _ = proof_environment
    if preexisting_queue:
        seed_existing_queue(connection, today)
    original_rows = environment_rows(connection)
    original_execute = proof.execute_command

    def fail_after_settings_mutation(*arguments):
        original_execute(*arguments)
        fixture_card_id = arguments[2]['repertoire_id'] + '-00'
        with connection() as stored:
            stored.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,0)', (today, fixture_card_id))
            stored.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(?,'correct',?,0,7)", (fixture_card_id, today))
        changed_rows = environment_rows(connection)
        assert changed_rows['background_tasks'] != original_rows['background_tasks']
        assert changed_rows['queue_projections'] != original_rows['queue_projections']
        assert changed_rows['background_task_events'] != original_rows['background_task_events']
        assert changed_rows['background_metric_buckets'] != original_rows['background_metric_buckets']
        raise AssertionError('injected proof failure after durable queue mutation')

    monkeypatch.setattr(proof, 'execute_command', fail_after_settings_mutation)
    with pytest.raises(AssertionError, match='injected proof failure'):
        proof.main_check()
    assert environment_rows(connection) == original_rows


@pytest.mark.parametrize('preexisting_queue', [True, False])
def test_repertoire_limit_proof_restores_pruned_history_both_dates_and_stale_cards(
    proof_environment, preexisting_queue,
):
    connection, today, tomorrow = proof_environment
    if preexisting_queue:
        seed_existing_queue(connection, today)
    with connection() as stored:
        stored.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,last_error) VALUES(?,'failed',19,0,'tomorrow-error')", (tomorrow,))
    original_rows = environment_rows(connection)
    with connection() as stored:
        snapshot = proof.snapshot_queue_environment(stored, (today, tomorrow))
        repertoire_commands.update_repertoire_settings(stored, {'repertoire_id': 'unrelated', 'settings': {'new_cards_per_day': 5}})
        request_queue_refresh_in_transaction(stored, tomorrow)
        proof.main._reset_stale_opening_introductions(stored, tomorrow)
        assert stored.execute("SELECT state FROM cards WHERE id='unrelated-card'").fetchone()[0] == 'new'
        if preexisting_queue:
            assert stored.execute('SELECT * FROM background_task_events WHERE id=?', (snapshot['events'][0]['id'],)).fetchone() is None
        # Restore the fixture-owned override separately, just as main_check deletes its fixtures.
        stored.execute("UPDATE repertoires SET new_cards_per_day=NULL WHERE id='unrelated'")
    with connection() as stored:
        proof.restore_queue_environment(stored, snapshot)
    assert environment_rows(connection) == original_rows


def test_repertoire_limit_proof_cleanup_failure_is_loud_and_atomic(proof_environment, monkeypatch):
    connection, today, _ = proof_environment
    seed_existing_queue(connection, today)
    original_execute = proof.execute_command
    mutated_rows = None

    def fail_after_settings_mutation(*arguments):
        nonlocal mutated_rows
        original_execute(*arguments)
        mutated_rows = environment_rows(connection)
        raise AssertionError('injected body failure')

    def fail_during_restore(stored, snapshot):
        stored.execute("DELETE FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'")
        raise RuntimeError('injected cleanup failure')

    monkeypatch.setattr(proof, 'execute_command', fail_after_settings_mutation)
    monkeypatch.setattr(proof, 'restore_queue_environment', fail_during_restore)
    with pytest.raises(RuntimeError, match='injected cleanup failure') as failure:
        proof.main_check()
    assert isinstance(failure.value.__context__, AssertionError)
    assert environment_rows(connection) == mutated_rows


def test_repertoire_limit_proof_reconciliation_preserves_unrelated_entries(proof_environment, monkeypatch):
    unrelated_candidate = {'id': 1, 'card_id': 'unrelated-card', 'repertoire_id': 'unrelated'}
    fixture_candidate = {'id': 2, 'card_id': 'fixture-card', 'repertoire_id': 'fixture'}
    published_candidates = []

    def prepare(queue_date, processed_ids, introduced_counts):
        for candidate in (unrelated_candidate, fixture_candidate):
            if candidate['id'] not in processed_ids:
                return candidate, 0
        assert introduced_counts == {'fixture': 1}
        return None, 0

    def reconcile(stored, queue_date, candidate, starting_count):
        published_candidates.append(candidate)
        return True

    monkeypatch.setattr(proof, '_prepare_unseen_reconciliation', prepare)
    monkeypatch.setattr(proof, '_reconcile_one_unseen_entry', reconcile)
    proof.reconcile_fixture_entries(proof_environment[2], ['fixture'])
    assert published_candidates == [fixture_candidate]
