"""Single-owner SQLite writer with foreground-priority scheduling."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
import logging
import sqlite3
import threading
import time
from typing import Callable, Generic, TypeVar

from .. import database as database_module


Result = TypeVar("Result")
WriteOperation = Callable[[sqlite3.Connection], Result]


@dataclass
class _PendingWrite(Generic[Result]):
    operation: WriteOperation[Result]
    future: Future[Result]
    label: str
    submitted_at: float
    background: bool


class DatabaseWriter:
    """Own the process's only application write connection.

    Operations are intentionally plain callbacks: callers must prepare network,
    chess, engine, and batch work before submission. The callback boundary is
    the transaction boundary.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._foreground: deque[_PendingWrite] = deque()
        self._background: deque[_PendingWrite] = deque()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._healthy = False
        self._database_path = None
        self._logger = logging.getLogger("tempo.writer")

    def start(self) -> None:
        with self._condition:
            current_path = database_module.DB_PATH
            if self._thread and self._thread.is_alive() and self._database_path == current_path:
                return
            if self._thread and self._thread.is_alive():
                raise RuntimeError("database writer is already running for another database")
            self._database_path = current_path
            self._stopping = False
            self._healthy = False
            self._thread = threading.Thread(
                target=self._run,
                name="tempo-database-writer",
                daemon=True,
            )
            self._thread.start()
            self._condition.wait_for(lambda: self._healthy or not self._thread.is_alive(), timeout=5)
            if not self._healthy:
                raise RuntimeError("database writer failed to start")

    def stop(self) -> None:
        with self._condition:
            if not self._thread:
                return
            self._stopping = True
            self._condition.notify_all()
            thread = self._thread
        thread.join(timeout=5)
        with self._condition:
            if thread.is_alive():
                raise RuntimeError("database writer failed to stop")
            self._thread = None
            self._healthy = False
            self._database_path = None

    @property
    def healthy(self) -> bool:
        with self._condition:
            return bool(self._healthy and self._thread and self._thread.is_alive())

    @property
    def queued_counts(self) -> dict[str, int]:
        with self._condition:
            return {
                "foreground": len(self._foreground),
                "background": len(self._background),
            }

    def submit_foreground_write(
        self, operation: WriteOperation[Result], *, label: str
    ) -> Result:
        return self._submit(operation, label=label, background=False)

    def submit_background_write(
        self, operation: WriteOperation[Result], *, label: str
    ) -> Result:
        return self._submit(operation, label=label, background=True)

    def _submit(
        self,
        operation: WriteOperation[Result],
        *,
        label: str,
        background: bool,
    ) -> Result:
        if not self.healthy:
            raise RuntimeError("database writer is unavailable")
        future: Future[Result] = Future()
        pending = _PendingWrite(operation, future, label, time.perf_counter(), background)
        with self._condition:
            (self._background if background else self._foreground).append(pending)
            self._condition.notify_all()
        return future.result()

    def _run(self) -> None:
        database_connection: sqlite3.Connection | None = None
        try:
            assert self._database_path is not None
            database_connection = sqlite3.connect(self._database_path)
            database_connection.row_factory = sqlite3.Row
            database_connection.execute("PRAGMA foreign_keys = ON")
            database_connection.execute("PRAGMA busy_timeout = 1000")
            with self._condition:
                self._healthy = True
                self._condition.notify_all()
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._foreground or self._background or self._stopping
                    )
                    if self._stopping and not self._foreground and not self._background:
                        return
                    pending = (
                        self._foreground.popleft()
                        if self._foreground
                        else self._background.popleft()
                    )
                wait_seconds = time.perf_counter() - pending.submitted_at
                transaction_started = time.perf_counter()
                try:
                    with database_module.write_compatibility_lock:
                        database_connection.execute("BEGIN IMMEDIATE")
                        result = pending.operation(database_connection)
                        database_connection.commit()
                    pending.future.set_result(result)
                except BaseException as error:
                    database_connection.rollback()
                    pending.future.set_exception(error)
                finally:
                    hold_seconds = time.perf_counter() - transaction_started
                    self._logger.info(
                        "database write label=%s wait=%.3fs hold=%.3fs",
                        pending.label,
                        wait_seconds,
                        hold_seconds,
                    )
                    if pending.background and hold_seconds > 0.05:
                        self._logger.warning(
                            "background commit exceeded 50ms budget label=%s hold=%.3fs",
                            pending.label,
                            hold_seconds,
                        )
        except BaseException:
            self._logger.exception("database writer terminated")
            with self._condition:
                self._healthy = False
                queued = [*self._foreground, *self._background]
                self._foreground.clear()
                self._background.clear()
                self._condition.notify_all()
            for pending in queued:
                if not pending.future.done():
                    pending.future.set_exception(RuntimeError("database writer terminated"))
        finally:
            if database_connection is not None:
                database_connection.close()
            with self._condition:
                self._healthy = False
                self._condition.notify_all()


database_writer = DatabaseWriter()


def submit_foreground_write(
    operation: WriteOperation[Result], *, label: str
) -> Result:
    return database_writer.submit_foreground_write(operation, label=label)


def submit_background_write(
    operation: WriteOperation[Result], *, label: str
) -> Result:
    return database_writer.submit_background_write(operation, label=label)
