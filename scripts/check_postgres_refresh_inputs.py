"""Refresh intent coalescing on PostgreSQL with real publications and leases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store
from app.database import background_connection
from app.services import durable_tasks, refresh_requests, postgres_priority
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
from app.services.introduction_priorities import enqueue_priority_refresh_in_transaction
from app.services.repertoire_opportunities import enqueue_opportunity_refresh_in_transaction


def proof_refresh_inputs(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Refresh coalescing proof requires disposable PostgreSQL')
    import check_postgres_graph_retention as fixtures
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url), fixtures.owned_fixture_database() as identity:
        now = datetime.now(timezone.utc)
        repertoire_id = identity + '-scope'
        concurrent_repertoire_id = identity + '-concurrent'
        with postgres_store.connection() as database:
            for target_id in (repertoire_id, concurrent_repertoire_id):
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'Proof','synthetic',?)", (target_id, now.isoformat()))
            first_generation = enqueue_priority_refresh_in_transaction(database, repertoire_id)
            enqueue_opportunity_refresh_in_transaction(database, repertoire_id)
            database.execute("UPDATE background_tasks SET phase='nodes',payload_json=? WHERE kind='repertoire_opportunity' AND deduplication_key=?",
                             (json.dumps({'repertoire_id':repertoire_id,'phase':'nodes','cursor':'saved'}), repertoire_id))
        durations = []
        for _ in range(100):
            postgres_store.close_pools()
            started = time.perf_counter()
            with background_connection() as database:
                assert enqueue_priority_refresh_in_transaction(database, repertoire_id) == first_generation
                enqueue_opportunity_refresh_in_transaction(database, repertoire_id)
            durations.append(time.perf_counter() - started)
        with postgres_store.connection(read_only=True) as database:
            row = database.execute("SELECT generation,phase,payload_json FROM background_tasks WHERE kind='repertoire_opportunity' AND deduplication_key=?", (repertoire_id,)).fetchone()
            assert row['generation'] == 1 and row['phase'] == 'nodes' and json.loads(row['payload_json'])['cursor'] == 'saved'
        # The epoch lock serializes first-time requests, before a refresh row exists.
        def concurrent_request(_index):
            with postgres_store.connection(background=True) as database:
                return enqueue_priority_refresh_in_transaction(database, concurrent_repertoire_id)
        with ThreadPoolExecutor(max_workers=3) as workers:
            assert list(workers.map(concurrent_request, range(12))) == [1] * 12
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
            opportunity = database.execute("SELECT generation,phase,payload_json FROM background_tasks WHERE kind='repertoire_opportunity' AND deduplication_key=?", (repertoire_id,)).fetchone()
            assert opportunity['generation'] == 2 and json.loads(opportunity['payload_json'])['phase'] == 'summaries'
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


if __name__ == '__main__':
    proof_refresh_inputs(os.environ['TEMPO_REFRESH_PROOF_URL'])
