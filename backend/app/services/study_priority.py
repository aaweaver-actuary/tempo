"""Expiring control snapshots; optional workers never poll the study database.

The database remains authoritative. A refresh reserves its generation *before*
reading, so a slow old read cannot overwrite a post-commit settings refresh.
Missing, refreshing, expired or unavailable control evidence denies optional work.
This adapter belongs to the legacy assembly, not the broker-independent Learning API.
"""

from contextlib import contextmanager
from functools import lru_cache
import json
import logging
import os
import threading
import time

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from . import redis_admission_gate

POLICY_SECONDS = 15
REFRESH_SECONDS = 5
POLICY_KEY = redis_admission_gate._ADMISSION_NAMESPACE + ':study-policy'
GENERATION_KEY = POLICY_KEY + ':generation'
_logger = logging.getLogger('tempo.study_priority')
_local_lock = threading.Lock()
_local_generation = 0
_local_policy = None

_PUBLISH = """
if tonumber(redis.call('GET', KEYS[2])) ~= tonumber(ARGV[1]) then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""
_READ = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
return {redis.call('ZCARD', KEYS[1]), redis.call('GET', KEYS[2]) or '',
        redis.call('GET', KEYS[3]) or ''}
"""


@lru_cache(maxsize=1)
def control_client():
    return redis.Redis.from_url(os.environ['TEMPO_REDIS_URL'],
        socket_connect_timeout=0.05, socket_timeout=0.05, retry=Retry(NoBackoff(), 0))


def refresh_policy(*, invalidate: bool = True) -> bool:
    """One bounded, independent read. Failure never authorizes optional work."""
    global _local_generation, _local_policy
    # A private producer may finish an in-flight read after its owner restores
    # module configuration. Publish only to the identity that reserved this
    # generation; deleting that private generation must fence the late result.
    policy_key, generation_key = POLICY_KEY, GENERATION_KEY
    from tempo_legacy_database.connections import background_read_connection
    from .activity_gate import activity_gate
    try:
        shared = redis_admission_gate.configured()
        if shared:
            generation = int(control_client().incr(generation_key) if invalidate else
                control_client().eval("return redis.call('GET',KEYS[1]) or redis.call('INCR',KEYS[1])", 1, generation_key))
        else:
            with _local_lock:
                if invalidate or not _local_generation: _local_generation += 1
                generation = _local_generation
        with activity_gate.background_job('study_policy', 'refresh', yielding=True), activity_gate.background_control():
            with background_read_connection(pool_timeout_seconds=0.1) as database:
                row = database.execute('SELECT study_only_mode FROM settings WHERE id=1').fetchone()
        if row is None:
            return False
        policy = {'generation': generation, 'study_only_mode': bool(row[0]),
                  'expires_at_ms': int((time.time() + POLICY_SECONDS) * 1000)}
        if shared:
            return bool(control_client().eval(_PUBLISH, 2, policy_key, generation_key,
                                              generation, json.dumps(policy), POLICY_SECONDS))
        with _local_lock:
            if generation == _local_generation:
                _local_policy = policy
                return True
        return False
    except Exception as error:
        # A control outage is expected degradation, not a successful settings read.
        _logger.warning('Study priority policy refresh deferred: %s', type(error).__name__)
        return False


def control_snapshot() -> dict:
    unavailable = {'active': True, 'available': False, 'study_only_mode': None,
                   'policy_generation': None}
    try:
        if redis_admission_gate.configured():
            active, encoded, current_generation = control_client().eval(
                _READ, 3, redis_admission_gate._FOREGROUND_KEY, POLICY_KEY, GENERATION_KEY,
                int(time.time() * 1000))
            policy = json.loads(encoded) if encoded else None
            generation = int(current_generation) if current_generation else None
        else:
            from .activity_gate import activity_gate
            active = activity_gate.foreground_waiting
            with _local_lock:
                policy, generation = _local_policy, _local_generation
        if (not policy or policy['generation'] != generation or
                policy['expires_at_ms'] <= time.time() * 1000 or
                type(policy['study_only_mode']) is not bool):
            return unavailable
        return {'active': bool(active), 'available': True,
                'study_only_mode': policy['study_only_mode'], 'policy_generation': generation}
    except (redis.RedisError, ValueError, TypeError, KeyError):
        return unavailable


@contextmanager
def policy_publisher():
    """One API-owned refresher; no database work on a control HTTP request."""
    stopped = threading.Event()
    def publish():
        while not stopped.wait(REFRESH_SECONDS):
            refresh_policy(invalidate=False)
    refresh_policy()
    thread = threading.Thread(target=publish, name='tempo-study-policy', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=0.5)
