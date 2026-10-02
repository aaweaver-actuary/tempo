"""Cross-process foreground admission for short background database slices."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import logging
import os
import threading
import time
import uuid
from typing import Iterator

import redis


_LOGGER = logging.getLogger("tempo.admission")


_REGISTER_FOREGROUND = """
local now = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), ARGV[2])
redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[3]) / 1000) + 1)
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

_FOREGROUND_KEY = "tempo:admission:foreground"
_BACKGROUND_KEY = "tempo:admission:background"
_FOREGROUND_LEASE_MS = 30_000
_BACKGROUND_LEASE_MS = 5_000


@lru_cache(maxsize=1)
def client() -> redis.Redis:
    broker_url = os.environ["TEMPO_REDIS_URL"]
    return redis.Redis.from_url(broker_url, socket_connect_timeout=1, socket_timeout=1)


def configured() -> bool:
    return bool(os.environ.get("TEMPO_REDIS_URL"))


def foreground_present() -> bool:
    current_milliseconds = int(time.time() * 1000)
    server = client()
    pipeline = server.pipeline(transaction=True)
    pipeline.zremrangebyscore(_FOREGROUND_KEY, "-inf", current_milliseconds)
    pipeline.zcard(_FOREGROUND_KEY)
    return bool(pipeline.execute()[1])


def record_browser_activity(seconds: float) -> None:
    """Keep the active workspace ahead of new background database slices."""

    lease_milliseconds = max(1, int(seconds * 1000))
    client().eval(
        _REGISTER_FOREGROUND, 1, _FOREGROUND_KEY,
        int(time.time() * 1000), "browser-activity", lease_milliseconds,
    )


@contextmanager
def foreground_lease() -> Iterator[None]:
    token = uuid.uuid4().hex
    stop_renewal = threading.Event()

    def renew() -> None:
        while not stop_renewal.wait(_FOREGROUND_LEASE_MS / 3000):
            try:
                client().eval(
                    _REGISTER_FOREGROUND, 1, _FOREGROUND_KEY,
                    int(time.time() * 1000), token, _FOREGROUND_LEASE_MS,
                )
            except redis.RedisError:
                _LOGGER.exception("Could not renew foreground admission lease")

    client().eval(
        _REGISTER_FOREGROUND, 1, _FOREGROUND_KEY,
        int(time.time() * 1000), token, _FOREGROUND_LEASE_MS,
    )
    renewal = threading.Thread(target=renew, name="tempo-foreground-lease", daemon=True)
    renewal.start()
    try:
        yield
    finally:
        stop_renewal.set()
        renewal.join(timeout=1)
        try:
            client().zrem(_FOREGROUND_KEY, token)
        except redis.RedisError:
            _LOGGER.exception("Could not release foreground admission lease")


@contextmanager
def background_lease() -> Iterator[None]:
    token = uuid.uuid4().hex
    while not client().eval(
        _CLAIM_BACKGROUND, 2, _FOREGROUND_KEY, _BACKGROUND_KEY,
        int(time.time() * 1000), token, _BACKGROUND_LEASE_MS,
    ):
        from .background_runtime import heartbeat
        heartbeat()
        time.sleep(0.01)
    try:
        yield
    finally:
        try:
            client().zrem(_BACKGROUND_KEY, token)
        except redis.RedisError:
            _LOGGER.exception("Could not release background admission lease")
