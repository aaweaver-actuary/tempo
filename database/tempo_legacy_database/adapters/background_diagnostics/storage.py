"""Existing complete queue/bucket queries and bounded PG/SQLite reader scope."""
from contextlib import contextmanager
import sqlite3

import psycopg
from psycopg_pool import PoolTimeout

from .. import postgres_store
from ...connections import read_connection


class DiagnosticReader:
    def __init__(self, query):
        self._query = query

    def metadata(self):
        return self._query("SELECT collection_started_at FROM background_diagnostic_metadata WHERE id=1")

    def durable_rows(self, now, kinds, task_admission_sql):
        # One mutually exclusive classification; retrying remains an underlying state.
        classification = ("CASE WHEN t.state IN ('queued','retrying') AND (COALESCE(a.paused,0)=1 OR NOT " + task_admission_sql("t.kind") + ") THEN 'paused' "
                          "WHEN t.state IN ('queued','retrying') AND t.next_attempt_at>? THEN 'delayed' "
                          "WHEN t.state IN ('queued','retrying') AND t.kind NOT IN (" + ",".join("'"+kind+"'" for kind in sorted(kinds-{'other','engine_game','engine_defense'})) + ") THEN 'blocked' "
                          "ELSE t.state END")
        return self._query("SELECT " + classification + " AS diagnostic_state,t.state AS underlying_state,COUNT(*) AS count,"
                          "MIN(t.pending_since) AS pending_since,MIN(t.generation_started_at) AS generation_started_at,"
                          "MIN(t.next_attempt_at) AS next_attempt_at,SUM(t.age_origin_estimated) AS estimated "
                          "FROM background_tasks t LEFT JOIN background_activity a ON a.source='durable' AND a.work_id=t.id "
                          "GROUP BY diagnostic_state,t.state", (now.isoformat(),))

    def engine_rows(self, eligible_threat_request, search_admission_sql):
        # Engine parent jobs and defensive requests are distinct queues; never sum them with durable jobs.
        for queue, table, identity, state_column, origin in (
            ('engine_game','game_analysis_jobs','game_id','status','pending_since'),
            ('engine_defense','threat_analysis_requests','id','state','created_at'),
        ):
            # Eligibility details below use the same defensive relation probes as its claim path.
            blocked = "0"
            if queue == 'engine_defense':
                eligible = eligible_threat_request.replace("SELECT request.id,request.request_json", "SELECT request.id")
                blocked = "NOT EXISTS ("+eligible+" AND request.id=t.id)"
            else:
                blocked = "NOT EXISTS(SELECT 1 FROM imported_games g WHERE g.id=t.game_id AND g.rated=1 AND g.speed IN ('blitz','rapid','classical'))"
            global_pause = "NOT " + search_admission_sql("t.id") if queue == "engine_defense" else "FALSE"
            classification = f"CASE WHEN t.{state_column}='queued' AND (COALESCE(a.paused,0)=1 OR {global_pause}) THEN 'paused' WHEN t.{state_column}='queued' AND ({blocked}) THEN 'blocked' ELSE t.{state_column} END"
            rows = self._query(f"SELECT {classification} AS diagnostic_state,COUNT(*) AS count,MIN(t.{origin}) AS origin {",SUM(t.age_origin_estimated) AS estimated,MIN(t.generation_started_at) AS generation_started_at" if queue=="engine_game" else ""} "
                               f"FROM {table} t LEFT JOIN background_activity a ON a.source=? AND a.work_id=t.{identity} "
                               "GROUP BY diagnostic_state", ('game_analysis' if queue=='engine_game' else 'threat_analysis',))
            yield queue, rows

    def metric_rows(self, window_start, now, count_names, duration_names):
        sums = ','.join(f'{"MAX" if name.endswith("_max_seconds") else "SUM"}({name}) AS {name}' for name in count_names + duration_names)
        return self._query(f"SELECT kind,{sums} FROM background_metric_buckets WHERE bucket_start>=? AND bucket_start<=? GROUP BY kind",
                          (window_start.isoformat(),now.isoformat()))


@contextmanager
def diagnostic_reader(budget_seconds, remaining_budget, progress_callback, *, postgres_store, read_connection):
    connection_context = (postgres_store.diagnostic_read_connection(budget_seconds)
                          if postgres_store.configured() else read_connection())
    with connection_context as database:
        if postgres_store.configured():
            # Expanded freshness views can trigger JIT even for a tiny queue.
            # Compilation consumes this short operational read's budget;
            # disable it for this transaction, preserving the deadline.
            database.execute_native("SELECT set_config('jit', 'off', true)")
        def query(statement, parameters=()):
            translated = postgres_store.postgres_sql(statement) if postgres_store.configured() else statement
            remaining = remaining_budget()
            if remaining <= 0:
                raise TimeoutError()
            if postgres_store.configured():
                database.execute_native("SELECT set_config('statement_timeout', %s, true)",
                                        (f"{max(1,int(remaining*1000))}ms",))
            else:
                database.set_progress_handler(progress_callback(), 1000)
            cursor = (database.execute_native(translated, parameters) if postgres_store.configured()
                      else database.execute(statement, parameters))
            return cursor.fetchall()
        yield DiagnosticReader(query)
