"""Cooperative priority gate between foreground requests and background work."""

from __future__ import annotations

from contextlib import contextmanager
from contextlib import nullcontext, ExitStack
from contextvars import ContextVar
import threading
import time
import json
import os
from urllib.request import Request, urlopen
from typing import Iterator

from . import redis_admission_gate
from .redis_admission_gate import BackgroundAdmissionDeferred


class BackgroundContractError(RuntimeError):
    """Raised when background code opens an unclassified database connection."""


_work_class: ContextVar[str] = ContextVar("tempo_work_class", default="foreground")
_background_job: ContextVar[tuple[str, str] | None] = ContextVar(
    "tempo_background_job", default=None
)
_yield_admission: ContextVar[bool] = ContextVar('tempo_yield_admission', default=False)
_control_section: ContextVar[bool] = ContextVar('tempo_background_control', default=False)


class ApplicationActivityGate:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._foreground_requests = 0
        self._active_background_sections = 0
        self._background_work: tuple[str, str] | None = None
        self._browser_active_until = 0.0

    def record_browser_activity(self, seconds: float = 3.0) -> None:
        if redis_admission_gate.configured():
            redis_admission_gate.record_browser_activity(seconds)
        with self._condition:
            self._browser_active_until = max(self._browser_active_until, time.monotonic() + seconds)
            self._condition.notify_all()

    @contextmanager
    def foreground(self):
        token = _work_class.set("foreground")
        shared_lease = (
            redis_admission_gate.foreground_lease()
            if redis_admission_gate.configured() else nullcontext()
        )
        try:
            with shared_lease:
                with self._condition:
                    self._foreground_requests += 1
                    self._condition.notify_all()
                try:
                    yield
                finally:
                    with self._condition:
                        self._foreground_requests -= 1
                        self._condition.notify_all()
        finally:
            _work_class.reset(token)

    @contextmanager
    def background_request(self):
        """Mark an HTTP request made by a browser background worker."""

        token = _work_class.set("background")
        admission_token = _yield_admission.set(True)
        try:
            yield
        finally:
            _work_class.reset(token)
            _yield_admission.reset(admission_token)

    def wait_for_foreground(self) -> None:
        if _control_section.get():
            return
        if _yield_admission.get() or redis_admission_gate.configured():
            self.check_background_admission()
            return
        activity_url = os.getenv("TEMPO_FOREGROUND_ACTIVITY_URL")
        if activity_url:
            while True:
                try:
                    request = Request(activity_url, headers={"X-Tempo-Work-Class": "background"})
                    with urlopen(request, timeout=2) as response:
                        active = bool(json.load(response)["active"])
                except (OSError, ValueError, KeyError):
                    active = True
                if not active:
                    break
                time.sleep(0.25)
        with self._condition:
            self._condition.wait_for(lambda: self._foreground_requests == 0)

    def check_background_admission(self) -> None:
        """Check once before claiming or computing; unavailable evidence denies."""
        if _control_section.get():
            return
        if self.foreground_waiting:
            raise BackgroundAdmissionDeferred('Waiting for foreground activity')
        if redis_admission_gate.configured():
            import redis
            try:
                if redis_admission_gate.foreground_present():
                    raise BackgroundAdmissionDeferred('Waiting for foreground activity')
            except redis.RedisError as error:
                raise BackgroundAdmissionDeferred('Foreground admission is unavailable') from error
        elif activity_url := os.getenv('TEMPO_FOREGROUND_ACTIVITY_URL'):
            try:
                request = Request(activity_url, headers={'X-Tempo-Work-Class': 'background'})
                with urlopen(request, timeout=0.25) as response:
                    active = bool(json.load(response)['active'])
            except (OSError, ValueError, KeyError) as error:
                raise BackgroundAdmissionDeferred('Foreground admission is unavailable') from error
            if active:
                raise BackgroundAdmissionDeferred('Waiting for foreground activity')

    @contextmanager
    def background_control(self):
        """Short receipt/release/deferral bookkeeping; never analysis or I/O."""
        token = _control_section.set(True)
        try:
            yield
        finally:
            _control_section.reset(token)

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

        shared_lease = (
            redis_admission_gate.background_lease()
            if redis_admission_gate.configured() and not _control_section.get() else nullcontext()
        )
        from .background_runtime import admission_wait, reserved_database_telemetry, heartbeat
        # Acquire before suppressing so admission-wait heartbeats remain visible.
        # Exit leases before telemetry suppression, including error cleanup.
        with ExitStack() as telemetry, ExitStack() as leases:
            with admission_wait():
                leases.enter_context(shared_lease)
                telemetry.enter_context(reserved_database_telemetry())
                self.wait_for_foreground()
                with self._condition:
                    if _yield_admission.get() or redis_admission_gate.configured() or _control_section.get():
                        if (self._active_background_sections and not _control_section.get()) or (
                            self._foreground_requests and not _control_section.get()
                        ):
                            raise BackgroundAdmissionDeferred('Waiting for a database section')
                    else:
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
        heartbeat()

    @contextmanager
    def background_job(self, job_type: str, job_id: str, *, yielding: bool = False):
        class_token = _work_class.set("background")
        job_token = _background_job.set((job_type, job_id))
        admission_token = _yield_admission.set(yielding)
        with self._condition:
            self._background_work = (job_type, job_id)
        try:
            yield
        finally:
            with self._condition:
                self._background_work = None
            _background_job.reset(job_token)
            _work_class.reset(class_token)
            _yield_admission.reset(admission_token)

    @property
    def in_background(self) -> bool:
        return _work_class.get() == "background"

    @property
    def current_job(self) -> tuple[str, str] | None:
        return _background_job.get()

    @property
    def in_background_control(self) -> bool:
        return _control_section.get()

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
    def foreground_requests_active(self) -> bool:
        with self._condition:
            return self._foreground_requests > 0

    @property
    def active_background_sections(self) -> int:
        with self._condition:
            return self._active_background_sections


activity_gate = ApplicationActivityGate()
