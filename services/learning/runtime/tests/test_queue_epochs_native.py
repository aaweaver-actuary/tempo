"""Native queue epoch fences on an invocation-owned Learning database."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from tempo_learning_database.adapters import queue_refresh_epochs as epochs


@pytest.fixture
def schema59(case):
    # Until the integration owner registers 0005, install its exact DDL only in
    # this fixture's isolated database; never mutate an existing target.
    ddl = (Path(__file__).resolve().parents[4] / 'database/tempo_learning_database/ddl/migrations/0005_queue_refresh_epochs.sql').read_text()
    with psycopg.connect(case.admin) as raw:
        raw.execute("SET LOCAL search_path=learning,pg_catalog")
        exists = raw.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='learning' AND table_name='queue_projections' AND column_name='requested_refresh_epoch'").fetchone()
        if not exists:
            raw.execute(ddl)
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
