"""Retention must spend bounded row batches, not one scheduler turn per child."""

from contextlib import contextmanager
import pytest

from app import database
from app.canonical_prefix_api import save_prefix
from app.services import canonical_prefix_preview as previews
from app.services.durable_tasks import claim_task, requeue_interrupted_tasks
from test_canonical_repertoire_prefix import prefix_database
from scripts.check_postgres_canonical_retention import (
    RetentionMeasurements, seed_retention_fixture, run_retention_fixture,
)


@pytest.fixture
def retention_fixture(prefix_database):
    with database.connection() as connection:
        return seed_retention_fixture(connection, prefix_database)


def test_canonical_prefix_retention_converges_within_three_admitted_slices(retention_fixture):
    measurements = run_retention_fixture(retention_fixture, maximum_slices=3)
    assert sum(measurement['cleanup_rows'] for measurement in measurements.slices) == 120
    assert max(measurement['cleanup_rows'] for measurement in measurements.slices) <= 64
    with database.read_connection() as connection:
        assert {row[0] for row in connection.execute('SELECT id FROM canonical_prefix_previews')} == retention_fixture['retained_ids']
        for preview_id, task_id in zip(retention_fixture['preview_ids'], retention_fixture['task_ids']):
            retained = preview_id in retention_fixture['retained_ids']
            assert bool(connection.execute('SELECT 1 FROM background_tasks WHERE id=?', (task_id,)).fetchone()) == retained
            for table in ('canonical_prefix_results', 'canonical_prefix_positions'):
                assert bool(connection.execute(f'SELECT 1 FROM {table} WHERE preview_id=?', (preview_id,)).fetchone()) == retained
            assert bool(connection.execute('SELECT 1 FROM background_task_events WHERE task_id=?', (task_id,)).fetchone()) == retained


def cleanup_snapshot(fixture):
    with database.read_connection() as connection:
        snapshot = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY 1,2')]
                    for table in ('canonical_prefix_previews', 'canonical_prefix_results', 'canonical_prefix_positions')}
        snapshot['tasks'] = [tuple(row) for row in connection.execute(
            "SELECT * FROM background_tasks WHERE kind='canonical_prefix_preview' AND id<>? ORDER BY id",
            (fixture['owner_task_id'],))]
        snapshot['events'] = [tuple(row) for row in connection.execute(
            'SELECT * FROM background_task_events WHERE task_id<>? ORDER BY id', (fixture['owner_task_id'],))]
    return snapshot


def after_preparation(monkeypatch, action):
    original_read = previews.background_read_connection

    @contextmanager
    def raced_read(**options):
        with original_read(**options) as connection:
            yield connection
        action()
    monkeypatch.setattr(previews, 'background_read_connection', raced_read)


@pytest.mark.parametrize('change', ['create', 'activate', 'source'])
def test_canonical_prefix_retention_rechecks_creation_activation_and_source_changes(retention_fixture, monkeypatch, change):
    task = claim_task('canonical_prefix_preview')
    protected_id = retention_fixture['obsolete_ids'][0] if change == 'activate' else retention_fixture['active_id']
    created_ids = []

    def foreground_change():
        with database.connection() as connection:
            if change == 'create':
                created_ids.append(previews.request_preview(connection, 'italian', ['d2d4'])['preview_id'])
            elif change == 'activate':
                connection.execute("UPDATE canonical_prefix_previews SET state='ready' WHERE id=?", (protected_id,))
                save_prefix(connection, {'repertoire_id': 'italian', 'request': {
                    'preview_id': protected_id, 'expected_revision': 1}})
            else:
                connection.execute("UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id='italian'")
    with monkeypatch.context() as race:
        after_preparation(race, foreground_change)
        assert previews.execute_prefix_preview_slice(task)
    with database.read_connection() as connection:
        assert connection.execute('SELECT 1 FROM canonical_prefix_previews WHERE id=?', (protected_id,)).fetchone()
        assert connection.execute('SELECT COUNT(*) FROM canonical_prefix_results WHERE preview_id=?', (protected_id,)).fetchone()[0] == 4
        assert connection.execute('SELECT COUNT(*) FROM canonical_prefix_positions WHERE preview_id=?', (protected_id,)).fetchone()[0] == 11
        newest_ids = {row[0] for row in connection.execute(
            "SELECT id FROM canonical_prefix_previews WHERE repertoire_id='italian' ORDER BY created_at DESC,id DESC LIMIT 8")}
    run_retention_fixture(retention_fixture, maximum_slices=3)
    with database.read_connection() as connection:
        remaining_ids = {row[0] for row in connection.execute('SELECT id FROM canonical_prefix_previews')}
    assert remaining_ids == newest_ids | {protected_id}
    assert set(created_ids) <= remaining_ids


