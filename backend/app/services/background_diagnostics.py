"""Read-only indexed queue aggregates, independent of raw event history."""
from datetime import datetime, timedelta, timezone
import time

from tempo_legacy_database.adapters.background_diagnostics import (
    diagnostic_reader, postgres_store, read_connection, sqlite3, psycopg, PoolTimeout,
)
from .background_metrics import BackgroundDiagnostics, COUNT_NAMES, DURATION_NAMES, KINDS
from . import background_runtime
from ..threat_analysis_commands import _ELIGIBLE_THREAT_REQUEST
from .defensive_analysis import task_admission_sql, search_admission_sql

QUERY_BUDGET_SECONDS = 0.1


def _age(value, now):
    if value is None:
        return None
    try:
        origin = datetime.fromisoformat(value)
        if origin.tzinfo is None:
            origin = origin.replace(tzinfo=timezone.utc)
        return max(0.0, (now-origin).total_seconds())
    except (ValueError, TypeError):
        return None



def _until(value, now):
    if value is None:
        return None
    try:
        eligible_at = datetime.fromisoformat(value)
        if eligible_at.tzinfo is None:
            eligible_at = eligible_at.replace(tzinfo=timezone.utc)
        return max(0.0, (eligible_at-now).total_seconds())
    except (TypeError, ValueError):
        return None


def snapshot() -> BackgroundDiagnostics:
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    # Include the current partial bucket and previous 287 buckets; never imply exact rolling seconds.
    bucket_start = datetime.fromtimestamp(int(now.timestamp())//300*300, timezone.utc)
    window_start = bucket_start-timedelta(seconds=287*300)
    result = dict(generated_at=now.isoformat(), window_start=window_start.isoformat(),
                  window_end=now.isoformat(), available=True, query_duration_seconds=0)
    try:
        with diagnostic_reader(QUERY_BUDGET_SECONDS,
                lambda: QUERY_BUDGET_SECONDS-(time.monotonic()-started),
                lambda: lambda: int(time.monotonic()-started>=QUERY_BUDGET_SECONDS),
                postgres_store=postgres_store, read_connection=read_connection) as reads:
            metadata = reads.metadata()
            result["collection_started_at"] = metadata[0][0] if metadata else None
            queue_rows = []
            rows = reads.durable_rows(now,KINDS,task_admission_sql)
            for row in rows:
                state = row["diagnostic_state"]
                queue_rows.append(dict(queue="durable", state=state, underlying_state=row["underlying_state"], count=row["count"],
                    oldest_pending_age_seconds=_age(row["pending_since"],now) if state not in ('complete','superseded') else None,
                    oldest_generation_age_seconds=_age(row["generation_started_at"],now) if state not in ('complete','superseded') else None,
                    next_eligibility_seconds=_until(row["next_attempt_at"],now) if state=='delayed' else None,
                    estimated_age_count=int(row["estimated"] or 0)))
            for queue, rows in reads.engine_rows(_ELIGIBLE_THREAT_REQUEST,search_admission_sql):
                for row in rows:
                    queue_rows.append(dict(queue=queue,state=row['diagnostic_state'],count=row['count'],
                        oldest_pending_age_seconds=_age(row['origin'],now) if row['diagnostic_state']!='complete' else None,
                        oldest_generation_age_seconds=_age(row['generation_started_at'],now) if queue=='engine_game' and row['diagnostic_state']!='complete' else None,
                        estimated_age_count=int(row['estimated'] or 0) if queue=='engine_game' else 0))
            rows = reads.metric_rows(window_start,now,COUNT_NAMES,DURATION_NAMES)
            result.update(queues=queue_rows,counters=[dict(kind=row['kind'],counts={name:(int(row[name]) if name in COUNT_NAMES else float(row[name])) for name in COUNT_NAMES + DURATION_NAMES},
                                                        useful_completion_unit={'engine_game':'accepted_position','engine_defense':'accepted_position','game_analysis_publish':'published_game_analysis'}.get(row['kind']))
                                                    for row in rows if row['kind'] in KINDS])
    except (TimeoutError, PoolTimeout, psycopg.errors.QueryCanceled):
        result.update(available=False,unavailable_reason='query_deadline')
    except (sqlite3.OperationalError, psycopg.Error):
        result.update(available=False,unavailable_reason='query_deadline' if time.monotonic()-started>=QUERY_BUDGET_SECONDS else 'storage_unavailable')
    result['query_duration_seconds'] = max(0.0,time.monotonic()-started)
    result['runtime'] = background_runtime.snapshot().model_dump()
    return BackgroundDiagnostics.model_validate(result)
