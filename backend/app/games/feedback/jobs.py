"""Run one optional unit; finite provider timeouts belong to injected adapters."""
from __future__ import annotations

import threading

from .ports import AcquisitionFailure, ConcurrentUpdate


class FeedbackRunner:
    def __init__(self, coordinator, *, delivery_enabled: bool = True,
                 owned_resources=(), lease_seconds: float = 120):
        if not 1 <= lease_seconds <= 300:
            raise ValueError("finite work lease required")
        self.coordinator = coordinator
        self.delivery_enabled = delivery_enabled
        self._owned = tuple(owned_resources)
        self._stop = threading.Event()
        self._unit = threading.Lock()
        self._closed = False
        self._prefer_delivery = False
        self.lease_seconds = lease_seconds

    def request_stop(self) -> None:
        self._stop.set()

    def run_delivery_once(self) -> str:
        """Only already determined local work; never claim or fetch a page."""
        if self._stop.is_set():
            self.close()
            return "stopped"
        if not self.delivery_enabled:
            return "idle"
        if not self._unit.acquire(blocking=False):
            return "busy"
        try:
            pending = self.coordinator.store.pending_delivery(self.coordinator.clock.now(), ready_only=True)
            if pending is None:
                return "idle"
            self.coordinator.deliver(pending)
            return "delivery"
        finally:
            self._unit.release()
            if self._stop.is_set():
                self.close()

    def run_once(self) -> str:
        if self._stop.is_set():
            self.close()
            return "stopped"
        if not self._unit.acquire(blocking=False):
            return "busy"
        try:
            coordinator = self.coordinator
            now = coordinator.clock.now()
            if self.delivery_enabled and self._prefer_delivery:
                pending = coordinator.store.pending_delivery(now)
                if pending:
                    coordinator.deliver(pending)
                    self._prefer_delivery = False
                    return "delivery"
            job = coordinator.store.claim_job(now, now + self.lease_seconds)
            if job:
                try:
                    coordinator.capture_page(job, stopped=self._stop.is_set)
                except ConcurrentUpdate:
                    coordinator.store.save_job(job.model_copy(update={"status": "retryable", "lease_until": 0,
                        "next_attempt_at": now + 1, "error": "concurrent_page_changed"}))
                except Exception as error:
                    terminal = isinstance(error, AcquisitionFailure) and not error.retryable
                    delay = max(min(300, 2 ** min(job.attempts, 8)), getattr(error, "retry_after", 0))
                    coordinator.store.save_job(job.model_copy(update={"status": "failed" if terminal else "retryable",
                        "lease_until": 0, "next_attempt_at": coordinator.clock.now() + delay,
                        "error": str(error) if isinstance(error, AcquisitionFailure) else "optional_unit_failed"}))
                self._prefer_delivery = True
                return "page"
            pending = coordinator.store.pending_delivery(now) if self.delivery_enabled else None
            if pending:
                coordinator.deliver(pending)
                self._prefer_delivery = False
                return "delivery"
            return "idle"
        finally:
            self._unit.release()
            if self._stop.is_set():
                self.close()

    def close(self) -> None:
        self.request_stop()
        if self._closed or self._unit.locked():
            return
        self._closed = True
        for resource in reversed(self._owned):
            resource.close()
