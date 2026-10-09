"""Bounded durable progress, health episodes and notification reads."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import sqlite3
import uuid
from fastapi import HTTPException
import psycopg
from .. import postgres_store
from ..database import background_connection, read_connection
from .activity_gate import activity_gate
from .background_metric_kinds import KINDS
from .defensive_analysis import DEFENSIVE_TASK_KINDS

LOGGER = logging.getLogger("tempo.activity.health")
RUNNING_SECONDS = 300
QUEUE_IDLE_SECONDS = 900
IDENTICAL_TIMEOUTS = 5
MONITOR_FRESH_SECONDS = 120
BOOTSTRAP_PAGE = 32


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    return (
        parsed.astimezone(timezone.utc)
        if parsed.tzinfo
        else parsed.replace(tzinfo=timezone.utc)
    )


def stall_reason(
    *,
    state,
    admitted_seconds,
    idle_seconds,
    identical_timeouts,
    eligible,
    evidence_available,
    waiting_reason=None,
):
    if (
        not evidence_available
        or not eligible
        or waiting_reason
        in {
            "foreground",
            "manual_pause",
            "settings_disabled",
            "retry_delay",
            "blocked",
            "unknown",
        }
    ):
        return None
    if identical_timeouts >= IDENTICAL_TIMEOUTS:
        return "repeated_timeout"
    if state in {"running", "leased"} and admitted_seconds >= RUNNING_SECONDS:
        return "running_stalled"
    if state in {"queued", "retrying"} and idle_seconds >= QUEUE_IDLE_SECONDS:
        return "queue_stalled"
    return None


def reconcile_incident(database, work, reason, now):
    lock = " FOR UPDATE" if postgres_store.configured() else ""
    existing = database.execute(
        "SELECT * FROM activity_notification_incidents WHERE kind=? AND resolved_at IS NULL"
        + lock,
        (work["kind"],),
    ).fetchone()
    if existing:
        # A replacement/other job's progress never resolves the affected episode.
        recovered = (
            existing["source"] == work["source"]
            and existing["work_id"] == work["work_id"]
            and work["progress_version"] > existing["baseline_progress_version"]
            and work["progress_generation"] == work["generation_key"]
        )
        if recovered:
            database.execute(
                "UPDATE activity_notification_incidents SET resolved_at=?,last_progress_at=? WHERE id=?",
                (now.isoformat(), work["last_progress_at"], existing["id"]),
            )
            database.execute(
                "INSERT INTO activity_notification_changes(incident_id,event,occurred_at) VALUES(?,'resolved',?) ON CONFLICT(incident_id,event) DO NOTHING",
                (existing["id"], now.isoformat()),
            )
        return existing["id"]
    if reason is None:
        return None
    identity = str(uuid.uuid4())
    database.execute(
        """INSERT INTO activity_notification_incidents(id,kind,source,work_id,generation_key,baseline_progress_version,reason,opened_at,last_progress_at)
      VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            identity,
            work["kind"],
            work["source"],
            work["work_id"],
            work["generation_key"],
            work["progress_version"],
            reason,
            now.isoformat(),
            work["last_progress_at"],
        ),
    )
    database.execute(
        "INSERT INTO activity_notification_changes(incident_id,event,occurred_at) VALUES(?,'opened',?)",
        (identity, now.isoformat()),
    )
    return identity


def record_execution_error(task, signature, *, execution_id):
    digest = (
        hashlib.sha256(
            (signature + ":" + task.get("phase", "queued")).encode()
        ).hexdigest()
        if signature == "transaction_timeout"
        else None
    )
    with activity_gate.background_control(), background_connection() as database:
        database.execute(
            """UPDATE activity_work_progress SET identical_timeouts=CASE WHEN CAST(? AS TEXT) IS NULL THEN 0 WHEN timeout_signature=? THEN identical_timeouts+1 ELSE 1 END,
          timeout_signature=?,last_error_execution_id=? WHERE source='durable' AND work_id=? AND generation_key=? AND (last_error_execution_id IS NULL OR last_error_execution_id!=?)""",
            (
                digest,
                digest,
                digest,
                execution_id,
                task["id"],
                str(task["generation"]),
                execution_id,
            ),
        )


