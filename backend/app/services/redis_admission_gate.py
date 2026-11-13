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

from tempo_storage.redis_control import admission as storage
from tempo_storage.redis_control.admission import client, configured


_LOGGER = logging.getLogger("tempo.admission")


class BackgroundAdmissionDeferred(RuntimeError):
    """Admission denied or unavailable; durable intent must be retained."""

    def __init__(self, *args, reason: str | None = None):
        super().__init__(*args)
        self.reason = reason


def foreground_present() -> bool:
    current_milliseconds = int(time.time() * 1000)
    server = client()
    return storage.has_foreground(server, current_milliseconds)


def record_browser_activity(seconds: float, *, session_id: str = "legacy") -> None:
    """Keep the active workspace ahead of new background database slices."""

    storage.record_browser_presence(client(), int(time.time() * 1000), session_id, seconds)


@asynccontextmanager
async def async_foreground_lease():
    """HTTP admission never blocks the event loop on synchronous Redis I/O."""
    token = uuid.uuid4().hex

    async def register():
        await asyncio.to_thread(
            storage.acquire_foreground,
            client(),
            int(time.time() * 1000),
            token,
        )

    await register()

    async def renew():
        while True:
            await asyncio.sleep(storage.foreground_renewal_seconds())
            try:
                await register()
            except redis.RedisError:
                _LOGGER.exception("Could not renew foreground admission lease")

    renewal = asyncio.create_task(renew())
    try:
        yield
    finally:
        renewal.cancel()
        with suppress(asyncio.CancelledError):
            await renewal
        try:
            await asyncio.to_thread(
                storage.remove_foreground, client(), token
            )
        except redis.RedisError:
            _LOGGER.exception("Could not release foreground admission lease")


@contextmanager
def foreground_lease() -> Iterator[None]:
    token = uuid.uuid4().hex
    stop_renewal = threading.Event()

    def renew() -> None:
        while not stop_renewal.wait(storage.foreground_renewal_seconds()):
            try:
                storage.acquire_foreground(
                    client(),
                    int(time.time() * 1000),
                    token,
                )
            except redis.RedisError:
                _LOGGER.exception("Could not renew foreground admission lease")

    from .background_runtime import training_timing_stage

    with training_timing_stage("foreground_admission"):
        storage.acquire_foreground(
            client(),
            int(time.time() * 1000),
            token,
        )
    renewal = threading.Thread(target=renew, name="tempo-foreground-lease", daemon=True)
    renewal.start()
    try:
        yield
    finally:
        stop_renewal.set()
        renewal.join(timeout=1)
        try:
            storage.remove_foreground(client(), token)
        except redis.RedisError:
            _LOGGER.exception("Could not release foreground admission lease")


@contextmanager
def background_lease() -> Iterator[None]:
    token = uuid.uuid4().hex
    try:
        admitted = storage.acquire_background(
            client(),
            int(time.time() * 1000),
            token,
        )
    except redis.RedisError as error:
        raise BackgroundAdmissionDeferred(
            "Foreground admission is unavailable"
        ) from error
    if not admitted:
        raise BackgroundAdmissionDeferred("Waiting for foreground activity", reason="foreground_wait")
    try:
        yield
    finally:
        try:
            storage.remove_background(client(), token)
        except redis.RedisError:
            _LOGGER.exception("Could not release background admission lease")
