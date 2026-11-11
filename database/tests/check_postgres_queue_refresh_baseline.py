"""Profile source queue refresh on one existing owned native PostgreSQL fixture.

Run with pytest and repository backend, database, and package src directories on
PYTHONPATH. Requires local postgres:18.6-trixie and Docker; the imported owner
applies checked-in migrations and verifies disposal. No live DSN is accepted.
The 250ms writer rolls back; measurements include Python/loopback overhead and
are diagnostic samples, not packaged/live acceptance or a latency assertion.
"""
from pathlib import Path
import os, sys, time, json, statistics
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'database/tests'), str(ROOT / 'backend/tests')]
import psycopg
from psycopg.conninfo import make_conninfo
from unittest.mock import patch
from test_product_game_lapse_postgres import owned_postgres
from queue_refresh_scale_fixture import seed_queue_refresh_case
from tempo_storage import postgres
from app.services import postgres_queue_refresh as refresh


def test_native_profile(owned_postgres):
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()

    os.environ['TEMPO_TEST_INSTANCE']='disposable'
    conninfo=make_conninfo(**owned_postgres.connection_options)
    os.environ['TEMPO_DATABASE_READ_URL']=conninfo
    os.environ['TEMPO_DATABASE_WRITE_URL']=conninfo
    with psycopg.connect(**owned_postgres.connection_options) as raw:
        facts=seed_queue_refresh_case(raw,queued_count=1258,issued_count=2,changed_kind_count=16)
    stages={}
    def timed(name, fn):
        def wrapper(*a, **kw):
            start=time.perf_counter()
            try: return fn(*a, **kw)
            finally: stages.setdefault(name,[]).append(round((time.perf_counter()-start)*1000,3))
        return wrapper
    original_read=refresh.read_storage._bounded_read
    try:
        with patch.object(refresh.read_storage,'_bounded_read',timed('bounded_query',original_read)), patch.object(refresh.queue_application,'presentation_rows',timed('presentation_rows',refresh.queue_application.presentation_rows)), patch.object(refresh.frozen_opening_order,'record_queue_changes',timed('frozen_changes',refresh.frozen_opening_order.record_queue_changes)), patch.object(refresh.presentation_storage,'apply_plan',timed('apply_plan',refresh.presentation_storage.apply_plan)):
            start=time.perf_counter()
            plan=refresh._prepare_queue_randomization(facts.queue_date)
            prepare=round((time.perf_counter()-start)*1000,3)
            print('PREPARE',prepare, 'entries',len(plan['entries'] or []),'calls',len(stages.get('presentation_rows',[])))
            for scenario in ('prepared','unchanged','reordered','kinds'):
                import copy
                p=copy.deepcopy(plan)
                if scenario in ('unchanged','reordered','kinds'):
                    with psycopg.connect(**owned_postgres.connection_options) as raw:
                        original={x[0]:(x[1],x[2],x[3]) for x in raw.execute("SELECT id,position,card_bucket,admission_kind FROM daily_queue WHERE status='queued'")}
                    for e in p['entries']:
                        e['position'],e['bucket'],e['kind']=original[e['id']]
                    if scenario=='reordered':
                        movable=[e for e in p['entries'] if e['id']>2]
                        positions=[e['position'] for e in movable][::-1]
                        for e,pos in zip(movable,positions): e['position']=pos
                    if scenario=='kinds':
                        for e in p['entries']:
                            if e['id'] in facts.changed_kind_ids: e['kind']='new'
                start=time.perf_counter()
                try:
                    with psycopg.connect(**owned_postgres.connection_options,row_factory=postgres.tempo_row_factory) as raw:
                        raw.execute("SET LOCAL transaction_timeout='250ms'; SET LOCAL lock_timeout='25ms'; SET LOCAL jit=off")
                        result=refresh._publish_queue_randomization(postgres.PostgresConnection(raw),facts.queue_date,p)
                        raw.rollback()
                    print('PUBLISH',scenario,'result',result,'ms',round((time.perf_counter()-start)*1000,3))
                except Exception as e:
                    print('PUBLISH',scenario,'ERROR',type(e).__name__,getattr(e,'sqlstate',None),str(e).splitlines()[0],round((time.perf_counter()-start)*1000,3))
        for name, values in stages.items():
                ordered=sorted(values)
                print('STAGE',name,'count',len(values),'p50_ms',round(statistics.median(values),3),
                      'p95_ms',ordered[min(len(ordered)-1, int(len(ordered)*0.95))], 'max_ms',max(values))
    finally:
        postgres.close_pools()
        os.environ.pop('TEMPO_DATABASE_READ_URL',None);os.environ.pop('TEMPO_DATABASE_WRITE_URL',None)