def begin_execution(task, now=None):
    now = now or datetime.now(timezone.utc)
    execution_id = task["lease_token"]
    with activity_gate.background_control(), background_connection() as database:
        lock = " FOR UPDATE OF progress" if postgres_store.configured() else ""
        previous = database.execute(
            "SELECT progress.* FROM activity_work_progress progress JOIN background_tasks t ON t.id=progress.work_id WHERE progress.source='durable' AND progress.work_id=? AND t.generation=? AND t.state='leased' AND t.lease_token=?"
            + lock,
            (task["id"], task["generation"], task["lease_token"]),
        ).fetchone()
        if not previous or previous["execution_id"] == execution_id:
            return None
        if (
            previous
            and previous["execution_admitted_at"]
            and previous["execution_expires_at"]
        ):
            origin = timestamp(previous["execution_admitted_at"])
            if previous["last_progress_at"]:
                origin = max(origin, timestamp(previous["last_progress_at"]))
            admitted = max(
                0,
                (
                    min(now, timestamp(previous["execution_expires_at"])) - origin
                ).total_seconds(),
            )
            database.execute(
                "UPDATE activity_work_progress SET admitted_seconds=admitted_seconds+? WHERE source='durable' AND work_id=?",
                (admitted, task["id"]),
            )
        changed = database.execute(
            """UPDATE activity_work_progress SET execution_id=?,execution_admitted_at=?,execution_expires_at=?
          WHERE source='durable' AND work_id=? AND generation_key=? AND EXISTS(SELECT 1 FROM background_tasks t WHERE t.id=activity_work_progress.work_id AND t.state='leased' AND t.lease_token=?)""",
            (
                execution_id,
                now.isoformat(),
                task.get("lease_expires_at"),
                task["id"],
                str(task["generation"]),
                task["lease_token"],
            ),
        ).rowcount
    return execution_id if changed else None


def finish_execution(task, execution_id, *, admission_wait_seconds=0, now=None):
    if execution_id is None:
        return
    now = now or datetime.now(timezone.utc)
    with activity_gate.background_control(), background_connection() as database:
        row = database.execute(
            "SELECT execution_admitted_at,execution_expires_at,last_progress_at FROM activity_work_progress WHERE source='durable' AND work_id=? AND generation_key=? AND execution_id=?",
            (task["id"], str(task["generation"]), execution_id),
        ).fetchone()
        if not row or not row["execution_admitted_at"]:
            return
        started = timestamp(row["execution_admitted_at"])
        if row["last_progress_at"]:
            started = max(started, timestamp(row["last_progress_at"]))
        ended = (
            min(now, timestamp(row["execution_expires_at"]))
            if row["execution_expires_at"]
            else now
        )
        elapsed = max(0, (ended - started).total_seconds() - admission_wait_seconds)
        database.execute(
            "UPDATE activity_work_progress SET admitted_seconds=admitted_seconds+?,execution_id=NULL,execution_admitted_at=NULL,execution_expires_at=NULL WHERE source='durable' AND work_id=? AND execution_id=?",
            (elapsed, task["id"], execution_id),
        )


