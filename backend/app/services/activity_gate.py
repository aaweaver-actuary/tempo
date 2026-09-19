"""Cooperative priority gate between foreground requests and background work."""

from __future__ import annotations

from contextlib import contextmanager
import threading


class ApplicationActivityGate:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._foreground_requests = 0
        self._background_work: tuple[str, str] | None = None

    @contextmanager
    def foreground(self):
        with self._condition:
            self._foreground_requests += 1
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._foreground_requests -= 1
                self._condition.notify_all()

    def wait_for_foreground(self) -> None:
        with self._condition:
            self._condition.wait_for(lambda: self._foreground_requests == 0)

    @contextmanager
    def background_job(self, job_type: str, job_id: str):
        with self._condition:
            self._background_work = (job_type, job_id)
        try:
            yield
        finally:
            with self._condition:
                self._background_work = None

    @property
    def background_work(self) -> tuple[str, str] | None:
        with self._condition:
            return self._background_work

    @property
    def foreground_waiting(self) -> bool:
        with self._condition:
            return self._foreground_requests > 0


activity_gate = ApplicationActivityGate()
