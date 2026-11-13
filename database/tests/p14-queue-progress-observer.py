"""Bounded read-only observation of the owning disposable Study queue."""
import json
import os
import re
import select
import sys
import time
from datetime import datetime, timezone

import psycopg
import redis

project = os.environ.get('TEMPO_P14_OBSERVER_PROJECT', '')
assert os.environ.get('TEMPO_TEST_INSTANCE') == 'disposable'
assert re.fullmatch(r'tempo-pg-regressions-[0-9]+-[0-9a-f]{8}', project)
# The service receives this credential from the owned Compose secret. Never print it.
dsn = os.environ['TEMPO_DATABASE_READ_URL']
assert dsn == 'postgresql://tempo_reader@postgres:5432/tempo'
assert os.environ.get('TEMPO_REDIS_URL') == 'redis://redis:6379/0'
from psycopg.rows import dict_row
from app.services.background_wakes import ownership_keys


def emit(record):
    print(json.dumps(record, default=str), flush=True)


def sample():
    result = {'at': datetime.now(timezone.utc).isoformat()}
    try:
        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=2) as db:
            db.execute('SET TRANSACTION READ ONLY')
            db.execute("SET LOCAL statement_timeout='1500ms'")
            marker = db.execute("SELECT current_database(), shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=current_database()").fetchone()
            if marker['current_database'] != 'tempo' or marker['marker'] != 'tempo-disposable-postgres-test':
                raise RuntimeError('Unowned database marker')
            result['tasks'] = db.execute("""SELECT id,deduplication_key,generation,state,phase,payload_json,attempt_count,max_attempts,next_attempt_at,lease_token,lease_expires_at,(last_error IS NOT NULL) AS has_error,md5(last_error) AS error_digest,created_at,updated_at FROM background_tasks WHERE kind='daily_queue' ORDER BY id LIMIT 100""").fetchall()
            result['events'] = db.execute("""SELECT e.task_id,e.generation,e.event,e.phase,(e.detail IS NOT NULL) AS has_detail,md5(e.detail) AS detail_digest,e.created_at FROM background_task_events e JOIN background_tasks t ON t.id=e.task_id WHERE t.kind='daily_queue' ORDER BY e.id DESC LIMIT 100""").fetchall()
            result['projections'] = db.execute("SELECT queue_date,state,generation,refresh_pending,requested_refresh_epoch,published_refresh_epoch,(last_error IS NOT NULL) AS has_error,md5(last_error) AS error_digest,updated_at FROM queue_projections ORDER BY queue_date DESC LIMIT 10").fetchall()
            result['activity'] = db.execute("""SELECT pid,application_name,state,wait_event_type,wait_event,query_start,md5(query) AS query_digest FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() ORDER BY pid LIMIT 40""").fetchall()
            result['locks'] = db.execute("""SELECT pid,locktype,mode,granted,relation::regclass::text AS relation,waitstart FROM pg_locks WHERE database=(SELECT oid FROM pg_database WHERE datname=current_database()) AND (NOT granted OR locktype='advisory') ORDER BY pid LIMIT 80""").fetchall()
    except Exception as error:
        result['database_error'] = type(error).__name__
    try:
        server = redis.Redis.from_url('redis://redis:6379/0', decode_responses=True, socket_timeout=1, socket_connect_timeout=1)
        owner, publication = ownership_keys('app.tasks.progress_study_queue')
        fields = server.hmget(publication, 'token', 'phase', 'queue_refresh_wake', 'queue', 'tag', 'unacked')
        result['wake'] = dict(zip(('token','phase','queue_refresh_wake','queue','tag','unacked'), fields))
        result['wake']['owner'] = server.get(owner)
        result['wake']['owner_ttl_ms'] = server.pttl(owner)
        result['wake']['foreground_depth'] = server.llen('foreground')
        queue, tag, unacked = fields[3], fields[4], fields[5]
        if queue and unacked and tag:
            # Compare the exact tracked envelope in Redis, never output its contents.
            message = server.hget(publication, 'message')
            result['wake']['tracked_queued'] = bool(message and server.lpos(queue, message, maxlen=256) is not None)
            result['wake']['tracked_queue_search_limit'] = 256
            result['wake']['tracked_unacked'] = bool(server.hexists(unacked, tag))
    except Exception as error:
        result['redis_error'] = type(error).__name__
    result['workers'] = {'active_reserved': 'unavailable without broker control messages'}
    return result


# stdin EOF ends the observer immediately; the 60-second cap is a second fence.
start = time.monotonic()
while time.monotonic() - start < 60:
    emit(sample())
    if select.select([sys.stdin], [], [], 1)[0] and sys.stdin.read(1) == '':
        break
