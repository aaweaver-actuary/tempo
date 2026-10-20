"""One bounded optional worker owns its phase and pools through actual completion.

A caller's timeout/cancellation ends its wait, not a database transaction. The
worker is a daemon and owns only optional resources; service shutdown never
joins it or closes its active pools. Canonical receipts settle eventual writes.
"""

import asyncio
from concurrent.futures import Future
from contextvars import copy_context
import logging
from queue import Queue
from threading import Lock, Thread

from .database import endgame_lane


class EndgameUnavailable(RuntimeError):
    """The caller has no confirmed result; an admitted phase may still finish."""


def _observe(future):
    # A cancelled/timed-out request must not leave an unobserved late exception.
    if not future.cancelled():
        future.exception()


class EndgameIO:
    """At most one admitted phase, with no unbounded executor/request queue."""

    def __init__(self, close_pools=lambda: None, *, wait_seconds=5):
        self._close_pools = close_pools
        self._wait_seconds = wait_seconds
        self._jobs = Queue(maxsize=1)
        self._guard = Lock()  # State handoff only; never held across optional I/O.
        self._active = None
        self._closed = False
        self._thread = None

    async def run(self, operation, *arguments):
        await asyncio.sleep(0)  # Deliver pending cancellation before admission.
        with self._guard:
            if self._closed:
                raise EndgameUnavailable("Optional endgame worker is closed")
            if self._active is not None:
                raise EndgameUnavailable("Optional endgame worker is busy")
            phase = Future()
            self._active = phase
            if self._thread is None:
                self._thread = Thread(target=self._serve, name="learning-endgame", daemon=True)
                try:
                    self._thread.start()
                except RuntimeError:
                    self._thread = None
                    self._active = None
                    raise EndgameUnavailable("Optional endgame worker could not start") from None
            self._jobs.put_nowait((copy_context(), operation, arguments, phase))

        result = asyncio.wrap_future(phase)
        result.add_done_callback(_observe)
        # asyncio.wait does not cancel the worker's delivery future when the
        # request is cancelled or the bounded result wait expires.
        done, _ = await asyncio.wait((result,), timeout=self._wait_seconds)
        if result in done:
            return result.result()
        raise EndgameUnavailable("Optional endgame result is not confirmed yet")

    def _serve(self):
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                context, operation, arguments, phase = job
                with self._guard:
                    stopping = self._closed
                error = None
                outcome = None
                if stopping:
                    error = EndgameUnavailable("Optional endgame phase did not start")
                else:
                    def invoke():
                        token = endgame_lane.set(True)
                        try:
                            return operation(*arguments)
                        finally:
                            endgame_lane.reset(token)
                    try:
                        outcome = context.run(invoke)
                    except BaseException as caught:
                        error = caught
                with self._guard:
                    self._active = None
                    # Shutdown can end the caller's wait while this phase keeps
                    # transaction ownership. Its late result is then unconfirmed.
                    if not phase.done():
                        if error is None:
                            phase.set_result(outcome)
                        else:
                            phase.set_exception(error)
                    stopping = self._closed
                if stopping:
                    break
        finally:
            # Only this daemon owns optional pools; no active phase is closed
            # underneath its transaction, even after the application's exit.
            try:
                self._close_pools()
            except Exception:
                logging.getLogger(__name__).exception("Optional endgame pool cleanup failed")

    async def aclose(self):
        with self._guard:
            if self._closed:
                return
            self._closed = True
            if self._active is not None and not self._active.done():
                self._active.set_exception(EndgameUnavailable("Optional endgame worker is stopping"))
            if self._thread is not None and self._active is None:
                self._jobs.put_nowait(None)
