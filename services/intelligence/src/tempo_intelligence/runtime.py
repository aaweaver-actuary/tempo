"""Owned, serial offloading for this service's synchronous database operations."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
import time

BACKGROUND_OPERATION_SECONDS = 5.0
background_deadline = ContextVar("intelligence_background_deadline", default=None)


async def drain_operation(future):
    """Observe completion even if the owner receives repeated Task.cancel()."""
    while True:
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.cancelled():
                raise RuntimeError("owned_database_operation_cancelled") from None


class DatabaseRunner:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="intelligence-db")
        self.lock = asyncio.Lock()
        self.pending = None
        self.closed = False

    async def run(self, operation, *arguments, on_result=None, **keywords):
        # Deliver already-pending cancellation before starting a new operation.
        await asyncio.sleep(0)
        async with self.lock:
            if self.closed:
                raise RuntimeError("database_runner_closed")
            deadline = time.monotonic() + BACKGROUND_OPERATION_SECONDS

            def invoke():
                token = background_deadline.set(deadline)
                try:
                    return operation(*arguments, **keywords)
                finally:
                    background_deadline.reset(token)

            future = asyncio.get_running_loop().run_in_executor(self.executor, invoke)
            self.pending = future
            try:
                try:
                    result = await asyncio.shield(future)
                except asyncio.CancelledError:
                    try:
                        result = await drain_operation(future)
                    except Exception:
                        pass  # Pending cancellation remains authoritative.
                    else:
                        if on_result is not None:
                            on_result(result)
                    raise
                if on_result is not None:
                    on_result(result)
                return result
            finally:
                self.pending = None  # The future was consumed, also on cancel.

    async def aclose(self):
        async with self.lock:
            if self.closed:
                return
            self.closed = True
            # No operation is running or queued. Join away from the event loop.
            joining = asyncio.create_task(asyncio.to_thread(self.executor.shutdown, wait=True))
            try:
                await asyncio.shield(joining)
            except asyncio.CancelledError:
                await drain_operation(joining)
                raise