def notification_changes(*, after=0, limit=50):
    if after < 0:
        raise HTTPException(422, "A nonnegative notification cursor is required")
    limit = max(1, min(limit, 100))
    with read_connection() as database:
        metadata = database.execute(
            "SELECT workspace_id FROM activity_health_metadata WHERE id=1"
        ).fetchone()
        if metadata is None:
            raise HTTPException(503, "Activity monitoring storage is unavailable")
        rows = database.execute(
            """SELECT c.sequence,c.event,c.occurred_at,i.id,i.kind,i.source,i.work_id,i.generation_key,i.reason,i.opened_at,i.resolved_at,i.last_progress_at
          FROM activity_notification_changes c JOIN activity_notification_incidents i ON i.id=c.incident_id
          WHERE c.sequence>? ORDER BY c.sequence LIMIT ?""",
            (after, limit + 1),
        ).fetchall()
        monitoring = database.execute(
            "SELECT MIN(checked_at) checked_at,MIN(available) available,MIN(bootstrap_ready) ready,COUNT(*) count FROM activity_pipeline_health"
        ).fetchone()
    now = datetime.now(timezone.utc)
    as_of = monitoring["checked_at"] if monitoring else None
    monitoring_available = bool(
        monitoring
        and monitoring["count"] == len(KINDS)
        and monitoring["available"]
        and monitoring["ready"]
        and as_of
        and 0 <= (now - timestamp(as_of)).total_seconds() <= MONITOR_FRESH_SECONDS
    )
    visible = [dict(row) for row in rows[:limit]]
    return {
        "workspace_id": metadata[0],
        "items": visible,
        "next_cursor": visible[-1]["sequence"] if visible else after,
        "has_more": len(rows) > limit,
        "available": True,
        "monitoring_available": monitoring_available,
        "monitoring_as_of": as_of,
    }


def _bootstrap_pipeline(database, pipeline):
    kind = pipeline["kind"]
    sources = [("durable", "background_tasks", "id", " AND kind=?")]
    if kind == "engine_game":
        sources = [("game_analysis", "game_analysis_jobs", "game_id", "")]
    elif kind == "engine_defense":
        sources = [("threat_analysis", "threat_analysis_requests", "id", "")]
    elif kind == "coverage_explorer":
        sources.append(("coverage", "repertoire_coverage_runs", "id", ""))
    elif kind == "game_sync_window":
        sources.append(("sync", "game_sync_jobs", "id", ""))
    elif kind == "game_derivation_positions":
        sources.append(("derivation", "game_derivation_jobs", "game_id", ""))
    source_index = min(int(pipeline["bootstrap_source"]), len(sources) - 1)
    source, table, identity, extra = sources[source_index]
    parameters = [
        pipeline["bootstrap_cursor"],
        *([kind] if extra else []),
        BOOTSTRAP_PAGE,
    ]
    rows = database.execute(
        f"SELECT * FROM {table} WHERE {identity}>?{extra} ORDER BY {identity} LIMIT ?",
        tuple(parameters),
    ).fetchall()
    for row in rows:
        generation = (
            str(row["generation"])
            if source == "durable"
            else str(row["analysis_version"])
            + ":"
            + str(row["analysis_evidence_version"])
            if source == "game_analysis"
            else str(row["derivation_version"])
            if source == "derivation"
            else "1"
            if source == "threat_analysis"
            else row[identity]
        )
        pending = (
            row["pending_since"]
            if source in {"durable", "game_analysis"}
            else row["created_at"]
            if source in {"threat_analysis", "coverage", "sync"}
            else row["updated_at"]
        ) or row["updated_at"]
        paused = database.execute(
            "SELECT paused FROM background_activity WHERE source=? AND work_id=?",
            (source, row[identity]),
        ).fetchone()
        database.execute(
            """INSERT INTO activity_work_progress(source,work_id,kind,generation_key,state,next_attempt_at,pending_since,manual_paused)
          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source,work_id) DO NOTHING""",
            (
                source,
                row[identity],
                kind,
                generation,
                row["state"]
                if source in {"durable", "threat_analysis"}
                else row["status"],
                row["next_attempt_at"] if source in {"durable", "derivation"} else None,
                pending,
                int(paused[0]) if paused else 0,
            ),
        )
    completed_source = len(rows) < BOOTSTRAP_PAGE
    ready = completed_source and source_index == len(sources) - 1
    cursor = rows[-1][identity] if rows else pipeline["bootstrap_cursor"]
    if completed_source and not ready:
        source_index += 1
        cursor = ""
    database.execute(
        "UPDATE activity_pipeline_health SET bootstrap_cursor=?,bootstrap_source=?,bootstrap_ready=? WHERE kind=?",
        (cursor, source_index, int(ready), kind),
    )
    return ready


