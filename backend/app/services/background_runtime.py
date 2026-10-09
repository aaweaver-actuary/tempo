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
import logging
import os
import socket
import time

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from . import redis_admission_gate
from .background_metric_kinds import KINDS
from .background_diagnostic_types import DiagnosticTimestamp, WorkKind

_LOGGER = logging.getLogger("tempo.background.diagnostics")
STAGES = (
    "idle",
    "dispatch",
    "foreground_admission",
    "database",
    "execution",
    "unknown",
)
_reserved_database_depth: ContextVar[int] = ContextVar(
    "diagnostic_database_reservation_depth", default=0
)
_current: ContextVar["RuntimeMeasurement | None"] = ContextVar(
    "background_measurement", default=None
)


class WorkerStage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    kind: WorkKind
    worker_role: Literal["analysis", "control", "unknown"] = "unknown"
    stage: Literal[
        "idle", "dispatch", "foreground_admission", "database", "execution", "unknown"
    ]
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
    engine_available: bool = False
    engine_observed_at: DiagnosticTimestamp | None = None
    engine_stage: Literal["idle", "execution", "foreground_admission"] | None = None
    workers: list[WorkerStage] = Field(default_factory=list, max_length=16)


_PROCESS_STARTED = datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=1)
def _diagnostic_client():
    # Telemetry must never inherit the admission client's one-second network waits.
    return redis.Redis.from_url(
        os.environ["TEMPO_REDIS_URL"],
        socket_connect_timeout=0.005,
        socket_timeout=0.005,
        retry=Retry(NoBackoff(), 0),
    )


def _worker_slot() -> int:
    return (
        int.from_bytes(
            hashlib.blake2s(
                f"{socket.gethostname()}:{os.getpid()}".encode(), digest_size=2
            ).digest(),
            "big",
        )
        % 16
    )


@dataclass
class RuntimeMeasurement:
    kind: str
    dispatch_wait_seconds: float | None = None
    worker_role: str = "analysis"
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
        return WorkerStage(
            kind=self.kind if self.kind in KINDS else "other",
            stage=self.stage,
            worker_role=self.worker_role,
            observed_at=datetime.now(timezone.utc).isoformat(),
            process_started_at=_PROCESS_STARTED,
            admission_wait_seconds=wait,
            handler_elapsed_seconds=elapsed,
            execution_seconds=max(0.0, elapsed - wait),
            dispatch_wait_seconds=self.dispatch_wait_seconds,
        )

    def publish(self, *, force=False):
        from .activity_gate import activity_gate

        if _reserved_database_depth.get() or activity_gate.active_background_sections:
            return
        now = time.monotonic()
        if not force and now - self.last_publish < 5:
            return
        self.last_publish = now
        if redis_admission_gate.configured():
            try:
                _diagnostic_client().set(
                    f"tempo:diagnostics:worker:{_worker_slot()}",
                    self.sample().model_dump_json(),
                    ex=15,
                )
            except Exception:
                # Telemetry availability never changes admission or execution outcomes.
                _LOGGER.warning("worker diagnostic record unavailable")


@contextmanager
def measure_handler(kind: str, submitted_at=None, *, worker_role="analysis"):
    dispatch_wait = None
    try:
        if submitted_at is not None:
            submitted = float(submitted_at)
            if submitted > 0 and submitted <= time.time():
                dispatch_wait = time.time() - submitted
    except (ValueError, TypeError, OverflowError):
        pass
    measurement = RuntimeMeasurement(kind, dispatch_wait, worker_role=worker_role)
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
            measurement.admission_wait_seconds += max(
                0.0, time.monotonic() - measurement.admission_started
            )
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
        values = _diagnostic_client().mget(
            [f"tempo:diagnostics:worker:{slot}" for slot in range(16)]
            + ["tempo:diagnostics:engine-capacity"]
        )
        now = datetime.now(timezone.utc)
        workers = []
        for value in values[:16]:
            if value:
                sample = WorkerStage.model_validate_json(value)
                if sample.kind not in KINDS:
                    raise ValueError("Unknown worker kind")
                age = (now - datetime.fromisoformat(sample.observed_at)).total_seconds()
                if 0 <= age < 15:
                    workers.append(sample)
        engine_fields = {}
        if len(values) == 17 and values[16]:
            import json

            engine_sample = json.loads(values[16])
            age = (
                now - datetime.fromisoformat(engine_sample["observed_at"])
            ).total_seconds()
            if 0 <= age < 15:
                engine_fields = dict(
                    engine_available=True,
                    engine_observed_at=engine_sample["observed_at"],
                    engine_stage=engine_sample["stage"],
                )
        return RuntimeSnapshot(available=True, workers=workers, **engine_fields)
    except Exception:
        return RuntimeSnapshot()


@contextmanager
def reserved_database_telemetry():
    """Suppress diagnostic network I/O until both local and shared leases release."""
    token = _reserved_database_depth.set(_reserved_database_depth.get() + 1)
    try:
        yield
    finally:
        _reserved_database_depth.reset(token)


def record_idle_capacity(*, capacity="analysis"):
    """One observed idle second after actual admission; duplicate wakes coalesce."""
    if not redis_admission_gate.configured():
        return
    try:
        seconds = int(time.time())
        minute = seconds // 60
        server = _diagnostic_client()
        pipeline = server.pipeline(transaction=False)
        idle_key = f"tempo:diagnostics:idle:{'engine:' if capacity == 'engine' else ''}{minute}"
        pipeline.setbit(idle_key, seconds % 60, 1)
        pipeline.expire(idle_key, 180)
        pipeline.execute()
    except Exception:
        _LOGGER.warning("idle capacity evidence unavailable")


def idle_capacity_since(start, end, *, capacity="analysis"):
    """At most three memory-only minute bitmaps; absent samples credit no time."""
    first = max(int(start.timestamp()), int(end.timestamp()) - 120)
    last = int(end.timestamp())
    if last < first:
        return {}
    minutes = list(range(first // 60, last // 60 + 1))
    raw = _diagnostic_client().mget(
        [
            f"tempo:diagnostics:idle:{'engine:' if capacity == 'engine' else ''}{minute}"
            for minute in minutes
        ]
    )
    samples = {}
    for minute, value in zip(minutes, raw):
        for second in range(max(first, minute * 60), min(last, minute * 60 + 60)):
            offset = second % 60
            if (
                value
                and offset // 8 < len(value)
                and value[offset // 8] & (1 << (7 - offset % 8))
            ):
                samples[second] = 1
    return samples


def record_engine_capacity(stage):
    """An actual engine control probe/accepted claim; no SQL or durable payload."""
    if not redis_admission_gate.configured():
        return
    from .activity_gate import activity_gate

    if _reserved_database_depth.get() or activity_gate.active_background_sections:
        return
    if stage not in {"idle", "execution", "foreground_admission"}:
        raise ValueError("Unknown engine stage")
    try:
        import json

        _diagnostic_client().set(
            "tempo:diagnostics:engine-capacity",
            json.dumps(
                {"observed_at": datetime.now(timezone.utc).isoformat(), "stage": stage}
            ),
            ex=15,
        )
        if stage == "idle":
            record_idle_capacity(capacity="engine")
    except Exception:
        _LOGGER.warning("Engine capacity evidence unavailable")
