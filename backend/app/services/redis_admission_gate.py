"""Cross-process foreground admission for short background database slices."""

from __future__ import annotations

from contextlib import contextmanager, asynccontextmanager, suppress
import asyncio
from functools import lru_cache
import logging
import os
import threading
import time
import uuid
from typing import Iterator

import redis


_LOGGER = logging.getLogger("tempo.admission")


class BackgroundAdmissionDeferred(RuntimeError):
    """Admission denied or unavailable; durable intent must be retained."""


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


def foreground_present() -> bool:
    current_milliseconds = int(time.time() * 1000)
    server = client()
    pipeline = server.pipeline(transaction=True)
    pipeline.zremrangebyscore(_FOREGROUND_KEY, "-inf", current_milliseconds)
    pipeline.zcard(_FOREGROUND_KEY)
    return bool(pipeline.execute()[1])


def record_browser_activity(seconds: float, *, session_id: str = 'legacy') -> None:
    """Keep the active workspace ahead of new background database slices."""

    lease_milliseconds = max(1, int(seconds * 1000))
    client().eval(
        _REGISTER_FOREGROUND, 1, _FOREGROUND_KEY,
        int(time.time() * 1000), f"browser-activity:{session_id}", lease_milliseconds,
    )


@asynccontextmanager
async def async_foreground_lease():
    """HTTP admission never blocks the event loop on synchronous Redis I/O."""
    token = uuid.uuid4().hex
    async def register():
        await asyncio.to_thread(client().eval, _REGISTER_FOREGROUND, 1, _FOREGROUND_KEY,
                                int(time.time() * 1000), token, _FOREGROUND_LEASE_MS)
    await register()
    async def renew():
        while True:
            await asyncio.sleep(_FOREGROUND_LEASE_MS / 3000)
            try:
                await register()
            except redis.RedisError:
                _LOGGER.exception('Could not renew foreground admission lease')
    renewal = asyncio.create_task(renew())
    try:
        yield
    finally:
        renewal.cancel()
        with suppress(asyncio.CancelledError):
            await renewal
        try:
            await asyncio.to_thread(client().zrem, _FOREGROUND_KEY, token)
        except redis.RedisError:
            _LOGGER.exception('Could not release foreground admission lease')


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

    from .background_runtime import training_timing_stage
    with training_timing_stage("foreground_admission"):
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
    try:
        admitted = client().eval(
            _CLAIM_BACKGROUND, 2, _FOREGROUND_KEY, _BACKGROUND_KEY,
            int(time.time() * 1000), token, _BACKGROUND_LEASE_MS,
        )
    except redis.RedisError as error:
        raise BackgroundAdmissionDeferred('Foreground admission is unavailable') from error
    if not admitted:
        raise BackgroundAdmissionDeferred('Waiting for foreground activity')
    try:
        yield
    finally:
        try:
            client().zrem(_BACKGROUND_KEY, token)
        except redis.RedisError:
            _LOGGER.exception("Could not release background admission lease")
