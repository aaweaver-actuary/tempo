"""Issue #37 proof on a runner-owned database; never inspect or reset live queues."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from scripts.apply_postgres_migrations import apply_migrations
from app import postgres_store
from app.services import durable_tasks, background_diagnostics
from app.services.background_metrics import increment

ADMIN_DSN = 'postgresql://postgres@postgres:5432/postgres'


def main():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Background diagnostics proof requires the disposable test runner')
    database_name='tempo_diagnostics_'+uuid.uuid4().hex
    dsn=f'postgresql://postgres@postgres:5432/{database_name}'
    with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    try:
        apply_migrations(dsn)
        apply_migrations(dsn)
        os.environ['TEMPO_DATABASE_WRITE_URL']=dsn
        os.environ['TEMPO_DATABASE_READ_URL']=dsn
        os.environ.pop('TEMPO_REDIS_URL',None)
        os.environ.pop('TEMPO_FOREGROUND_ACTIVITY_URL',None)
        row=durable_tasks.enqueue_task('daily_queue','synthetic',{})
        claimed=durable_tasks.claim_task('daily_queue')
        with postgres_store.connection() as connection:
            durable_tasks.advance_task_slice_in_transaction(connection,claimed,next_phase='synthetic',next_payload={})
        assert background_diagnostics.snapshot().available
        claimed=durable_tasks.claim_task('daily_queue')
        assert durable_tasks.complete_task(claimed['id'],claimed['generation'],claimed['lease_token'])
        assert not durable_tasks.complete_task(claimed['id'],claimed['generation'],claimed['lease_token'])
        replacement=durable_tasks.enqueue_task('daily_queue','synthetic',{})
        pending_since=replacement['pending_since']
        replaced=durable_tasks.enqueue_task('daily_queue','synthetic',{})
        assert replaced['pending_since']==pending_since
        claimed=durable_tasks.claim_task('daily_queue')
        assert durable_tasks.defer_task_for_contention(claimed['id'],claimed['generation'],claimed['lease_token'])
        with postgres_store.connection() as connection:
            assert connection.execute('SELECT pending_since FROM background_tasks WHERE id=?',(row['id'],)).fetchone()[0]==pending_since
            connection.execute('UPDATE background_tasks SET next_attempt_at=? WHERE id=?',('2000-01-01T00:00:00+00:00',row['id']))
        claimed=durable_tasks.claim_task('daily_queue')
        with postgres_store.connection() as connection:
            connection.execute('UPDATE background_tasks SET lease_expires_at=? WHERE id=?',('2000-01-01T00:00:00+00:00',row['id']))
        durable_tasks.claim_task('daily_queue')
        snapshot=background_diagnostics.snapshot()
        assert snapshot.available
        counters=next(item.counts for item in snapshot.counters if item.kind=='daily_queue')
        assert counters.completed_generations==1 and counters.slices==1
        assert counters.generation_replacements==1 and counters.lease_reclaims==1
        assert counters.contention_deferrals==1 and counters.stale_results==1
        # All metric increments roll back with their associated outcome.
        try:
            with postgres_store.connection() as connection:
                increment(connection,'engine_game','rollback',engine_completed_positions=9)
                raise RuntimeError('synthetic rollback')
        except RuntimeError:
            pass
        assert not any(item.kind=='engine_game' for item in background_diagnostics.snapshot().counters)
        # Concurrent additive UPSERTs, including deliberate same-shard contention, lose no counters.
        def worker(index):
            for _ in range(20):
                with postgres_store.connection() as connection:
                    increment(connection,'engine_game','same-shard',engine_preemptions=1)
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(worker,range(4)))
        assert next(item.counts for item in background_diagnostics.snapshot().counters if item.kind=='engine_game').engine_preemptions==80
        def times():
            samples=[]
            for _ in range(20):
                sample=background_diagnostics.snapshot()
                assert sample.available, sample
                samples.append(sample.query_duration_seconds)
            return samples
        before=times()
        with postgres_store.connection() as connection:
            connection.execute_native("INSERT INTO background_task_events(task_id,generation,event,created_at) "
                "SELECT %s,1,'synthetic',%s FROM generate_series(1,100000)",(row['id'],row['created_at']))
        after=times()
        with postgres_store.connection() as connection:
            durable_tasks._record_event(connection,row['id'],replaced['generation'],'slice_complete')
            assert connection.execute('SELECT COUNT(*) FROM background_task_events WHERE task_id=?',(row['id'],)).fetchone()[0]==100
            for offset in range(600):
                increment(connection,'engine_defense','bounded',now=datetime(2026,10,2,tzinfo=timezone.utc)+timedelta(seconds=offset*300),claims=1)
            assert connection.execute("SELECT COUNT(*) FROM background_metric_buckets WHERE kind='engine_defense'").fetchone()[0]==288
        def write_times(instrumented):
            samples=[]
            for _ in range(50):
                started=time.perf_counter()
                with postgres_store.connection(background=True) as connection:
                    connection.execute_native("UPDATE background_tasks SET updated_at=%s WHERE id=%s",(datetime.now(timezone.utc).isoformat(),row['id']))
                    connection.execute(durable_tasks._EVENT_INSERT_SQL,(row['id'],replaced['generation'],'synthetic',None,None,row['created_at']))
                    connection.execute(durable_tasks._EVENT_PRUNE_SQL,(row['id'],))
                    if instrumented:
                        increment(connection,'daily_queue',row['id'],slices=1)
                samples.append(time.perf_counter()-started)
            return samples
        baseline_writes=write_times(False)
        instrumented_writes=write_times(True)
        print(json.dumps({'regression':'background_snapshot_cost_is_independent_of_event_history',
                          'before_seconds':before,'after_100000_events_seconds':after,
                          'baseline_transition_seconds':baseline_writes,'instrumented_transition_seconds':instrumented_writes,
                          'query_deadline_seconds':0.1,'counter_replay':'passed','bounded_buckets':'passed'}))
    finally:
        postgres_store.close_pools()
        with psycopg.connect(ADMIN_DSN,autocommit=True) as administrator:
            administrator.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))


if __name__=='__main__':
    main()