def _eligible_work(database, work, now, settings_enabled):
    if work["manual_paused"]:
        return False, "manual_pause"
    if work["kind"] in DEFENSIVE_TASK_KINDS and not settings_enabled:
        return False, "settings_disabled"
    if (
        work["state"] != "failed"
        and work["next_attempt_at"]
        and timestamp(work["next_attempt_at"]) > now
    ):
        return False, "retry_delay"
    if work["kind"] not in KINDS or work["kind"] == "other":
        return False, "blocked"
    # PostgreSQL parents summarize independently claimed child stages. Monitoring
    # their queued envelope as executable work would double-count idle capacity.
    if postgres_store.configured() and work["source"] in {
        "coverage",
        "sync",
        "derivation",
    }:
        return False, "blocked"
    if work["source"] == "coverage":
        from .canonical_scope_freshness import latest_coverage_attempt_predicate

        predicate = latest_coverage_attempt_predicate(
            database, native=postgres_store.configured()
        )
        current = database.execute(
            "SELECT 1 FROM repertoire_coverage_runs r WHERE r.id=? AND " + predicate,
            (work["work_id"],),
        ).fetchone()
        return bool(current), None if current else "blocked"
    if work["source"] == "derivation":
        current = database.execute(
            "SELECT 1 FROM imported_games WHERE id=?", (work["work_id"],)
        ).fetchone()
        return bool(current), None if current else "blocked"
    if work["source"] == "game_analysis":
        game = database.execute(
            "SELECT 1 FROM imported_games WHERE id=? AND rated=1 AND speed IN ('blitz','rapid','classical')",
            (work["work_id"],),
        ).fetchone()
        return bool(game), None if game else "blocked"
    if work["source"] == "threat_analysis":
        from ..threat_analysis_commands import _ELIGIBLE_THREAT_REQUEST
        from .defensive_analysis import search_admission_sql

        if work["state"] == "leased":
            admitted = database.execute(
                "SELECT 1 FROM threat_analysis_requests request WHERE request.id=? AND "
                + search_admission_sql("request.id"),
                (work["work_id"],),
            ).fetchone()
        else:
            admitted = database.execute(
                _ELIGIBLE_THREAT_REQUEST + " AND request.id=?", (work["work_id"],)
            ).fetchone()
        return (
            bool(admitted),
            None
            if admitted
            else "settings_disabled"
            if not settings_enabled
            else "blocked",
        )
    if work["source"] == "durable":
        row = database.execute(
            "SELECT kind,payload_json,deduplication_key FROM background_tasks WHERE id=? AND generation=?",
            (work["work_id"], int(work["generation_key"])),
        ).fetchone()
        if not row:
            return False, "blocked"
        payload = json.loads(row["payload_json"])
        if row["kind"].startswith("game_derivation_"):
            current = database.execute(
                "SELECT derivation_version FROM game_derivation_jobs WHERE game_id=?",
                (row["deduplication_key"],),
            ).fetchone()
            if not current or current[0] != payload.get("derivation_version"):
                return False, "blocked"
        if row["kind"] in {"coverage_seed", "coverage_explorer"}:
            from .canonical_scope_freshness import latest_coverage_attempt_predicate

            predicate = latest_coverage_attempt_predicate(
                database, native=postgres_store.configured()
            )
            current = database.execute(
                "SELECT 1 FROM repertoire_coverage_runs r WHERE r.id=? AND "
                + predicate,
                (payload.get("run_id"),),
            ).fetchone()
            if not current:
                return False, "blocked"
        if row["kind"] in {"game_analysis_publish", "game_analysis_followup"}:
            current = database.execute(
                "SELECT analysis_version FROM game_analysis_jobs WHERE game_id=?",
                (payload.get("game_id"),),
            ).fetchone()
            if not current or current[0] != payload.get("analysis_version"):
                return False, "blocked"
        if row["kind"] == "opening_graph_rebuild":
            current = database.execute(
                "SELECT generation FROM opening_graph_publications WHERE repertoire_id=?",
                (row["deduplication_key"],),
            ).fetchone()
            if current and int(current[0]) > int(work["generation_key"]):
                return False, "blocked"
        if row["kind"] == "integrity_scan":
            current = database.execute(
                "SELECT generation FROM opening_graph_publications WHERE repertoire_id=?",
                (row["deduplication_key"],),
            ).fetchone()
            if not current or current[0] != payload.get("graph_generation"):
                return False, "blocked"
        repertoire_id = payload.get("repertoire_id")
        if (
            repertoire_id
            and not database.execute(
                "SELECT 1 FROM repertoires WHERE id=?", (repertoire_id,)
            ).fetchone()
        ):
            return False, "blocked"
    return True, None


