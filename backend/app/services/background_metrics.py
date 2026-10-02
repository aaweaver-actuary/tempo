"""Fixed-cardinality, transaction-fenced diagnostic counters (never raw payloads)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from .background_metric_kinds import KINDS
from .background_runtime import RuntimeSnapshot
from .background_diagnostic_types import DiagnosticTimestamp, WorkKind, WorkState

BUCKET_SECONDS = 300
BUCKET_SLOTS = 288
SHARDS = 16
DURATION_NAMES = ("engine_successful_seconds", "engine_preempted_seconds", "engine_abandoned_seconds", "engine_successful_max_seconds", "engine_preempted_max_seconds", "engine_abandoned_max_seconds")
COUNT_NAMES = (
    "generations_started",
    "generation_replacements",
    "generation_restarts",
    "claims",
    "slices",
    "retries",
    "completed_generations",
    "useful_completions",
    "stale_deliveries",
    "stale_results",
    "lease_expiries",
    "lease_reclaims",
    "contention_deferrals",
    "engine_completed_positions",
    "engine_preemptions",
    "engine_timeouts",
    "engine_failures",
    "priority_calculator_calls",
    "priority_publications",
    "priority_published_records",
    "engine_successful_samples",
    "engine_abandoned_samples",
    "engine_unknown_timing_attempts",
)
# Keep labels finite even when extensions enqueue unknown kinds.
EVENT_COUNTS = {
    "enqueued": "generations_started", "generation_replaced": "generation_replacements",
    "restarted": "generation_restarts", "claimed": "claims", "slice_complete": "slices",
    "retrying": "retries", "manual_retry": "generation_restarts", "published": "completed_generations",
    "stale_delivery": "stale_deliveries", "stale_generation_discarded": "stale_results",
    "lease_expired": "lease_expiries", "reclaimed": "lease_reclaims", "yielded": "contention_deferrals",
}


def metric_shard(identity: str) -> int:
    return int.from_bytes(hashlib.blake2s(identity.encode(), digest_size=2).digest(), "big") % SHARDS


def increment(database, kind: str, identity: str, *, now: datetime | None = None, shard_override: int | None = None, **counts: int | float) -> None:
    """One sharded UPSERT inside the existing outcome transaction; no global hot row."""
    if not counts or any(name not in COUNT_NAMES + DURATION_NAMES or isinstance(value, bool)
                         or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                         or (name in COUNT_NAMES and type(value) is not int) for name, value in counts.items()):
        raise ValueError("Invalid background counter")
    kind = kind if kind in KINDS else "other"
    now = now or datetime.now(timezone.utc)
    bucket_number = int(now.timestamp()) // BUCKET_SECONDS
    bucket_start = datetime.fromtimestamp(bucket_number * BUCKET_SECONDS, timezone.utc).isoformat()
    shard = metric_shard(identity) if shard_override is None else shard_override
    if not 0 <= shard < SHARDS:
        raise ValueError("Invalid metric shard")
    key = (kind, shard, bucket_number % BUCKET_SLOTS, bucket_start)
    from ..postgres_store import PostgresConnection
    if isinstance(database, PostgresConnection):
        database.add_background_metrics(key, counts)
    else:
        write_metric_delta(database, key, counts)


def write_metric_delta(database, key: tuple, counts: dict) -> None:
    """Flush already-validated increments after domain writes in deterministic lock order."""
    names = list(counts)
    updates = []
    for name in COUNT_NAMES + DURATION_NAMES:
        aggregate = (f"CASE WHEN background_metric_buckets.{name}>excluded.{name} "
                     f"THEN background_metric_buckets.{name} ELSE excluded.{name} END"
                     if name.endswith("_max_seconds") else f"background_metric_buckets.{name}+excluded.{name}")
        updates.append(f"{name}=CASE WHEN background_metric_buckets.bucket_start=excluded.bucket_start "
                       f"THEN {aggregate} ELSE excluded.{name} END")
    updates = ",".join(updates)
    sql = ("INSERT INTO background_metric_buckets(kind,shard,slot,bucket_start," + ",".join(names) + ") "
           "VALUES(" + ",".join("?" for _ in range(4 + len(names))) + ") "
           "ON CONFLICT(kind,shard,slot) DO UPDATE SET " + updates + ",bucket_start=excluded.bucket_start")
    parameters = (*key, *counts.values())
    if hasattr(database, "execute_native"):
        database.execute_native(sql.replace("?", "%s"), parameters)
    else:
        database.execute(sql, parameters)


def record_event_counts(database, task_id: str, event: str) -> None:
    metric = EVENT_COUNTS.get(event)
    if metric:
        row = database.execute("SELECT kind FROM background_tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            increment(database, row["kind"], task_id, **{metric: 1})


class QueueDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    queue: Literal["durable", "engine_game", "engine_defense"]
    underlying_state: WorkState | None = None
    state: WorkState
    count: int = Field(ge=0)
    oldest_pending_age_seconds: float | None = Field(default=None, ge=0)
    oldest_generation_age_seconds: float | None = Field(default=None, ge=0)
    next_eligibility_seconds: float | None = Field(default=None, ge=0)
    estimated_age_count: int = Field(default=0, ge=0)


class MetricCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    generations_started: int = Field(default=0, ge=0)
    generation_replacements: int = Field(default=0, ge=0)
    generation_restarts: int = Field(default=0, ge=0)
    claims: int = Field(default=0, ge=0)
    slices: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    completed_generations: int = Field(default=0, ge=0)
    useful_completions: int = Field(default=0, ge=0)
    stale_deliveries: int = Field(default=0, ge=0)
    stale_results: int = Field(default=0, ge=0)
    lease_expiries: int = Field(default=0, ge=0)
    lease_reclaims: int = Field(default=0, ge=0)
    contention_deferrals: int = Field(default=0, ge=0)
    engine_completed_positions: int = Field(default=0, ge=0)
    engine_preemptions: int = Field(default=0, ge=0)
    engine_timeouts: int = Field(default=0, ge=0)
    engine_failures: int = Field(default=0, ge=0)
    priority_calculator_calls: int = Field(default=0, ge=0)
    priority_publications: int = Field(default=0, ge=0)
    priority_published_records: int = Field(default=0, ge=0)


    engine_successful_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    engine_preempted_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    engine_abandoned_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)


    engine_successful_samples: int = Field(default=0, ge=0)
    engine_abandoned_samples: int = Field(default=0, ge=0)
    engine_unknown_timing_attempts: int = Field(default=0, ge=0)
    engine_successful_max_seconds: float = Field(default=0, ge=0)
    engine_preempted_max_seconds: float = Field(default=0, ge=0)
    engine_abandoned_max_seconds: float = Field(default=0, ge=0)


class KindCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    kind: WorkKind
    counts: MetricCounts
    useful_completion_unit: Literal["accepted_position", "published_priority_generation", "published_game_analysis"] | None = None


class BackgroundDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1
    collection_started_at: DiagnosticTimestamp | None = None
    generated_at: DiagnosticTimestamp
    window_start: DiagnosticTimestamp
    window_end: DiagnosticTimestamp
    bucket_seconds: Literal[300] = 300
    retention_seconds: Literal[86400] = 86400
    query_duration_seconds: float = Field(ge=0)
    available: bool
    unavailable_reason: Literal["query_deadline", "storage_unavailable"] | None = None
    queues: list[QueueDiagnostic] = Field(default_factory=list, max_length=27)
    counters: list[KindCounts] = Field(default_factory=list, max_length=33)
    runtime: "RuntimeSnapshot"
