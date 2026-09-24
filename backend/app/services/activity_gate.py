"""Cooperative priority gate between foreground requests and background work."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
from typing import Iterator


class BackgroundContractError(RuntimeError):
    """Raised when background code opens an unclassified database connection."""


_work_class: ContextVar[str] = ContextVar("tempo_work_class", default="foreground")
_background_job: ContextVar[tuple[str, str] | None] = ContextVar(
    "tempo_background_job", default=None
)


class ApplicationActivityGate:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._foreground_requests = 0
        self._active_background_sections = 0
        self._background_work: tuple[str, str] | None = None
        self._browser_active_until = 0.0

    def record_browser_activity(self, seconds: float = 3.0) -> None:
        with self._condition:
            self._browser_active_until = max(self._browser_active_until, time.monotonic() + seconds)
            self._condition.notify_all()

    @contextmanager
    def foreground(self):
        token = _work_class.set("foreground")
        with self._condition:
            self._foreground_requests += 1
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._foreground_requests -= 1
                self._condition.notify_all()
            _work_class.reset(token)

    @contextmanager
    def background_request(self):
        """Mark an HTTP request made by a browser background worker."""

        token = _work_class.set("background")
        try:
            yield
        finally:
            _work_class.reset(token)

    def wait_for_foreground(self) -> None:
        with self._condition:
            self._condition.wait_for(lambda: self._foreground_requests == 0)

    def wait_for_background_sections(self) -> None:
        """Wait until an active background SQLite section has committed."""

        with self._condition:
            self._condition.wait_for(lambda: self._active_background_sections == 0)

    @contextmanager
    def background_database_section(self) -> Iterator[None]:
        """Reserve one short database section for background work.

        Computation and network activity must happen outside this context. A
        foreground request can arrive while a section is active, but the
        section is bounded and the next background section will yield to it.
        """

        with self._condition:
            self._condition.wait_for(
                lambda: self._foreground_requests == 0
                and self._active_background_sections == 0
            )
            self._active_background_sections += 1
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._active_background_sections -= 1
                self._condition.notify_all()

    @contextmanager
    def background_job(self, job_type: str, job_id: str):
        class_token = _work_class.set("background")
        job_token = _background_job.set((job_type, job_id))
        with self._condition:
            self._background_work = (job_type, job_id)
        try:
            yield
        finally:
            with self._condition:
                self._background_work = None
            _background_job.reset(job_token)
            _work_class.reset(class_token)

    @property
    def in_background(self) -> bool:
        return _work_class.get() == "background"

    @property
    def current_job(self) -> tuple[str, str] | None:
        return _background_job.get()

    def assert_foreground_connection_allowed(self, background: bool) -> None:
        if self.in_background and not background:
            job = self.current_job
            label = f" for {job[0]}:{job[1]}" if job else ""
            raise BackgroundContractError(
                f"background work cannot open a foreground database connection{label}"
            )

    @property
    def background_work(self) -> tuple[str, str] | None:
        with self._condition:
            return self._background_work

    @property
    def foreground_waiting(self) -> bool:
        with self._condition:
            return self._foreground_requests > 0 or time.monotonic() < self._browser_active_until

    @property
    def active_background_sections(self) -> int:
        with self._condition:
            return self._active_background_sections


activity_gate = ApplicationActivityGate()
