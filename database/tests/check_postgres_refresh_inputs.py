"""Refresh intent coalescing on PostgreSQL with real publications and leases."""

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.connections import background_connection
from app.services import durable_tasks, refresh_requests, postgres_priority
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
from app.services.introduction_priorities import enqueue_priority_refresh_in_transaction


def proof_refresh_inputs(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Refresh coalescing proof requires disposable PostgreSQL')
    import database.tests.check_postgres_graph_retention as fixtures
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url), fixtures.owned_fixture_database() as identity:
        now = datetime.now(timezone.utc)
        repertoire_id = identity + '-scope'
        concurrent_repertoire_id = identity + '-concurrent'
        with postgres_store.connection() as database:
            for target_id in (repertoire_id, concurrent_repertoire_id):
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'Proof','synthetic',?)", (target_id, now.isoformat()))
            first_generation = enqueue_priority_refresh_in_transaction(database, repertoire_id)
        durations = []
        for _ in range(100):
            postgres_store.close_pools()
            started = time.perf_counter()
            with background_connection() as database:
                assert enqueue_priority_refresh_in_transaction(database, repertoire_id) == first_generation
                durations.append(time.perf_counter() - started)
        # The epoch lock serializes first-time requests, before a refresh row exists.
        def concurrent_request(_index):
            from psycopg.errors import LockNotAvailable, TransactionTimeout
            try:
                with postgres_store.connection(background=True) as database:
                    return enqueue_priority_refresh_in_transaction(database, concurrent_repertoire_id)
            except (LockNotAvailable, TransactionTimeout):
                # Current main deliberately refuses contended bounded writes.
                # A rolled-back request is not acknowledged; replay it only
                # after this concurrent cohort has fully released its locks.
                return None
        with ThreadPoolExecutor(max_workers=3) as workers:
            concurrent_results = list(workers.map(concurrent_request, range(12)))
        for result in concurrent_results:
            if result is None:
                with background_connection() as database:
                    result = enqueue_priority_refresh_in_transaction(database, concurrent_repertoire_id)
            assert result == 1
        controlled_time = [now]
        with patch.object(refresh_requests, '_now', lambda: controlled_time[0]), patch.object(durable_tasks, '_now', lambda: controlled_time[0]):
            with background_connection() as database:
                database.execute('DELETE FROM analysis_refresh_requests WHERE repertoire_id=?', (repertoire_id,))
                enqueue_priority_refresh_in_transaction(database, repertoire_id)
                for elapsed_seconds in (4, 8, 56, 59, 60):
                    controlled_time[0] = now + timedelta(seconds=elapsed_seconds)
                    database.execute('UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=?', (repertoire_id,))
                    enqueue_priority_refresh_in_transaction(database, repertoire_id)
                    scheduled_at = datetime.fromisoformat(database.execute('SELECT next_attempt_at FROM repertoire_priority_jobs WHERE repertoire_id=?', (repertoire_id,)).fetchone()[0])
                    assert scheduled_at == min(controlled_time[0] + timedelta(seconds=5), now + timedelta(seconds=60))
                database.execute("UPDATE background_tasks SET priority=-1000 WHERE kind='repertoire_priority' AND deduplication_key=?", (repertoire_id,))
            # Real lease claims and publication close the window in the same commit.
            for _ in range(3):
                task = durable_tasks.claim_task('repertoire_priority')
                assert task and task['deduplication_key'] == repertoire_id
                assert postgres_priority.execute_repertoire_priority_slice(task)
                postgres_store.close_pools()
                with postgres_store.connection(read_only=True) as database:
                    publication = database.execute('SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=?', (repertoire_id,)).fetchone()
                if publication:
                    break
            assert publication and publication[0] == 7
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT pending_since FROM analysis_refresh_requests WHERE kind='repertoire_priority' AND repertoire_id=?", (repertoire_id,)).fetchone()[0] is None
        try:
            with background_connection() as database:
                database.execute('UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=?', (repertoire_id,))
                assert enqueue_priority_refresh_in_transaction(database, repertoire_id) == 8
                raise RuntimeError('crash before commit')
        except RuntimeError as error:
            assert str(error) == 'crash before commit'
        with background_connection() as database:
            assert enqueue_priority_refresh_in_transaction(database, repertoire_id) == 7
            database.execute("UPDATE repertoire_priority_jobs SET status='failed',last_error='retained diagnostic' WHERE repertoire_id=?", (repertoire_id,))
            assert enqueue_priority_refresh_in_transaction(database, repertoire_id) == 7
            assert database.execute('SELECT last_error FROM repertoire_priority_jobs WHERE repertoire_id=?', (repertoire_id,)).fetchone()[0] == 'retained diagnostic'
        with activity_gate.foreground():
            try:
                with background_connection():
                    raise AssertionError('Foreground activity admitted discretionary refresh work')
            except BackgroundAdmissionDeferred:
                pass
        assert max(durations) < 0.25, durations
        print(json.dumps({'test':'test_postgres_refresh_inputs_preserve_cursor_restart_failure_and_atomic_publication',
                          'unchanged_requests':100,'concurrent_initial_requests':12,'maximum_delay_seconds':60,
                          'actual_priority_publication':publication[0],'max_reconnect_transaction_ms':round(max(durations)*1000,3)}))


