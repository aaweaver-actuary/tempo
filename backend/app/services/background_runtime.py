"""Best-effort, fixed-slot live worker stages and monotonic timing; no DB writes."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
import hashlib
import json
import logging
import os
import socket
import time

# Bounded spans extend existing diagnostics; disable explicitly for paired probes.
_training_timing: ContextVar[dict | None] = ContextVar("training_timing", default=None)


@lru_cache(maxsize=1)
def _training_latency_logger():
    logger = logging.getLogger("tempo.foreground_latency")
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():
        # Uvicorn configures its own loggers rather than the root logger.
        logger.addHandler(logging.StreamHandler())
    return logger


@contextmanager
def measure_training_timing(label: str, identity: str = ""):
    if os.getenv("TEMPO_TRAINING_TIMINGS", '1') != "1":
        yield None
        return
    started = time.perf_counter()
    measurement = {"label": label, "trace": hashlib.sha256(identity.encode()).hexdigest()[:16],
                   "started_epoch_ms": time.time() * 1000, "stages_ms": {}, "connections": [],
                   "sql_count": 0, "sql_ms": 0.0, 'slow_sql': []}
    token = _training_timing.set(measurement)
    try:
        yield measurement
    finally:
        measurement["duration_ms"] = (time.perf_counter() - started) * 1000
        _training_timing.reset(token)
        _training_latency_logger().info("training_timing %s", json.dumps(measurement))


@contextmanager
def training_timing_stage(label: str):
    measurement = _training_timing.get()
    started = time.perf_counter() if measurement is not None else None
    try:
        yield
    finally:
        if measurement is not None:
            stages = measurement["stages_ms"]
            stages[label] = stages.get(label, 0.0) + (time.perf_counter() - started) * 1000


def current_training_timing():
    return _training_timing.get()

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from . import redis_admission_gate
from .background_metric_kinds import KINDS
from .background_diagnostic_types import DiagnosticTimestamp, WorkKind

_LOGGER = logging.getLogger("tempo.background.diagnostics")
STAGES = ("idle", "dispatch", "foreground_admission", "database", "execution", "unknown")
_reserved_database_depth: ContextVar[int] = ContextVar("diagnostic_database_reservation_depth", default=0)
_current: ContextVar["RuntimeMeasurement | None"] = ContextVar("background_measurement", default=None)


class WorkerStage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    kind: WorkKind
    stage: Literal["idle", "dispatch", "foreground_admission", "database", "execution", "unknown"]
    observed_at: DiagnosticTimestamp
    process_started_at: DiagnosticTimestamp
    admission_wait_seconds: float = Field(ge=0)
    handler_elapsed_seconds: float = Field(ge=0)
    execution_seconds: float = Field(ge=0)
    dispatch_wait_seconds: float | None = Field(default=None, ge=0)


class RuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    available: bool = False
    retention_seconds: Literal[15] = 15
    coverage: Literal["fixed_slots_latest_samples"] = "fixed_slots_latest_samples"
    workers: list[WorkerStage] = Field(default_factory=list, max_length=16)


_PROCESS_STARTED = datetime.now(timezone.utc).isoformat()

@lru_cache(maxsize=1)
def _diagnostic_client():
    # Telemetry must never inherit the admission client's one-second network waits.
    return redis.Redis.from_url(os.environ["TEMPO_REDIS_URL"], socket_connect_timeout=0.005,
                               socket_timeout=0.005, retry=Retry(NoBackoff(), 0))


def _worker_slot() -> int:
    return int.from_bytes(hashlib.blake2s(f"{socket.gethostname()}:{os.getpid()}".encode(), digest_size=2).digest(), "big") % 16


@dataclass
class RuntimeMeasurement:
    kind: str
    dispatch_wait_seconds: float | None = None
    started: float = field(default_factory=lambda: time.monotonic())
    admission_wait_seconds: float = 0.0
    stage: str = "execution"
    admission_depth: int = 0
    admission_started: float | None = None
    last_publish: float = float("-inf")

    def sample(self) -> WorkerStage:
        elapsed = max(0.0, time.monotonic() - self.started)
        wait = self.admission_wait_seconds
        if self.admission_started is not None:
            wait += max(0.0, time.monotonic() - self.admission_started)
        return WorkerStage(kind=self.kind if self.kind in KINDS else "other", stage=self.stage,
                           observed_at=datetime.now(timezone.utc).isoformat(), process_started_at=_PROCESS_STARTED,
                           admission_wait_seconds=wait, handler_elapsed_seconds=elapsed,
                           execution_seconds=max(0.0, elapsed-wait), dispatch_wait_seconds=self.dispatch_wait_seconds)

    def publish(self, *, force=False):
        from .activity_gate import activity_gate
        if _reserved_database_depth.get() or activity_gate.active_background_sections:
            return
        now = time.monotonic()
        if not force and now-self.last_publish < 5:
            return
        self.last_publish = now
        if redis_admission_gate.configured():
            try:
                _diagnostic_client().set(f"tempo:diagnostics:worker:{_worker_slot()}",
                                                 self.sample().model_dump_json(), ex=15)
            except Exception:
                # Telemetry availability never changes admission or execution outcomes.
                _LOGGER.warning("worker diagnostic record unavailable")


@contextmanager
def measure_handler(kind: str, submitted_at=None):
    dispatch_wait = None
    try:
        if submitted_at is not None:
            submitted = float(submitted_at)
            if submitted > 0 and submitted <= time.time():
                dispatch_wait = time.time()-submitted
    except (ValueError, TypeError, OverflowError):
        pass
    measurement = RuntimeMeasurement(kind, dispatch_wait)
    token = _current.set(measurement)
    measurement.publish(force=True)
    try:
        yield measurement
    finally:
        measurement.stage = "idle"
        measurement.publish(force=True)
        _LOGGER.info("background_handler %s", measurement.sample().model_dump_json())
        _current.reset(token)


@contextmanager
def admission_wait():
    measurement = _current.get()
    if measurement is None:
        yield
        return
    previous_stage = measurement.stage
    if measurement.admission_depth == 0:
        measurement.stage = "foreground_admission"
        measurement.publish()
        # Telemetry latency is execution overhead, not foreground admission wait.
        measurement.admission_started = time.monotonic()
    measurement.admission_depth += 1
    try:
        yield
    finally:
        measurement.admission_depth -= 1
        if measurement.admission_depth == 0:
            measurement.admission_wait_seconds += max(0.0, time.monotonic()-measurement.admission_started)
            measurement.admission_started = None
            measurement.stage = previous_stage


def heartbeat():
    measurement = _current.get()
    if measurement:
        measurement.publish()


@contextmanager
def database_stage():
    measurement = _current.get()
    previous = measurement.stage if measurement else None
    if measurement:
        measurement.stage = "database"
        measurement.publish()
    try:
        yield
    finally:
        if measurement:
            measurement.publish()
            measurement.stage = previous


def snapshot() -> RuntimeSnapshot:
    if not redis_admission_gate.configured():
        return RuntimeSnapshot()
    try:
        values = _diagnostic_client().mget([f"tempo:diagnostics:worker:{slot}" for slot in range(16)])
        now = datetime.now(timezone.utc)
        workers = []
        for value in values:
            if value:
                sample = WorkerStage.model_validate_json(value)
                if sample.kind not in KINDS:
                    raise ValueError("Unknown worker kind")
                age = (now-datetime.fromisoformat(sample.observed_at)).total_seconds()
                if 0 <= age < 15:
                    workers.append(sample)
        return RuntimeSnapshot(available=True, workers=workers)
    except Exception:
        return RuntimeSnapshot()


@contextmanager
def reserved_database_telemetry():
    """Suppress diagnostic network I/O until both local and shared leases release."""
    token = _reserved_database_depth.set(_reserved_database_depth.get()+1)
    try:
        yield
    finally:
        _reserved_database_depth.reset(token)


class StudyResponseTiming:
    """Measure response-body delivery independently of the handler/SQL spans."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if (scope['type'] != 'http' or os.getenv('TEMPO_TRAINING_TIMINGS', '1') != '1'
                or not (path.startswith('/api/queue/') or path.endswith('/review'))):
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        headers_started = None
        identity = next((value for name,value in scope.get('headers', []) if name.lower() == b'idempotency-key'), b'')
        async def measured_send(message):
            nonlocal headers_started
            if message['type'] == 'http.response.start':
                headers_started = time.perf_counter()
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                ended = time.perf_counter()
                _training_latency_logger().info('study_response_timing %s', json.dumps({
                    'trace': hashlib.sha256(identity).hexdigest()[:16],
                    'request_through_delivery_ms': (ended-started)*1000,
                    'response_delivery_ms': (ended-(headers_started or started))*1000,
                }))
        await self.app(scope, receive, measured_send)
