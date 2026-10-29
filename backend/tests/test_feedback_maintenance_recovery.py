"""#558: a lost database connection must not strand a claimed Study slice."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json

import psycopg
import pytest

from app import tasks
from app.product_composition import feedback_maintenance
from app.services import durable_tasks
from tempo_legacy_database import connections


@pytest.fixture
def claimed_slice(tmp_path, monkeypatch):
    monkeypatch.delenv('TEMPO_DATABASE_WRITE_URL', raising=False)
    monkeypatch.delenv('TEMPO_DATABASE_READ_URL', raising=False)
    monkeypatch.setattr(connections, 'DB_PATH', tmp_path / 'synthetic.sqlite')
    connections.initialize()
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    monkeypatch.setattr(durable_tasks, '_now', lambda: now)
    def write(operation, **_):
        with connections.connection() as database:
            return operation(database)
    monkeypatch.setattr(durable_tasks, 'submit_background_write', write)
    monkeypatch.setattr(durable_tasks.activity_gate, 'background_control', nullcontext)
    monkeypatch.setattr(tasks.activity_gate, 'background_job', lambda *_, **__: nullcontext())
    monkeypatch.setattr(tasks, '_maintenance_cooldowns', {})
    with connections.connection() as database:
        task = durable_tasks.enqueue_task_in_transaction(database, 'study_game_feedback', 'current', {'after': 'retained'})
        database.execute("UPDATE background_tasks SET state='leased',phase='scan',lease_token='original',attempt_count=1,lease_expires_at=? WHERE id=?",
                         ((now + timedelta(seconds=60)).isoformat(), task['id']))
        task = dict(database.execute('SELECT * FROM background_tasks WHERE id=?', (task['id'],)).fetchone())
    def saved():
        with connections.read_connection() as database:
            return dict(database.execute('SELECT * FROM background_tasks WHERE id=?', (task['id'],)).fetchone())
    monkeypatch.setattr(feedback_maintenance, 'claim_feedback_sweep', lambda: task)
    return task, saved


@pytest.mark.parametrize('error_type', [psycopg.OperationalError, psycopg.InterfaceError, psycopg.errors.TransactionTimeout])
def test_issue558_post_claim_database_failure_releases_same_checkpoint_before_wake(claimed_slice, monkeypatch, error_type):
    task, saved = claimed_slice
    def fail(_):
        raise error_type('private connection details must not be persisted')
    monkeypatch.setattr(feedback_maintenance, 'execute_feedback_sweep', fail)
    wakes = []
    def wake(*args, **options):
        row = saved()
        assert row['state'] == 'retrying' and row['lease_token'] is None
        wakes.append((args, options))
    monkeypatch.setattr(tasks.celery_app, 'send_task', wake)
    assert tasks.maintain_game_feedback.run() is False
    row = saved()
    assert (row['id'], row['generation'], row['phase'], row['payload_json']) == (
        task['id'], task['generation'], task['phase'], task['payload_json'])
    assert row['attempt_count'] == 0 and row['lease_expires_at'] is None
    assert datetime.fromisoformat(row['next_attempt_at']) == durable_tasks._now() + timedelta(seconds=1)
    assert len(wakes) == 1
    if error_type is not psycopg.errors.TransactionTimeout:
        assert row['last_error'] == 'database_connection_unavailable'
        assert row['transaction_timeout_count'] == 0


@pytest.mark.parametrize('committed', [False, True], ids=['new-generation', 'commit-ack-lost'])
def test_issue558_recovery_cannot_overwrite_new_generation_or_committed_progress(claimed_slice, monkeypatch, committed):
    task, saved = claimed_slice
    def fail(_):
        with connections.connection() as database:
            if committed:
                database.execute("UPDATE background_tasks SET state='queued',phase='scan',payload_json=?,lease_token=NULL WHERE id=?",
                                 (json.dumps({'after': 'committed-effect'}), task['id']))
            else:
                database.execute("UPDATE background_tasks SET generation=generation+1,lease_token='successor' WHERE id=?", (task['id'],))
        raise psycopg.OperationalError('uncertain response')
    monkeypatch.setattr(feedback_maintenance, 'execute_feedback_sweep', fail)
    monkeypatch.setattr(tasks.celery_app, 'send_task', lambda *_, **__: pytest.fail('stale recovery must not publish a retry'))
    assert tasks.maintain_game_feedback.run() is False
    row = saved()
    assert row['last_error'] is None
    assert (row['state'] == 'queued' and json.loads(row['payload_json'])['after'] == 'committed-effect') if committed else row['lease_token'] == 'successor'


def test_issue558_failed_recovery_retains_lease_without_retry_loop(claimed_slice, monkeypatch):
    _, saved = claimed_slice
    monkeypatch.setattr(feedback_maintenance, 'execute_feedback_sweep', lambda _: (_ for _ in ()).throw(psycopg.OperationalError('lost')))
    calls = []
    def unavailable(*_, **__):
        calls.append('one fresh recovery attempt')
        raise psycopg.OperationalError('still unavailable')
    monkeypatch.setattr(durable_tasks, 'submit_background_write', unavailable)
    monkeypatch.setattr(tasks.celery_app, 'send_task', lambda *_, **__: pytest.fail('no immediate wake on failed recovery'))
    assert tasks.maintain_game_feedback.run() is False
    assert calls == ['one fresh recovery attempt'] and saved()['lease_token'] == 'original'
