"""Cross-process foreground admission for short background database slices."""

from __future__ import annotations

from contextlib import contextmanager, asynccontextmanager, suppress
import asyncio
import logging
import threading
import time
import uuid
from typing import Iterator

import redis

from tempo_legacy_database.adapters.redis_control import admission as storage
from tempo_legacy_database.adapters.redis_control.admission import (
    _REGISTER_FOREGROUND, _CLAIM_BACKGROUND, _ADMISSION_NAMESPACE,
    _FOREGROUND_KEY, _BACKGROUND_KEY, _FOREGROUND_LEASE_MS, _BACKGROUND_LEASE_MS,
    client, configured,
)


_LOGGER = logging.getLogger("tempo.admission")


class BackgroundAdmissionDeferred(RuntimeError):
    """Admission denied or unavailable; durable intent must be retained."""


def foreground_present() -> bool:
    current_milliseconds = int(time.time() * 1000)
    server = client()
    return storage.foreground_present(server, _FOREGROUND_KEY, current_milliseconds)


def record_browser_activity(seconds: float, *, session_id: str = 'legacy') -> None:
    """Keep the active workspace ahead of new background database slices."""

    lease_milliseconds = max(1, int(seconds * 1000))
    storage.register_foreground(
        client(), _FOREGROUND_KEY, int(time.time() * 1000),
        f"browser-activity:{session_id}", lease_milliseconds,
    )


@asynccontextmanager
async def async_foreground_lease():
    """HTTP admission never blocks the event loop on synchronous Redis I/O."""
    token = uuid.uuid4().hex
    async def register():
        await asyncio.to_thread(
            storage.register_foreground, client(), _FOREGROUND_KEY,
            int(time.time() * 1000), token, _FOREGROUND_LEASE_MS,
        )
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
            await asyncio.to_thread(storage.release_foreground, client(), _FOREGROUND_KEY, token)
        except redis.RedisError:
            _LOGGER.exception('Could not release foreground admission lease')


@contextmanager
def foreground_lease() -> Iterator[None]:
    token = uuid.uuid4().hex
    stop_renewal = threading.Event()

    def renew() -> None:
        while not stop_renewal.wait(_FOREGROUND_LEASE_MS / 3000):
            try:
                storage.register_foreground(
                    client(), _FOREGROUND_KEY, int(time.time() * 1000), token, _FOREGROUND_LEASE_MS,
                )
            except redis.RedisError:
                _LOGGER.exception("Could not renew foreground admission lease")

    from .background_runtime import training_timing_stage
    with training_timing_stage("foreground_admission"):
        storage.register_foreground(
            client(), _FOREGROUND_KEY, int(time.time() * 1000), token, _FOREGROUND_LEASE_MS,
        )
    renewal = threading.Thread(target=renew, name="tempo-foreground-lease", daemon=True)
    renewal.start()
    try:
        yield
    finally:
        stop_renewal.set()
        renewal.join(timeout=1)
        try:
            storage.release_foreground(client(), _FOREGROUND_KEY, token)
        except redis.RedisError:
            _LOGGER.exception("Could not release foreground admission lease")


@contextmanager
def background_lease() -> Iterator[None]:
    token = uuid.uuid4().hex
    try:
        admitted = storage.claim_background(
            client(), _FOREGROUND_KEY, _BACKGROUND_KEY,
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
            storage.release_background(client(), _BACKGROUND_KEY, token)
        except redis.RedisError:
            _LOGGER.exception("Could not release background admission lease")