def proof_refresh_state_recovery(parent_database_url):
    """Retained priority state is stable under repeated unchanged requests."""
    import database.tests.check_postgres_graph_retention as fixtures
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Refresh recovery proof requires disposable PostgreSQL')
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url), fixtures.owned_fixture_database() as identity:
        repertoire_id = identity + '-recovery'
        with postgres_store.connection() as database:
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'Recovery','synthetic',?)",
                             (repertoire_id, datetime.now(timezone.utc).isoformat()))
            enqueue_priority_refresh_in_transaction(database, repertoire_id, quiet_seconds=0)
        for task_state in ('queued', 'leased', 'retrying', 'failed'):
            with postgres_store.connection() as database:
                database.execute("UPDATE background_tasks SET state=?,phase='nodes',attempt_count=3,last_error='retained diagnostic',lease_token=?,lease_expires_at=? WHERE kind='repertoire_priority' AND deduplication_key=?",
                                 (task_state, 'saved-lease' if task_state == 'leased' else None,
                                  (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat() if task_state == 'leased' else None, repertoire_id))
                retained = dict(database.execute("SELECT * FROM background_tasks WHERE kind='repertoire_priority' AND deduplication_key=?", (repertoire_id,)).fetchone())
            postgres_store.close_pools()
            with background_connection() as database:
                assert enqueue_priority_refresh_in_transaction(database, repertoire_id, quiet_seconds=0) == 1
                assert dict(database.execute("SELECT * FROM background_tasks WHERE kind='repertoire_priority' AND deduplication_key=?", (repertoire_id,)).fetchone()) == retained
        print(json.dumps({'test':'test_postgres_priority_refresh_unchanged_requests_preserve_states','states':4}))


def proof_refresh_schema_upgrade(parent_database_url):
    import tempfile
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    import database.tooling.apply_postgres_migrations as migration
    import database.tests.check_postgres_graph_retention as fixtures
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Refresh upgrade proof requires disposable PostgreSQL')
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url), fixtures.owned_fixture_database() as identity:
        upgrade_name = identity + '_v45'
        upgrade_url = make_conninfo(parent_database_url, dbname=upgrade_name)
        with psycopg.connect(parent_database_url, autocommit=True) as parent:
            parent.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(upgrade_name)))
        try:
            with tempfile.TemporaryDirectory(prefix='tempo-pr124-schema45-') as directory:
                historical_migrations = Path(directory)
                for source in migration.MIGRATIONS.glob('*.sql'):
                    if int(source.name[:3]) <= 45:
                        (historical_migrations / source.name).symlink_to(source)
                with patch.object(migration, 'MIGRATIONS', historical_migrations), patch.object(migration, 'POSTGRES_SCHEMA_VERSION', 45):
                    migration.apply_migrations(upgrade_url)
            with psycopg.connect(upgrade_url) as database:
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('historical','Historical','synthetic','2026-01-01')")
                database.execute("INSERT INTO repertoire_priority_jobs(repertoire_id,generation,status,attempts,next_attempt_at,last_error,updated_at) VALUES('historical',9,'failed',3,'2026-01-01','preserve error','2026-01-01')")
            migration.apply_migrations(upgrade_url)
            migration.apply_migrations(upgrade_url)
            with psycopg.connect(upgrade_url) as database:
                assert database.execute("SELECT generation,status,attempts,last_error FROM repertoire_priority_jobs WHERE repertoire_id='historical'").fetchone() == (9, 'failed', 3, 'preserve error')
                assert database.execute('SELECT COUNT(*) FROM analysis_refresh_requests').fetchone()[0] == 0
                assert database.execute('SELECT max(version),count(*) FROM tempo_schema_migrations').fetchone() == (migration.POSTGRES_SCHEMA_VERSION, migration.POSTGRES_SCHEMA_VERSION)
            print(json.dumps({'test': 'test_postgres_refresh_schema45_upgrade_preserves_existing_jobs_and_replays', 'from': 45, 'to': migration.POSTGRES_SCHEMA_VERSION}))
        finally:
            with psycopg.connect(parent_database_url, autocommit=True) as parent:
                parent.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(upgrade_name)))


if __name__ == '__main__':
    proof_refresh_inputs(os.environ['TEMPO_REFRESH_PROOF_URL'])
    proof_refresh_state_recovery(os.environ['TEMPO_REFRESH_PROOF_URL'])
    proof_refresh_schema_upgrade(os.environ['TEMPO_REFRESH_PROOF_URL'])