def _refresh_eligibility_page(database, kind, now):
    # Current-source/provenance checks are indexed per identity. This cache has
    # its own bounded reconciliation turn; a parked item cannot hide later work.
    rows = database.execute(
        "SELECT * FROM activity_work_progress WHERE kind=? AND (state IN ('queued','retrying','leased','running') OR (state='failed' AND identical_timeouts>=5)) ORDER BY COALESCE(eligibility_checked_at,''),work_id LIMIT ?",
        (kind, BOOTSTRAP_PAGE),
    ).fetchall()
    for row in rows:
        work = dict(row)
        work["manual_paused"] = 0
        work["next_attempt_at"] = None
        eligible, _reason = _eligible_work(database, work, now, True)
        database.execute(
            "UPDATE activity_work_progress SET eligible=?,eligibility_checked_at=? WHERE source=? AND work_id=?",
            (int(eligible), now.isoformat(), work["source"], work["work_id"]),
        )


def _oldest_cached_work(database, kind, states, now):
    candidates = []
    for state in states:
        row = database.execute(
            "SELECT * FROM activity_work_progress WHERE kind=? AND eligible=1 AND state=? AND manual_paused=0 AND (state='failed' OR next_attempt_at IS NULL OR next_attempt_at<=?) AND (state!='failed' OR identical_timeouts>=5) ORDER BY pending_since,work_id LIMIT 1",
            (kind, state, now.isoformat()),
        ).fetchone()
        if row:
            candidates.append(row)
    return (
        min(candidates, key=lambda row: (row["pending_since"], row["work_id"]))
        if candidates
        else None
    )


def _cache_kind_counters(database, kind, now):
    from .background_metrics import COUNT_NAMES, DURATION_NAMES

    window_start = datetime.fromtimestamp(
        int(now.timestamp()) // 300 * 300, timezone.utc
    ) - timedelta(seconds=287 * 300)
    aggregates = ",".join(
        f"{'MAX' if name.endswith('_max_seconds') else 'SUM'}({name}) AS {name}"
        for name in COUNT_NAMES + DURATION_NAMES
    )
    row = database.execute(
        f"SELECT {aggregates} FROM background_metric_buckets WHERE kind=? AND bucket_start>=? AND bucket_start<=?",
        (kind, window_start.isoformat(), now.isoformat()),
    ).fetchone()
    counts = {
        name: (int(row[name] or 0) if name in COUNT_NAMES else float(row[name] or 0))
        for name in COUNT_NAMES + DURATION_NAMES
    }
    unit = {
        "engine_game": "accepted_position",
        "engine_defense": "accepted_position",
        "repertoire_priority": "published_priority_generation",
        "game_analysis_publish": "published_game_analysis",
    }.get(kind)
    database.execute(
        "UPDATE activity_pipeline_health SET diagnostics_json=?,diagnostics_at=? WHERE kind=?",
        (
            json.dumps(
                {"kind": kind, "counts": counts, "useful_completion_unit": unit},
                separators=(",", ":"),
            ),
            now.isoformat(),
            kind,
        ),
    )


