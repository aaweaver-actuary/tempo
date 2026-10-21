"""Existing Redis Study-policy fencing, client construction and settings projection."""
from functools import lru_cache
import os

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from . import admission as redis_admission_gate

POLICY_KEY = redis_admission_gate._ADMISSION_NAMESPACE + ':study-policy'

GENERATION_KEY = POLICY_KEY + ':generation'

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


def advance_generation(server, generation_key: str):
    return server.incr(generation_key)


def existing_or_initial_generation(server, generation_key: str):
    return server.eval("return redis.call('GET',KEYS[1]) or redis.call('INCR',KEYS[1])", 1, generation_key)


def study_mode_setting(database):
    return database.execute('SELECT study_only_mode FROM settings WHERE id=1').fetchone()


def publish_policy(server, policy_key: str, generation_key: str, generation: int,
                   encoded_policy: str, policy_seconds: int):
    return server.eval(_PUBLISH, 2, policy_key, generation_key,
                      generation, encoded_policy, policy_seconds)


def policy_snapshot(server, foreground_key: str, policy_key: str, generation_key: str,
                    current_milliseconds: int):
    return server.eval(
        _READ, 3, foreground_key, policy_key, generation_key,
        current_milliseconds)