@pytest.mark.parametrize('old_task_state', ['queued', 'leased', 'superseded', 'missing'])
def test_canonical_prefix_retention_handles_retired_tasks_and_partial_children(retention_fixture, old_task_state):
    obsolete_id = retention_fixture['obsolete_ids'][0]
    obsolete_task_id = retention_fixture['task_ids'][1]
    with database.connection() as connection:
        connection.execute('DELETE FROM canonical_prefix_results WHERE preview_id=?', (obsolete_id,))
        connection.execute('DELETE FROM canonical_prefix_positions WHERE preview_id=? AND ply<5', (obsolete_id,))
        if old_task_state == 'missing':
            connection.execute('DELETE FROM background_tasks WHERE id=?', (obsolete_task_id,))
        else:
            connection.execute('UPDATE background_tasks SET state=?,lease_token=?,lease_expires_at=? WHERE id=?',
                               (old_task_state, 'old-lease', '2099-01-01', obsolete_task_id))
    measurements = run_retention_fixture(retention_fixture, maximum_slices=3)
    assert max(item['cleanup_rows'] for item in measurements.slices) <= 64
    with database.read_connection() as connection:
        assert {row[0] for row in connection.execute('SELECT id FROM canonical_prefix_previews')} == retention_fixture['retained_ids']
        assert not connection.execute('SELECT 1 FROM background_task_events WHERE task_id=?', (obsolete_task_id,)).fetchone()


def test_canonical_prefix_retention_fences_leased_victim_before_partial_child_cleanup(retention_fixture):
    owner = claim_task('canonical_prefix_preview')
    obsolete_id = retention_fixture['obsolete_ids'][0]
    obsolete_task_id = retention_fixture['task_ids'][1]
    with database.connection() as connection:
        connection.execute('UPDATE background_tasks SET priority=-2000 WHERE id=?', (obsolete_task_id,))
        for ordinal in range(70):
            connection.execute(
                'INSERT INTO canonical_prefix_results(preview_id,item_id,name,status) VALUES(?,?,?,?)',
                (obsolete_id, f'abandoned-line:{ordinal}', f'Abandoned line {ordinal}', 'valid'))
    victim_delivery = claim_task('canonical_prefix_preview')
    assert victim_delivery['id'] == obsolete_task_id
    assert previews.execute_prefix_preview_slice(owner)
    with database.read_connection() as connection:
        retired_task = connection.execute('SELECT * FROM background_tasks WHERE id=?', (obsolete_task_id,)).fetchone()
        assert retired_task['state'] == 'superseded'
        assert retired_task['generation'] == victim_delivery['generation'] + 1
        assert retired_task['lease_token'] is None
        assert retired_task['lease_expires_at'] is None
        assert connection.execute('SELECT state FROM canonical_prefix_previews WHERE id=?', (obsolete_id,)).fetchone()[0] == 'stale'
        assert connection.execute('SELECT 1 FROM canonical_prefix_results WHERE preview_id=?', (obsolete_id,)).fetchone()
    after_retirement = cleanup_snapshot(retention_fixture)
    assert not previews.execute_prefix_preview_slice(victim_delivery)
    assert cleanup_snapshot(retention_fixture) == after_retirement
    run_retention_fixture(retention_fixture, maximum_slices=3)


