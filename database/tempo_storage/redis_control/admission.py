"""Existing Redis lease scripts, keys, client construction and fixed lease operations."""
from functools import lru_cache
import os

import redis


_REGISTER_FOREGROUND = """
local now = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local existing = tonumber(redis.call('ZSCORE', KEYS[1], ARGV[2]) or '0')
local expires = math.max(existing, now + tonumber(ARGV[3]))
redis.call('ZADD', KEYS[1], expires, ARGV[2])
local latest = redis.call('ZREVRANGE', KEYS[1], 0, 0, 'WITHSCORES')
redis.call('PEXPIRE', KEYS[1], math.max(1, tonumber(latest[2]) - now + 1000))
return 1
"""

_CLAIM_BACKGROUND = """
local now = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('ZCARD', KEYS[1]) > 0 then return 0 end
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[3]), ARGV[2])
redis.call('EXPIRE', KEYS[2], math.ceil(tonumber(ARGV[3]) / 1000) + 1)
return 1
"""

_ADMISSION_NAMESPACE = os.getenv('TEMPO_ADMISSION_NAMESPACE', 'tempo:admission')

_FOREGROUND_KEY = _ADMISSION_NAMESPACE + ':foreground'

_BACKGROUND_KEY = _ADMISSION_NAMESPACE + ':background'

_FOREGROUND_LEASE_MS = 30_000

_BACKGROUND_LEASE_MS = 5_000


@lru_cache(maxsize=1)
def client() -> redis.Redis:
    broker_url = os.environ["TEMPO_REDIS_URL"]
    return redis.Redis.from_url(broker_url, socket_connect_timeout=1, socket_timeout=1)


def configured() -> bool:
    return bool(os.environ.get("TEMPO_REDIS_URL"))


def foreground_present(server, foreground_key: str, current_milliseconds: int) -> bool:
    pipeline = server.pipeline(transaction=True)
    pipeline.zremrangebyscore(foreground_key, "-inf", current_milliseconds)
    pipeline.zcard(foreground_key)
    return bool(pipeline.execute()[1])


def register_foreground(server, foreground_key: str, current_milliseconds: int,
                        token: str, lease_milliseconds: int):
    return server.eval(
        _REGISTER_FOREGROUND, 1, foreground_key,
        current_milliseconds, token, lease_milliseconds,
    )


def release_foreground(server, foreground_key: str, token: str):
    return server.zrem(foreground_key, token)


def claim_background(server, foreground_key: str, background_key: str,
                     current_milliseconds: int, token: str, lease_milliseconds: int):
    return server.eval(
        _CLAIM_BACKGROUND, 2, foreground_key, background_key,
        current_milliseconds, token, lease_milliseconds,
    )


def release_background(server, background_key: str, token: str):
    return server.zrem(background_key, token)
