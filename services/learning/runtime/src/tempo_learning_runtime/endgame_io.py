"""Bounded ownership of optional endgame synchronous phases.

Connections enter, execute and leave in one worker invocation. Cancellation
waits for an already-started phase so a receipt/transaction cannot outlive its
request owner or the application's database pools.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from .database import endgame_lane


async def _drain(future):
    while True:
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.cancelled():
                raise RuntimeError("Endgame worker was unexpectedly cancelled") from None


class EndgameIO:
    """One optional DB phase at a time, outside Study's worker allocation."""

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="learning-endgame")
        self._lock = asyncio.Lock()
        self._closed = False

    async def run(self, operation, *arguments):
        await asyncio.sleep(0)
        async with self._lock:
            if self._closed:
                raise RuntimeError("Endgame worker is closed")
            context = copy_context()

            def invoke():
                token = endgame_lane.set(True)
                try:
                    return operation(*arguments)
                finally:
                    endgame_lane.reset(token)

            future = asyncio.get_running_loop().run_in_executor(
                self._executor, context.run, invoke
            )
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                try:
                    await _drain(future)
                except Exception:
                    pass  # Cancellation remains the request's outcome.
                raise

    async def aclose(self):
        # Fence new phases before waiting for any active transaction to leave.
        self._closed = True

        async def close():
            async with self._lock:
                await asyncio.to_thread(self._executor.shutdown, wait=True)

        closing = asyncio.create_task(close())
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            await _drain(closing)
            raise
