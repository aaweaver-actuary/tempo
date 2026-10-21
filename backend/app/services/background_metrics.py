"""Fixed-cardinality, transaction-fenced diagnostic counters (never raw payloads)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from tempo_legacy_database.adapters.lifecycle import background_metrics as storage
from tempo_legacy_database.adapters.lifecycle.background_metrics import COUNT_NAMES, DURATION_NAMES, write_metric_delta
from .background_metric_kinds import KINDS
from .background_runtime import RuntimeSnapshot
from .background_diagnostic_types import DiagnosticTimestamp, WorkKind, WorkState

BUCKET_SECONDS = 300
BUCKET_SLOTS = 288
SHARDS = 16
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
    storage.record_metric_delta(database, key, counts)


def record_event_counts(database, task_id: str, event: str, *, kind: str | None = None) -> None:
    metric = EVENT_COUNTS.get(event)
    if metric:
        if kind is None:
            # Compatibility for exceptional ID-only callers; lifecycle owners know kind.
            row = storage.task_kind(database, task_id)
            if not row:
                return
            kind = row["kind"]
        increment(database, kind, task_id, **{metric: 1})


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
    counters: list[KindCounts] = Field(default_factory=list, max_length=len(KINDS))
    runtime: "RuntimeSnapshot"