def monitor_one_pipeline(*, now=None, evidence=None):
    """One short control slice; all Redis reads finish before opening SQL."""
    now = now or datetime.now(timezone.utc)
    from . import background_runtime, redis_admission_gate

    if evidence is None:
        runtime = background_runtime.snapshot()
        try:
            foreground = activity_gate.foreground_waiting or (
                redis_admission_gate.configured()
                and redis_admission_gate.foreground_present()
            )
            evidence = {
                "available": runtime.available
                and any(worker.worker_role == "analysis" for worker in runtime.workers),
                "foreground": foreground,
                "engine_available": runtime.engine_available,
                "engine_idle_sampler": lambda start, end: (
                    background_runtime.idle_capacity_since(
                        start, end, capacity="engine"
                    )
                ),
                "idle_sampler": background_runtime.idle_capacity_since,
            }
        except Exception:
            evidence = {
                "available": False,
                "foreground": False,
                "idle_sampler": lambda _a, _b: 0,
            }
    # Read only the bounded idle-sample window before any database section.
    sampler = evidence.get("idle_sampler", lambda _a, _b: 0)
    engine_available = evidence.get("engine_available", evidence["available"])
    try:
        engine_idle_samples = (
            evidence.get("engine_idle_sampler", sampler)(
                now - timedelta(seconds=MONITOR_FRESH_SECONDS), now
            )
            if engine_available
            else {}
        )
        idle_samples = (
            sampler(now - timedelta(seconds=MONITOR_FRESH_SECONDS), now)
            if evidence["available"]
            else {}
        )
    except Exception:
        LOGGER.warning("Idle capacity evidence unavailable")
        evidence = {**evidence, "available": False}
        idle_samples = {}
        engine_idle_samples = {}
        engine_available = False
    with activity_gate.background_control(), background_connection() as database:
        if postgres_store.configured():
            database.execute_native("SELECT set_config('jit','off',true)")
        lock = " FOR UPDATE SKIP LOCKED" if postgres_store.configured() else ""
        pipeline = database.execute(
            "SELECT * FROM activity_pipeline_health WHERE bootstrap_ready=0 OR checked_at IS NULL OR checked_at<=? ORDER BY monitor_turn,kind LIMIT 1"
            + lock,
            ((now - timedelta(seconds=60)).isoformat(),),
        ).fetchone()
        if pipeline is None:
            return False
        pipeline = dict(pipeline)
        kind = pipeline["kind"]
        ready = bool(pipeline["bootstrap_ready"]) or _bootstrap_pipeline(
            database, pipeline
        )
        _refresh_eligibility_page(database, kind, now)
        setting = database.execute(
            "SELECT defensive_analysis_enabled FROM settings WHERE id=1"
        ).fetchone()
        available = (
            ready
            and setting is not None
            and (
                engine_available
                if kind in {"engine_game", "engine_defense"}
                else evidence["available"]
            )
        )
        existing = database.execute(
            "SELECT source,work_id FROM activity_notification_incidents WHERE kind=? AND resolved_at IS NULL",
            (kind,),
        ).fetchone()
        if existing:
            affected = database.execute(
                "SELECT * FROM activity_work_progress WHERE source=? AND work_id=?",
                tuple(existing),
            ).fetchone()
            if affected:
                reconcile_incident(database, dict(affected), None, now)
        # Inspect bounded indexed candidates and one unresolved episode; no queue traversal.
        running = _oldest_cached_work(database, kind, ("leased", "running"), now)
        pending = _oldest_cached_work(database, kind, ("queued", "retrying"), now)
        repeated_failure = _oldest_cached_work(database, kind, ("failed",), now)
        chosen = repeated_failure or running or pending
        health = "unknown"
        waiting = "unknown"
        reason = None
        if chosen:
            work = dict(chosen)
            eligible, waiting = _eligible_work(
                database, work, now, bool(setting[0]) if setting else False
            )
            if evidence["foreground"]:
                waiting = "foreground"
            capacity_samples = (
                engine_idle_samples
                if kind in {"engine_game", "engine_defense"}
                else idle_samples
            )
            idle_seconds = float(work["eligible_idle_seconds"])
            if available and eligible and not waiting and work["idle_observed_at"]:
                previous = timestamp(work["idle_observed_at"])
                if 0 <= (now - previous).total_seconds() <= MONITOR_FRESH_SECONDS:
                    idle_seconds += sum(
                        value
                        for second, value in capacity_samples.items()
                        if previous.timestamp() <= second < now.timestamp()
                    )
            database.execute(
                "UPDATE activity_work_progress SET eligible_idle_seconds=?,idle_observed_at=? WHERE source=? AND work_id=?",
                (
                    idle_seconds,
                    now.isoformat() if available and eligible and not waiting else None,
                    work["source"],
                    work["work_id"],
                ),
            )
            admitted = float(work["admitted_seconds"])
            if work["execution_admitted_at"] and work["execution_expires_at"]:
                execution_start = timestamp(work["execution_admitted_at"])
                if work["last_progress_at"]:
                    execution_start = max(
                        execution_start, timestamp(work["last_progress_at"])
                    )
                admitted += max(
                    0,
                    (
                        min(now, timestamp(work["execution_expires_at"]))
                        - execution_start
                    ).total_seconds(),
                )
            reason = stall_reason(
                state=work["state"],
                admitted_seconds=admitted,
                idle_seconds=idle_seconds,
                identical_timeouts=work["identical_timeouts"],
                eligible=eligible,
                evidence_available=available
                or (
                    setting is not None
                    and work["identical_timeouts"] >= IDENTICAL_TIMEOUTS
                ),
                waiting_reason=waiting,
            )
            if reason:
                reconcile_incident(database, work, reason, now)
            unresolved = database.execute(
                "SELECT 1 FROM activity_notification_incidents WHERE kind=? AND resolved_at IS NULL",
                (kind,),
            ).fetchone()
            health = (
                "needs_attention"
                if unresolved
                else "unknown"
                if not available
                else "waiting"
                if waiting or work["state"] in {"queued", "retrying"}
                else "progressing"
                if work["last_progress_at"]
                and work["progress_generation"] == work["generation_key"]
                else "unknown"
            )
            waiting = waiting or (
                "eligible_queue" if work["state"] in {"queued", "retrying"} else None
            )
        elif available:
            # An empty/disabled/delayed pipeline is known waiting, not proof of progress.
            health = "waiting"
            waiting = "no_eligible_work"
        _cache_kind_counters(database, kind, now)
        database.execute(
            "UPDATE activity_pipeline_health SET checked_at=?,monitor_turn=?,available=?,health=?,waiting_reason=?,source=?,work_id=? WHERE kind=?",
            (
                now.isoformat() if ready else None,
                now.isoformat(),
                int(available),
                health,
                waiting,
                chosen["source"] if chosen else None,
                chosen["work_id"] if chosen else None,
                kind,
            ),
        )
        return True


