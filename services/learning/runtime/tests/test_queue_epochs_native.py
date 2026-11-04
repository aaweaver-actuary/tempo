"""Native queue epoch fences on an invocation-owned Learning database."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib

import psycopg
import pytest
from tempo_learning_database.adapters import queue_refresh_epochs as epochs


@pytest.fixture
def schema59(case):
    # The actual owning manifest must apply the exact DDL before API startup.
    ddl = (Path(__file__).resolve().parents[4] / 'database/tempo_learning_database/ddl/migrations/0005_queue_refresh_epochs.sql').read_bytes()
    applied = case.query("SELECT checksum FROM migration_history WHERE id=%s", ("learning.queue_epochs/0001",))
    assert len(applied) == 1 and applied[0][0] == hashlib.sha256(ddl).hexdigest()
    return case


def test_queue_epoch_stale_cycle_cannot_publish_and_quiet_cycle_converges(schema59):
    day = '2026-11-03'
    with schema59.database.scope(), schema59.database.transaction() as db:
        assert epochs.request(db, day) == 1
        assert epochs.request(db, day) == 2
        assert not epochs.publish_if_current(db, day, 1, 'now')
        assert tuple(epochs.lock(db, day)) == (2, 0)
        assert epochs.publish_if_current(db, day, 2, 'now')
    assert tuple(schema59.query('SELECT state,refresh_pending,requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (day,))[0]) == ('ready', 0, 2, 2)


def test_queue_epoch_rollback_and_concurrent_requests_are_durable(schema59):
    day = '2026-11-04'
    with pytest.raises(RuntimeError):
        with schema59.database.scope(), schema59.database.transaction() as db:
            epochs.request(db, day)
            raise RuntimeError('rollback')
    assert not schema59.query('SELECT queue_date FROM queue_projections WHERE queue_date=%s', (day,))

    def request():
        with schema59.database.scope(), schema59.database.transaction() as db:
            return epochs.request(db, day)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: request(), range(2))) == [1, 2]
    assert tuple(schema59.query('SELECT requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (day,))[0]) == (2, 0)


def test_worker_restarts_stale_cycle_and_publishes_only_after_quiet(schema59, monkeypatch):
    from tempo_learning_runtime import durable_tasks, maintenance

    day = '2026-11-05'
    monkeypatch.setattr(maintenance, 'next_candidates', lambda *_: [])
    with schema59.database.scope():
        with schema59.database.transaction() as db:
            durable_tasks.enqueue_task_in_transaction(db, 'daily_queue', 'current', {'queue_date': day})
            epochs.request(db, day)
        with schema59.database.transaction() as db:
            task = durable_tasks.claim(db)
        maintenance.apply_task(task)  # conservatively checkpoint before scanning
        with schema59.database.transaction() as db:
            epochs.request(db, day)  # request arrives after pin
        with schema59.database.transaction() as db:
            task = durable_tasks.claim(db)
        maintenance.apply_task(task)
        assert tuple(schema59.query('SELECT state,requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (day,))[0]) == ('refreshing', 2, 0)
        with schema59.database.transaction() as db:
            task = durable_tasks.claim(db)
        maintenance.apply_task(task)
    assert tuple(schema59.query('SELECT state,requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (day,))[0]) == ('ready', 2, 2)


def test_daily_requests_preserve_live_cursor_lease_and_frozen_source_change_restarts(schema59, monkeypatch):
    import json
    from tempo_learning_runtime import durable_tasks, maintenance

    day = '2026-11-06'
    monkeypatch.setattr(maintenance, 'next_candidates', lambda *_: [])
    with schema59.database.scope():
        with schema59.database.transaction() as db:
            original = durable_tasks.enqueue_task_in_transaction(db, 'daily_queue', 'current', {'queue_date': day})
            epochs.request(db, day)
        with schema59.database.transaction() as db:
            task = durable_tasks.claim(db)
        maintenance.apply_task(task)
        with schema59.database.transaction() as db:
            task = durable_tasks.claim(db)
        for entry_id in (4, 7, 5):
            with schema59.database.transaction() as db:
                again = durable_tasks.enqueue_task_in_transaction(db, 'daily_queue', 'current', {'queue_date': day, 'preserve_through_entry_id': entry_id})
                epochs.request(db, day)
                assert (again['id'], again['generation'], again['lease_token']) == (original['id'], task['generation'], task['lease_token'])
        maintenance.apply_task(task)
        row = schema59.query("SELECT payload_json,state FROM background_tasks WHERE id=%s", (original['id'],))[0]
        payload = json.loads(row[0])
        assert row[1] == 'queued' and payload['preserve_through_entry_id'] == 7
        assert payload['_refresh_cycle_epoch'] == 4
        with schema59.database.transaction() as db:
            db.execute_native('UPDATE frozen_opening_activation SET epoch=epoch+1 WHERE id=1')
        with schema59.database.transaction() as db:
            task = durable_tasks.claim(db)
        maintenance.apply_task(task)
        assert schema59.query('SELECT state FROM queue_projections WHERE queue_date=%s', (day,))[0][0] == 'refreshing'
        with schema59.database.transaction() as db:
            task = durable_tasks.claim(db)
        maintenance.apply_task(task)
    assert tuple(schema59.query('SELECT state,requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (day,))[0]) == ('ready', 4, 4)


def test_late_preserve_through_request_cannot_reorder_stale_cycle(schema59, monkeypatch):
    """A request committed after claim protects its issued prefix immediately."""
    from tempo_learning_runtime import durable_tasks, maintenance

    day = '2026-11-07'
    with schema59.database.scope(), schema59.database.transaction() as db:
        db.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','rep','test','now')")
        for card in ('a', 'b'):
            db.execute_native("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(%s,'rep','checkpoint','fen','[]',%s)", (card, day))
        rows = [db.execute_native("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,%s) RETURNING id", (day, card, pos)).fetchone()[0] for pos, card in enumerate(('a', 'b'))]
        durable_tasks.enqueue_task_in_transaction(db, 'daily_queue', 'current', {'queue_date': day})
        epochs.request(db, day)
    monkeypatch.setattr(maintenance, 'next_candidates', lambda *_: [])
    monkeypatch.setattr(maintenance.queue_application, 'presentation_rows', lambda *_: [dict(id=rows[0], position=0), dict(id=rows[1], position=1)])
    monkeypatch.setattr(maintenance.queue_ordering, '_queue_membership_hash', lambda *_: 'members')
    monkeypatch.setattr(maintenance.queue_ordering, '_plan_daily_queue_order', lambda entries, *_args, **_kwargs: (1, None, list(reversed(entries))))
    with schema59.database.scope():
        with schema59.database.transaction() as db:
            task = durable_tasks.claim(db)
        maintenance.apply_task(task)
        with schema59.database.transaction() as db:
            stale_task = durable_tasks.claim(db)
        with schema59.database.transaction() as db:
            durable_tasks.enqueue_task_in_transaction(db, 'daily_queue', 'current', {'queue_date': day, 'preserve_through_entry_id': rows[1]})
            epochs.request(db, day)
        maintenance.apply_task(stale_task)
    actual = schema59.query('SELECT id,position FROM daily_queue WHERE queue_date=%s ORDER BY id', (day,))
    assert [tuple(row) for row in actual] == [(rows[0], 0), (rows[1], 1)]
    assert schema59.query('SELECT state FROM queue_projections WHERE queue_date=%s', (day,))[0][0] == 'refreshing'


def test_expired_worker_cannot_publish_or_steal_replacement_lease(schema59, monkeypatch):
    """A resumed stale worker cannot commit after its lease is reclaimed."""
    from tempo_learning_runtime import durable_tasks, maintenance

    day = '2026-11-08'
    monkeypatch.setattr(maintenance, 'next_candidates', lambda *_: [])
    monkeypatch.setattr(maintenance.queue_application, 'presentation_rows', lambda *_: [])
    with schema59.database.scope():
        with schema59.database.transaction() as db:
            db.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','rep','test','now')")
            db.execute_native("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES('a','rep','checkpoint','fen','[]',%s)", (day,))
            entry = db.execute_native("INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES(%s,'a',0,'issued') RETURNING id", (day,)).fetchone()[0]
            original = durable_tasks.enqueue_task_in_transaction(db, 'daily_queue', 'current', {'queue_date': day, 'preserve_through_entry_id': entry})
            epochs.request(db, day)
        with schema59.database.transaction() as db:
            first = durable_tasks.claim(db)
        maintenance.apply_task(first)
        with schema59.database.transaction() as db:
            stale = durable_tasks.claim(db)
            db.execute_native("UPDATE background_tasks SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=%s", (original['id'],))
        with schema59.database.transaction() as db:
            current = durable_tasks.claim(db)
        assert current['id'] == stale['id'] == original['id']
        assert current['generation'] == stale['generation']
        assert current['lease_token'] != stale['lease_token']
        before = [tuple(row) for row in schema59.query('SELECT id,position,status,attempt_state,attempt_failed FROM daily_queue WHERE id=%s', (entry,))]
        with pytest.raises(maintenance.SupersededWork):
            maintenance.apply_task(stale)
        lease = schema59.query('SELECT state,generation,lease_token FROM background_tasks WHERE id=%s', (original['id'],))[0]
        assert tuple(lease) == ('leased', current['generation'], current['lease_token'])
        assert [tuple(row) for row in schema59.query('SELECT id,position,status,attempt_state,attempt_failed FROM daily_queue WHERE id=%s', (entry,))] == before
        assert tuple(schema59.query('SELECT requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (day,))[0]) == (1, 0)
        maintenance.apply_task(current)
    assert tuple(schema59.query('SELECT state,requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (day,))[0]) == ('ready', 1, 1)
    assert [tuple(row) for row in schema59.query('SELECT id,position,status,attempt_state,attempt_failed FROM daily_queue WHERE id=%s', (entry,))] == before