@pytest.mark.parametrize('replacement', ['generation', 'lease', 'removed'])
def test_canonical_prefix_retention_rejects_replaced_generation_lease_or_owner(retention_fixture, monkeypatch, replacement):
    task = claim_task('canonical_prefix_preview')
    snapshot = cleanup_snapshot(retention_fixture)

    def replace_ownership():
        with database.connection() as connection:
            if replacement == 'generation':
                connection.execute('UPDATE background_tasks SET generation=generation+1 WHERE id=?', (task['id'],))
            elif replacement == 'lease':
                connection.execute("UPDATE background_tasks SET lease_token='replacement' WHERE id=?", (task['id'],))
            else:
                connection.execute('DELETE FROM background_tasks WHERE id=?', (task['id'],))
    after_preparation(monkeypatch, replace_ownership)
    measurements = RetentionMeasurements(task['id'])
    with measurements.capture() as measurement:
        assert not previews.execute_prefix_preview_slice(task)
    assert measurement['cleanup_rows'] == 0
    assert cleanup_snapshot(retention_fixture) == snapshot


def test_canonical_prefix_retention_partial_batch_restart_and_stale_replay_are_idempotent(retention_fixture):
    first = claim_task('canonical_prefix_preview')
    assert previews.execute_prefix_preview_slice(first)
    snapshot = cleanup_snapshot(retention_fixture)
    assert not previews.execute_prefix_preview_slice(first)
    assert cleanup_snapshot(retention_fixture) == snapshot
    interrupted = claim_task('canonical_prefix_preview')
    with database.connection() as connection:
        checkpoint = connection.execute('SELECT payload_json FROM background_tasks WHERE id=?', (interrupted['id'],)).fetchone()[0]
        connection.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01' WHERE id=?", (interrupted['id'],))
    from app.services.database_executor import database_writer
    database_writer.stop()
    database_writer.start()
    requeue_interrupted_tasks()
    assert not previews.execute_prefix_preview_slice(interrupted)
    assert cleanup_snapshot(retention_fixture) == snapshot
    with database.read_connection() as connection:
        assert connection.execute('SELECT payload_json FROM background_tasks WHERE id=?', (interrupted['id'],)).fetchone()[0] == checkpoint
    run_retention_fixture(retention_fixture, maximum_slices=2)


def test_canonical_prefix_retention_rolls_back_cleanup_with_its_checkpoint(retention_fixture, monkeypatch):
    task = claim_task('canonical_prefix_preview')
    snapshot = cleanup_snapshot(retention_fixture)

    def interrupted_checkpoint(*args, **kwargs):
        raise RuntimeError('controlled retention checkpoint interruption')
    with monkeypatch.context() as checkpoint_patch:
        checkpoint_patch.setattr(previews, 'advance_task_slice_in_transaction', interrupted_checkpoint)
        with pytest.raises(RuntimeError, match='checkpoint interruption'):
            previews.execute_prefix_preview_slice(task)
    assert cleanup_snapshot(retention_fixture) == snapshot
    assert previews.execute_prefix_preview_slice(task)
    run_retention_fixture(retention_fixture, maximum_slices=2)


def test_canonical_prefix_retention_limits_victims_even_when_children_are_empty(retention_fixture):
    with database.connection() as connection:
        for ordinal in range(10):
            connection.execute('UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=?', ('italian',))
            previews.request_preview(connection, 'italian', ['d2d4'])
        # Empty children isolate the independent four-victim bound.
        connection.execute('DELETE FROM canonical_prefix_results')
        connection.execute('DELETE FROM canonical_prefix_positions')
        connection.execute('DELETE FROM background_task_events')
    task = claim_task('canonical_prefix_preview')
    assert previews.execute_prefix_preview_slice(task)
    with database.read_connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM canonical_prefix_previews').fetchone()[0] == 22 - 4