def best_effort(operation, *arguments, **keywords):
    """Observability outages cannot turn an accepted business result into failure."""
    try:
        return operation(*arguments, **keywords)
    except Exception:
        LOGGER.warning("Activity execution evidence unavailable", exc_info=True)
        return None


def record_engine_execution_in_transaction(
    database, kind, identity, *, completed, diagnostics=None
):
    source = "game_analysis" if kind == "engine_game" else "threat_analysis"
    if kind == "engine_game":
        position = database.execute(
            "SELECT game_id FROM game_analysis_position_reports WHERE id=?", (identity,)
        ).fetchone()
        if not position:
            return
        identity = position[0]
    now = datetime.now(timezone.utc).isoformat()
    if completed and kind == "engine_game":
        database.execute(
            """UPDATE activity_work_progress SET last_progress_at=?,progress_generation=generation_key,progress_version=progress_version+1,
          admitted_seconds=0,eligible_idle_seconds=0,idle_observed_at=NULL,identical_timeouts=0,timeout_signature=NULL,
          execution_id=NULL,execution_admitted_at=NULL,execution_expires_at=NULL WHERE source=? AND work_id=?""",
            (now, source, identity),
        )
    else:
        elapsed = diagnostics.elapsed_seconds if diagnostics and not completed else 0
        database.execute(
            """UPDATE activity_work_progress SET admitted_seconds=admitted_seconds+?,execution_id=NULL,execution_admitted_at=NULL,execution_expires_at=NULL
          WHERE source=? AND work_id=?""",
            (elapsed, source, identity),
        )
