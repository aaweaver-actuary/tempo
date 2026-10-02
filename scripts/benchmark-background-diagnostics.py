"""Comparable SQLite compatibility measurements, with temporary synthetic state only."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import database, postgres_store
from app.services import durable_tasks, background_diagnostics


def measure(call, repetitions=30):
    samples=[]
    for _ in range(repetitions):
        started=time.perf_counter()
        call()
        samples.append(time.perf_counter()-started)
    ordered=sorted(samples)
    return {'samples':repetitions,'p50_seconds':statistics.median(samples),'p95_seconds':ordered[int(len(ordered)*.95)-1],
            'max_seconds':max(samples)}


def main():
    if postgres_store.configured():
        raise RuntimeError("Compatibility benchmark requires an environment without PostgreSQL URLs")
    with tempfile.TemporaryDirectory(prefix='tempo-diagnostic-benchmark-') as directory:
        database.DB_PATH=Path(directory)/'synthetic.db'
        database.initialize()
        with database.connection() as connection:
            connection.execute('DELETE FROM background_tasks')
            now=datetime.now(timezone.utc).isoformat()
            connection.executemany("INSERT INTO background_tasks(id,kind,deduplication_key,next_attempt_at,created_at,updated_at) VALUES(?,'daily_queue',?,?,?,?)",
                [(f'job-{index}',f'key-{index}',now,now,now) for index in range(1000)])
        def snapshot():
            result=background_diagnostics.snapshot()
            assert result.available, result
        snapshot()
        empty_history=measure(snapshot)
        with database.connection() as connection:
            connection.executemany("INSERT INTO background_task_events(task_id,generation,event,created_at) VALUES('job-0',1,'synthetic',?)",[(now,)]*100_000)
        large_history=measure(snapshot)
        with database.connection() as connection:
            connection.execute('DELETE FROM background_task_events')
            for _ in range(100):
                durable_tasks._record_event(connection,'job-0',1,'slice_complete',kind='daily_queue')
        def transition(instrumented):
            with database.connection() as connection:
                connection.execute("UPDATE background_tasks SET updated_at=? WHERE id='job-0'",(now,))
                if instrumented:
                    durable_tasks._record_event(connection,'job-0',1,'slice_complete',kind='daily_queue')
                else:
                    connection.execute(durable_tasks._EVENT_INSERT_SQL,('job-0',1,'slice_complete',None,None,now))
                    connection.execute(durable_tasks._EVENT_PRUNE_SQL,('job-0',))
        baseline=measure(lambda:transition(False),100)
        instrumented=measure(lambda:transition(True),100)
        with database.read_connection() as connection:
            plan=[tuple(row) for row in connection.execute('EXPLAIN QUERY PLAN SELECT state,COUNT(*) FROM background_tasks GROUP BY state')]
            bucket_rows=connection.execute('SELECT COUNT(*) FROM background_metric_buckets').fetchone()[0]
        print(json.dumps({'schema_version':1,'environment':'macOS ARM64 SQLite compatibility; not PostgreSQL evidence',
            'current_queue_rows':1000,'snapshot_empty_history':empty_history,'snapshot_100000_events':large_history,
            'transition_baseline':baseline,'transition_instrumented':instrumented,'bucket_rows':bucket_rows,
            'state_count_query_plan':plan},indent=2))

if __name__=='__main__':
    main()
