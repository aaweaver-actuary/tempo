"""Read-only indexed queue aggregates, independent of raw event history."""
from datetime import datetime, timedelta, timezone
import sqlite3
import time

import psycopg
from psycopg_pool import PoolTimeout

from .. import postgres_store
from ..database import read_connection
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
        connection_context = (postgres_store.diagnostic_read_connection(QUERY_BUDGET_SECONDS)
                              if postgres_store.configured() else read_connection())
        with connection_context as database:
            def query(statement, parameters=()):
                translated = postgres_store.postgres_sql(statement) if postgres_store.configured() else statement
                remaining = QUERY_BUDGET_SECONDS-(time.monotonic()-started)
                if remaining <= 0:
                    raise TimeoutError()
                if postgres_store.configured():
                    database.execute_native("SELECT set_config('statement_timeout', %s, true)",
                                            (f"{max(1,int(remaining*1000))}ms",))
                else:
                    database.set_progress_handler(lambda: int(time.monotonic()-started>=QUERY_BUDGET_SECONDS), 1000)
                cursor = (database.execute_native(translated, parameters) if postgres_store.configured()
                          else database.execute(statement, parameters))
                return cursor.fetchall()

            metadata = query("SELECT collection_started_at FROM background_diagnostic_metadata WHERE id=1")
            result["collection_started_at"] = metadata[0][0] if metadata else None
            queue_rows = []
            # One mutually exclusive classification; retrying remains an underlying state.
            classification = ("CASE WHEN t.state IN ('queued','retrying') AND (COALESCE(a.paused,0)=1 OR NOT " + task_admission_sql("t.kind") + ") THEN 'paused' "
                              "WHEN t.state IN ('queued','retrying') AND t.next_attempt_at>? THEN 'delayed' "
                              "WHEN t.state IN ('queued','retrying') AND t.kind NOT IN (" + ",".join("'"+kind+"'" for kind in sorted(KINDS-{'other','engine_game','engine_defense'})) + ") THEN 'blocked' "
                              "ELSE t.state END")
            rows = query("SELECT " + classification + " AS diagnostic_state,t.state AS underlying_state,COUNT(*) AS count,"
                         "MIN(t.pending_since) AS pending_since,MIN(t.generation_started_at) AS generation_started_at,"
                         "MIN(t.next_attempt_at) AS next_attempt_at,SUM(t.age_origin_estimated) AS estimated "
                         "FROM background_tasks t LEFT JOIN background_activity a ON a.source='durable' AND a.work_id=t.id "
                         "GROUP BY diagnostic_state,t.state", (now.isoformat(),))
            for row in rows:
                state = row["diagnostic_state"]
                queue_rows.append(dict(queue="durable", state=state, underlying_state=row["underlying_state"], count=row["count"],
                    oldest_pending_age_seconds=_age(row["pending_since"],now) if state not in ('complete','superseded') else None,
                    oldest_generation_age_seconds=_age(row["generation_started_at"],now) if state not in ('complete','superseded') else None,
                    next_eligibility_seconds=_until(row["next_attempt_at"],now) if state=='delayed' else None,
                    estimated_age_count=int(row["estimated"] or 0)))
            # Engine parent jobs and defensive requests are distinct queues; never sum them with durable jobs.
            for queue, table, identity, state_column, origin in (
                ('engine_game','game_analysis_jobs','game_id','status','pending_since'),
                ('engine_defense','threat_analysis_requests','id','state','created_at'),
            ):
                # Eligibility details below use the same defensive relation probes as its claim path.
                blocked = "0"
                if queue == 'engine_defense':
                    eligible = _ELIGIBLE_THREAT_REQUEST.replace("SELECT request.id,request.request_json", "SELECT request.id")
                    blocked = "NOT EXISTS ("+eligible+" AND request.id=t.id)"
                else:
                    blocked = "NOT EXISTS(SELECT 1 FROM imported_games g WHERE g.id=t.game_id AND g.rated=1 AND g.speed IN ('blitz','rapid','classical'))"
                global_pause = "NOT " + search_admission_sql("t.id") if queue == "engine_defense" else "FALSE"
                classification = f"CASE WHEN t.{state_column}='queued' AND (COALESCE(a.paused,0)=1 OR {global_pause}) THEN 'paused' WHEN t.{state_column}='queued' AND ({blocked}) THEN 'blocked' ELSE t.{state_column} END"
                rows = query(f"SELECT {classification} AS diagnostic_state,COUNT(*) AS count,MIN(t.{origin}) AS origin {",SUM(t.age_origin_estimated) AS estimated,MIN(t.generation_started_at) AS generation_started_at" if queue=="engine_game" else ""} "
                             f"FROM {table} t LEFT JOIN background_activity a ON a.source=? AND a.work_id=t.{identity} "
                             "GROUP BY diagnostic_state", ('game_analysis' if queue=='engine_game' else 'threat_analysis',))
                for row in rows:
                    queue_rows.append(dict(queue=queue,state=row['diagnostic_state'],count=row['count'],
                        oldest_pending_age_seconds=_age(row['origin'],now) if row['diagnostic_state']!='complete' else None,
                        oldest_generation_age_seconds=_age(row['generation_started_at'],now) if queue=='engine_game' and row['diagnostic_state']!='complete' else None,
                        estimated_age_count=int(row['estimated'] or 0) if queue=='engine_game' else 0))
            sums = ','.join(f'{"MAX" if name.endswith("_max_seconds") else "SUM"}({name}) AS {name}' for name in COUNT_NAMES + DURATION_NAMES)
            rows = query(f"SELECT kind,{sums} FROM background_metric_buckets WHERE bucket_start>=? AND bucket_start<=? GROUP BY kind",
                         (window_start.isoformat(),now.isoformat()))
            result.update(queues=queue_rows,counters=[dict(kind=row['kind'],counts={name:(int(row[name]) if name in COUNT_NAMES else float(row[name])) for name in COUNT_NAMES + DURATION_NAMES},
                                                        useful_completion_unit={'engine_game':'accepted_position','engine_defense':'accepted_position','repertoire_priority':'published_priority_generation','game_analysis_publish':'published_game_analysis'}.get(row['kind']))
                                                    for row in rows if row['kind'] in KINDS])
    except (TimeoutError, PoolTimeout, psycopg.errors.QueryCanceled):
        result.update(available=False,unavailable_reason='query_deadline')
    except (sqlite3.OperationalError, psycopg.Error):
        result.update(available=False,unavailable_reason='query_deadline' if time.monotonic()-started>=QUERY_BUDGET_SECONDS else 'storage_unavailable')
    result['query_duration_seconds'] = max(0.0,time.monotonic()-started)
    result['runtime'] = background_runtime.snapshot().model_dump()
    return BackgroundDiagnostics.model_validate(result)
