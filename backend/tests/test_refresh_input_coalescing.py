"""Unchanged refresh intent preserves useful calculation generations."""
from datetime import datetime, timedelta, timezone

import pytest

from app import database
from app.services.introduction_priorities import enqueue_priority_refresh_in_transaction
from app.services.repertoire_opportunities import enqueue_opportunity_refresh_in_transaction
from app.services import durable_tasks, introduction_priorities, refresh_requests


@pytest.fixture
def refresh_store(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'refresh-inputs.sqlite')
    database.initialize()
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('scope','Scope','fixture',?)",
                           (datetime.now(timezone.utc).isoformat(),))


def test_unchanged_priority_requests_preserve_current_generation_and_failure(refresh_store):
    with database.connection() as connection:
        first = enqueue_priority_refresh_in_transaction(connection, 'scope')
        for _ in range(100):
            assert enqueue_priority_refresh_in_transaction(connection, 'scope') == first
        connection.execute("UPDATE repertoire_priority_jobs SET status='failed',last_error='retained diagnostic' WHERE repertoire_id='scope'")
        assert enqueue_priority_refresh_in_transaction(connection, 'scope') == first
        assert tuple(connection.execute('SELECT status,last_error FROM repertoire_priority_jobs').fetchone()) == ('failed', 'retained diagnostic')


def test_unchanged_opportunity_requests_do_not_replace_a_saved_cursor(refresh_store):
    with database.connection() as connection:
        enqueue_opportunity_refresh_in_transaction(connection, 'scope')
        connection.execute("UPDATE background_tasks SET payload_json='{" + '"repertoire_id":"scope","phase":"nodes","cursor":"saved"' + "}',phase='nodes' WHERE kind='repertoire_opportunity'")
        for _ in range(100):
            enqueue_opportunity_refresh_in_transaction(connection, 'scope')
        row = connection.execute("SELECT generation,phase,payload_json FROM background_tasks WHERE kind='repertoire_opportunity'").fetchone()
        assert row['generation'] == 1 and row['phase'] == 'nodes'
        assert 'saved' in row['payload_json']


def test_changed_inputs_wait_for_quiet_but_cannot_postpone_beyond_sixty_seconds(refresh_store, monkeypatch):
    started_at = datetime(2026, 10, 9, tzinfo=timezone.utc)
    current_time = [started_at]
    monkeypatch.setattr(refresh_requests, '_now', lambda: current_time[0])
    monkeypatch.setattr(durable_tasks, '_now', lambda: current_time[0])
    monkeypatch.setattr(introduction_priorities, '_now', lambda: current_time[0].isoformat())
    with database.connection() as connection:
        enqueue_priority_refresh_in_transaction(connection, 'scope')
        assert connection.execute('SELECT next_attempt_at FROM repertoire_priority_jobs').fetchone()[0] == (started_at + timedelta(seconds=5)).isoformat()
        for elapsed_seconds in (4, 8, 56, 59, 60):
            current_time[0] = started_at + timedelta(seconds=elapsed_seconds)
            connection.execute("UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id='scope'")
            enqueue_priority_refresh_in_transaction(connection, 'scope')
            scheduled_at = datetime.fromisoformat(connection.execute('SELECT next_attempt_at FROM repertoire_priority_jobs').fetchone()[0])
            assert scheduled_at == min(current_time[0] + timedelta(seconds=5), started_at + timedelta(seconds=60))
    assert introduction_priorities.claim_priority_refresh()['generation'] == 6
    with database.connection() as connection:
        assert connection.execute("SELECT pending_since FROM analysis_refresh_requests WHERE kind='repertoire_priority'").fetchone()[0] is None
        current_time[0] += timedelta(seconds=1)
        connection.execute("UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id='scope'")
        enqueue_priority_refresh_in_transaction(connection, 'scope')
        assert connection.execute('SELECT next_attempt_at FROM repertoire_priority_jobs').fetchone()[0] == (current_time[0] + timedelta(seconds=5)).isoformat()


def test_unchanged_input_window_survives_restart_without_extending_deadline(refresh_store, monkeypatch):
    with database.connection() as connection:
        first_generation = enqueue_priority_refresh_in_transaction(connection, 'scope')
        first_deadline = connection.execute('SELECT next_attempt_at FROM repertoire_priority_jobs').fetchone()[0]
    database.initialize()
    with database.connection() as connection:
        assert enqueue_priority_refresh_in_transaction(connection, 'scope') == first_generation
        assert connection.execute('SELECT next_attempt_at FROM repertoire_priority_jobs').fetchone()[0] == first_deadline


def test_opportunity_input_changes_only_when_priority_is_published(refresh_store):
    with database.connection() as connection:
        enqueue_priority_refresh_in_transaction(connection, 'scope')
        enqueue_opportunity_refresh_in_transaction(connection, 'scope')
        connection.execute("UPDATE repertoire_priority_jobs SET generation=generation+1,updated_at='heartbeat'")
        enqueue_opportunity_refresh_in_transaction(connection, 'scope')
        assert connection.execute("SELECT generation FROM background_tasks WHERE kind='repertoire_opportunity'").fetchone()[0] == 1
        connection.execute("INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at) VALUES('scope',2,'published')")
        enqueue_opportunity_refresh_in_transaction(connection, 'scope')
        assert connection.execute("SELECT generation FROM background_tasks WHERE kind='repertoire_opportunity'").fetchone()[0] == 2


def test_noop_source_updates_do_not_replace_calculations(refresh_store):
    with database.connection() as connection:
        first_generation = enqueue_priority_refresh_in_transaction(connection, 'scope')
        connection.execute("UPDATE repertoires SET scope_source_revision=scope_source_revision WHERE id='scope'")
        connection.execute('UPDATE settings SET coverage_path_floor=coverage_path_floor WHERE id=1')
        assert enqueue_priority_refresh_in_transaction(connection, 'scope') == first_generation


def test_refresh_rollback_does_not_consume_changed_intent(refresh_store):
    with pytest.raises(RuntimeError), database.connection() as connection:
        enqueue_priority_refresh_in_transaction(connection, 'scope')
        raise RuntimeError('simulated process interruption')
    with database.connection() as connection:
        assert enqueue_priority_refresh_in_transaction(connection, 'scope') == 1


def test_missing_refresh_source_is_actionable_and_creates_no_work(refresh_store):
    with pytest.raises(KeyError, match='Repertoire source is unavailable'), database.connection() as connection:
        enqueue_priority_refresh_in_transaction(connection, 'missing')
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM analysis_refresh_requests').fetchone()[0] == 0
