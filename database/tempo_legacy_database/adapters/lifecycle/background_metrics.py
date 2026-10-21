"""Transaction-fenced metric buffering, stored fields and sharded delta writes."""

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


def record_metric_delta(database, key: tuple, counts: dict) -> None:
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection
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


def task_kind(database, task_id: str):
    return database.execute("SELECT kind FROM background_tasks WHERE id=?", (task_id,)).fetchone()
