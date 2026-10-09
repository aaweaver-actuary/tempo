"""Embedded durable task queue and sanitized operational projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import random
import sqlite3
import uuid

from ..database import read_connection, background_read_connection
from .. import postgres_store
from .database_executor import submit_background_write, submit_foreground_write
from .background_activity import claimable, control_order
from .defensive_analysis import DEFENSIVE_TASK_KINDS, analysis_enabled, task_admission_sql
from .background_metrics import increment, record_event_counts


ACTIVE_STATES = ("queued", "leased", "retrying")

_EVENT_INSERT_SQL = """INSERT INTO background_task_events(
               task_id,generation,event,phase,detail,created_at
           ) VALUES(?,?,?,?,?,?)"""
_EVENT_PRUNE_SQL = """DELETE FROM background_task_events
           WHERE id IN (
               SELECT id FROM background_task_events WHERE task_id=?
               ORDER BY id DESC LIMIT -1 OFFSET 100
           )"""
_TASK_BY_KIND_SQL = "SELECT * FROM background_tasks WHERE kind=? AND deduplication_key=?"
_TASK_UPSERT_SQL = """INSERT INTO background_tasks(
                   id,kind,deduplication_key,generation,priority,state,phase,
                   payload_version,payload_json,attempt_count,max_attempts,
                   next_attempt_at,lease_token,lease_expires_at,last_error,
                   created_at,started_at,completed_at,updated_at
               ) VALUES(?,?,?,?,?,'queued','queued',1,?,0,?,?,NULL,NULL,NULL,?,NULL,NULL,?)
               ON CONFLICT(kind,deduplication_key) DO UPDATE SET
                   generation=excluded.generation,
                   priority=MIN(background_tasks.priority,excluded.priority),
                   state='queued',phase='queued',payload_version=excluded.payload_version,
                   payload_json=excluded.payload_json,attempt_count=0,
                   transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,
                   max_attempts=excluded.max_attempts,next_attempt_at=excluded.next_attempt_at,
                   lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   completed_at=NULL,updated_at=excluded.updated_at"""
_TASK_BY_ID_SQL = "SELECT * FROM background_tasks WHERE id=?"
_COMPLETE_SLICE_SQL = """UPDATE background_tasks SET state='complete',phase='published',
               lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
               transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,
               completed_at=?,updated_at=?
           WHERE id=? AND generation=? AND lease_token=? AND state='leased'"""
_QUEUE_REFRESH_STATUS_SQL = """UPDATE queue_projections
    SET state=?,refresh_pending=?,last_error=? WHERE queue_date=?"""
_QUEUE_REFRESH_PROGRESS_SQL = "UPDATE queue_projections SET last_error=NULL WHERE queue_date=?"


def warm_completion_sql() -> None:
    """Translate durable-task completion SQL before a 50 ms PostgreSQL section."""

    from ..postgres_store import postgres_sql

    for statement in (
        _TASK_BY_KIND_SQL, _TASK_UPSERT_SQL, _TASK_BY_ID_SQL,
        _EVENT_INSERT_SQL, _EVENT_PRUNE_SQL, _COMPLETE_SLICE_SQL,
        _QUEUE_REFRESH_STATUS_SQL, _QUEUE_REFRESH_PROGRESS_SQL,
    ):
        postgres_sql(statement)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _record_event(
    database: sqlite3.Connection,
    task_id: str,
    generation: int,
    event: str,
    phase: str | None = None,
    detail: str | None = None,
    *,
    kind: str | None = None,
) -> None:
    database.execute(
        _EVENT_INSERT_SQL,
        (task_id, generation, event, phase, detail, _iso()),
    )
    record_event_counts(database, task_id, event, kind=kind)
    database.execute(
        _EVENT_PRUNE_SQL,
        (task_id,),
    )


def update_queue_refresh_status_in_transaction(
    database, kind: str, payload: dict, *, state: str, error: str | None = None,
) -> None:
    """Mirror an accepted task transition while its row lock still fences publication."""

    if kind != "daily_queue" or not payload.get("queue_date"):
        return
    database.execute(
        _QUEUE_REFRESH_STATUS_SQL,
        (state, int(state == "refreshing"), error, payload["queue_date"]),
    )


def clear_queue_refresh_error_in_transaction(
    database, task_id: str, *, kind: str | None, payload: dict | None = None,
) -> None:
    """Committed progress clears its error without declaring the queue ready."""

    if kind not in {None, "daily_queue"}:
        return
    if payload is None:
        task_row = database.execute(_TASK_BY_ID_SQL, (task_id,)).fetchone()
        if task_row is None or task_row["kind"] != "daily_queue":
            return
        payload = json.loads(task_row["payload_json"])
    if payload.get("queue_date"):
        database.execute(_QUEUE_REFRESH_PROGRESS_SQL, (payload["queue_date"],))


def enqueue_task(
    kind: str,
    deduplication_key: str,
    payload: dict,
    *,
    priority: int = 100,
    max_attempts: int = 5,
    delay_seconds: float = 0,
    foreground: bool = True,
) -> dict:
    """Coalesce active work and advance its input generation."""

    def operation(database: sqlite3.Connection) -> dict:
        return enqueue_task_in_transaction(
            database, kind, deduplication_key, payload,
            priority=priority, max_attempts=max_attempts,
            delay_seconds=delay_seconds,
        )

    submit = submit_foreground_write if foreground else submit_background_write
    return submit(operation, label=f"enqueue:{kind}:{deduplication_key}")


def enqueue_task_in_transaction(
    database: sqlite3.Connection,
    kind: str,
    deduplication_key: str,
    payload: dict,
    *,
    priority: int = 100,
    max_attempts: int = 5,
    delay_seconds: float = 0,
    minimum_generation: int = 0,
) -> dict:
    """Persist a task inside the caller's existing short publication transaction."""

    existing = database.execute(
            _TASK_BY_KIND_SQL,
            (kind, deduplication_key),
    ).fetchone()
    now = _now()
    task_id = existing["id"] if existing else str(uuid.uuid4())
    generation = max(
        int(existing["generation"]) + 1 if existing else 1,
        minimum_generation + 1,
    )
    next_attempt_at = _iso(now + timedelta(seconds=delay_seconds))
    created_at = existing["created_at"] if existing else _iso(now)
    database.execute(
            _TASK_UPSERT_SQL,
            (
                task_id,
                kind,
                deduplication_key,
                generation,
                priority,
                json.dumps(payload, separators=(",", ":")),
                max_attempts,
                next_attempt_at,
                created_at,
                _iso(now),
            ),
    )
    _record_event(database, task_id, generation, "enqueued", "queued", kind=kind)
    queued_row = database.execute(_TASK_BY_ID_SQL, (task_id,)).fetchone()
    if queued_row["replaced_pending_generation"]:
        _record_event(database, task_id, generation, "generation_replaced", "queued", kind=kind)
    return dict(queued_row)


def enqueue_compact_postgres_task_in_transaction(
    database, kind: str, deduplication_key: str, payload: dict,
    *, priority: int, delay_seconds: float = 0,
) -> None:
    """Checkpoint a follow-up and its event in one bounded PostgreSQL statement."""

    from datetime import timedelta

    now = _now()
    database.execute_native(
        "WITH queued AS ("
        "INSERT INTO background_tasks("
        "id,kind,deduplication_key,generation,priority,state,phase,payload_version,"
        "payload_json,attempt_count,max_attempts,next_attempt_at,lease_token,"
        "lease_expires_at,last_error,created_at,started_at,completed_at,updated_at) "
        "VALUES(%s,%s,%s,1,%s,'queued','queued',1,%s,0,5,%s,"
        "NULL,NULL,NULL,%s,NULL,NULL,%s) "
        "ON CONFLICT(kind,deduplication_key) DO UPDATE SET "
        "generation=background_tasks.generation+1,"
        "priority=LEAST(background_tasks.priority,excluded.priority),"
        "state='queued',phase='queued',payload_version=1,payload_json=excluded.payload_json,"
        "attempt_count=0,max_attempts=5,next_attempt_at=excluded.next_attempt_at,"
        "transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,"
        "lease_token=NULL,lease_expires_at=NULL,last_error=NULL,"
        "completed_at=NULL,updated_at=excluded.updated_at RETURNING id,generation) "
        "INSERT INTO background_task_events(task_id,generation,event,phase,detail,created_at) "
        "SELECT id,generation,'enqueued','queued',NULL,%s FROM queued",
        (str(uuid.uuid4()), kind, deduplication_key, priority,
         json.dumps(payload, separators=(",", ":")),
         _iso(now + timedelta(seconds=delay_seconds)),
         _iso(now), _iso(now), _iso(now)),
    )
    queued = database.execute(_TASK_BY_KIND_SQL, (kind, deduplication_key)).fetchone()
    increment(database, kind, queued["id"], generations_started=1,
              generation_replacements=int(queued["replaced_pending_generation"]))
    database.execute(_EVENT_PRUNE_SQL, (queued["id"],))


def claim_task(
    kind: str | None = None, *, allowed_kinds: tuple[str, ...] | None = None,
    lease_seconds: int = 60,
) -> dict | None:
    if kind is not None and allowed_kinds is not None:
        raise ValueError("Use one durable-task kind filter")
    if allowed_kinds is not None and not allowed_kinds:
        raise ValueError("Allowed durable-task kinds cannot be empty")

    def operation(database: sqlite3.Connection) -> dict | None:
        now = _iso()
        reclaimed_phase = "" if postgres_store.configured() else ",phase='reclaimed'"
        reclaimed = database.execute(
            f"""UPDATE background_tasks
               SET state='queued'{reclaimed_phase},lease_token=NULL,lease_expires_at=NULL,
                   updated_at=?
               WHERE state='leased' AND lease_expires_at<=? RETURNING id,kind,generation""",
            (now, now),
        ).fetchall()
        # Reuse bounded aggregate shards rather than add one query/write per expired lease.
        from collections import Counter
        from .background_metrics import metric_shard
        reclaimed_groups = Counter((task["kind"],metric_shard(task["id"])) for task in reclaimed)
        for (reclaimed_kind, shard), reclaimed_count in reclaimed_groups.items():
            increment(database, reclaimed_kind, "", shard_override=shard,
                      lease_expiries=reclaimed_count, lease_reclaims=reclaimed_count,
                      generation_restarts=reclaimed_count)
        parameters: list[str] = [now]
        kind_clause = ""
        if kind is not None:
            kind_clause = " AND kind=?"
            parameters.append(kind)
        elif allowed_kinds is not None:
            kind_clause = " AND kind IN (" + ",".join("?" for _ in allowed_kinds) + ")"
            parameters.extend(allowed_kinds)
        row = database.execute(
            f"""SELECT * FROM background_tasks
                WHERE state IN ('queued','retrying') AND next_attempt_at<=?{kind_clause}
                  AND {claimable('durable', 'background_tasks.id')}
                  AND {task_admission_sql('background_tasks.kind')}
                ORDER BY priority,{control_order('durable', 'background_tasks.id')}next_attempt_at,created_at LIMIT 1""",
            parameters,
        ).fetchone()
        if not row:
            return None
        lease_token = str(uuid.uuid4())
        lease_expires_at = _iso(_now() + timedelta(seconds=lease_seconds))
        claimed_phase = "" if postgres_store.configured() else ",phase='claimed'"
        changed = database.execute(
            f"""UPDATE background_tasks
               SET state='leased'{claimed_phase},attempt_count=attempt_count+1,
                   lease_token=?,lease_expires_at=?,started_at=COALESCE(started_at,?),updated_at=?
               WHERE id=? AND generation=? AND state IN ('queued','retrying')""",
            (lease_token, lease_expires_at, now, now, row["id"], row["generation"]),
        ).rowcount
        if not changed:
            return None
        _record_event(database, row["id"], row["generation"], "claimed", "claimed", kind=row["kind"])
        claimed = dict(database.execute("SELECT * FROM background_tasks WHERE id=?", (row["id"],)).fetchone())
        claimed["payload"] = json.loads(claimed.pop("payload_json"))
        return claimed

    return submit_background_write(
        operation,
        label=f"claim:{kind or (','.join(allowed_kinds) if allowed_kinds else 'any')}",
    )


def complete_task(task_id: str, generation: int, lease_token: str, *, kind: str | None = None) -> bool:
    def operation(database: sqlite3.Connection) -> bool:
        now = _iso()
        changed = database.execute(
            """UPDATE background_tasks SET state='complete',phase='published',
                   lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,
                   completed_at=?,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (now, now, task_id, generation, lease_token),
        ).rowcount
        if changed:
            clear_queue_refresh_error_in_transaction(database, task_id, kind=kind)
            _record_event(database, task_id, generation, "published", "published", kind=kind)
        else:
            _record_stale_result(database, task_id, generation, kind or "other")
        return bool(changed)

    return submit_background_write(operation, label=f"complete:{task_id}")


def lock_current_slice(database, task: dict) -> bool:
    """Lock a claimed PostgreSQL task before publishing a slice's effects."""

    row = database.execute(
        "SELECT generation,lease_token,state FROM background_tasks WHERE id=? FOR UPDATE",
        (task["id"],),
    ).fetchone()
    current = bool(
        row and row["generation"] == task["generation"]
        and row["lease_token"] == task["lease_token"] and row["state"] == "leased"
    )
    if not current:
        increment(database, task.get("kind", "other"), task["id"], stale_results=1)
    return current


def advance_task_slice_in_transaction(
    database, task: dict, *, next_phase: str, next_payload: dict,
) -> bool:
    """Commit slice effects and restart state together in the caller's transaction."""

    now = _iso()
    changed = database.execute(
        """UPDATE background_tasks SET state='queued',phase=?,payload_json=?,
               attempt_count=0,next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,
               last_error=NULL,updated_at=?,
               transaction_timeout_count=0,transaction_timeout_checkpoint=NULL
           WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        (next_phase, json.dumps(next_payload, separators=(",", ":")), now, now,
         task["id"], task["generation"], task["lease_token"]),
    ).rowcount
    if changed:
        clear_queue_refresh_error_in_transaction(database, task["id"], kind=task.get("kind"),
                                                payload=task.get("payload"))
        _record_event(database, task["id"], task["generation"], "slice_complete", next_phase, kind=task.get("kind"))
    return bool(changed)


def complete_task_slice_in_transaction(database, task: dict) -> bool:
    """Complete a PostgreSQL task in the same transaction as its final publication."""

    now = _iso()
    changed = database.execute(
        _COMPLETE_SLICE_SQL,
        (now, now, task["id"], task["generation"], task["lease_token"]),
    ).rowcount
    if changed:
        clear_queue_refresh_error_in_transaction(database, task["id"], kind=task.get("kind"),
                                                payload=task.get("payload"))
        _record_event(database, task["id"], task["generation"], "published", "published", kind=task.get("kind"))
    else:
        _record_stale_result(database, task["id"], task["generation"], task.get("kind", "other"))
    return bool(changed)


def fail_task(task_id: str, generation: int, lease_token: str, error: Exception) -> dict:
    sanitized_error = str(error)[:500]

    def operation(database: sqlite3.Connection) -> dict:
        if postgres_store.configured():
            from .prefix_transition_application import lock_linked_receipts
            lock_linked_receipts(database, task_id, generation)
        row = database.execute(
            "SELECT attempt_count,max_attempts,kind,payload_json FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
            (task_id, generation, lease_token),
        ).fetchone()
        if not row:
            return {"state": "superseded"}
        terminal = row["attempt_count"] >= row["max_attempts"]
        delay = min(60.0, (2 ** max(0, row["attempt_count"] - 1)) + random.random())
        state = "failed" if terminal else "retrying"
        now = _now()
        retry_phase = "phase" if postgres_store.configured() else "?"
        changed = database.execute(
            f"""UPDATE background_tasks SET state=?,phase={retry_phase},next_attempt_at=?,
                   lease_token=NULL,lease_expires_at=NULL,last_error=?,
                   completed_at=?,updated_at=? WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (
                state,
                *((state,) if not postgres_store.configured() else ()),
                _iso(now + timedelta(seconds=delay)),
                sanitized_error,
                _iso(now) if terminal else None,
                _iso(now),
                task_id,
                generation,
                lease_token,
            ),
        ).rowcount
        if not changed:
            return {"state": "superseded"}
        update_queue_refresh_status_in_transaction(
            database, row["kind"], json.loads(row["payload_json"]),
            state="failed" if terminal else "refreshing", error=sanitized_error,
        )
        if terminal and row["kind"] in {"game_sync_window", "game_sync_record"}:
            sync_payload = json.loads(row["payload_json"])
            job_id = sync_payload["job_id"]
            database.execute(
                """UPDATE game_sync_jobs SET status='failed',error=?,completed_at=?,updated_at=?
                   WHERE id=? AND status IN ('queued','running','paused','retrying')""",
                (sanitized_error, _iso(now), _iso(now), job_id),
            )
            if row["kind"] == "game_sync_window":
                provider_row = database.execute(
                    "SELECT provider FROM game_sync_windows WHERE id=?",
                    (sync_payload["window_id"],),
                ).fetchone()
                provider = provider_row["provider"] if provider_row else None
            else:
                provider = sync_payload["record"]["provider"]
            if provider:
                database.execute(
                    "UPDATE game_sync_state SET status='error',last_error=? WHERE provider=?",
                    (sanitized_error, provider),
                )
        if row["kind"] == "integrity_scan":
            integrity_payload = json.loads(row["payload_json"])
            database.execute(
                "UPDATE repertoire_integrity_state SET scan_status=?,scan_error=? "
                "WHERE repertoire_id=? AND scan_generation=?",
                ("failed" if terminal else "retrying", sanitized_error,
                 integrity_payload["repertoire_id"], f"{task_id}:{generation}"),
            )
        if terminal and row["kind"] in {"coverage_seed", "coverage_explorer"}:
            coverage_payload = json.loads(row["payload_json"])
            database.execute(
                "UPDATE repertoire_coverage_runs SET status='failed',last_error=?,updated_at=? "
                "WHERE id=? AND status IN ('building','queued','running')",
                (sanitized_error, _iso(now), coverage_payload["run_id"]),
            )
        if terminal and row["kind"] == "game_analysis_publish":
            publication_payload = json.loads(row["payload_json"])
            game_id = publication_payload["game_id"]
            database.execute(
                "UPDATE game_analysis_publications SET status='failed',last_error=?,updated_at=? "
                "WHERE game_id=? AND status IN ('queued','publishing')",
                (sanitized_error, _iso(now), game_id),
            )
            database.execute(
                "UPDATE game_analysis_jobs SET status='failed',last_error=?,updated_at=? "
                "WHERE game_id=? AND status='publishing'",
                (sanitized_error, _iso(now), game_id),
            )
            database.execute(
                "UPDATE imported_games SET analysis_state='failed' WHERE id=?",
                (game_id,),
            )
        if terminal and postgres_store.configured():
            from .prefix_transition_application import record_linked_failure
            record_linked_failure(database, task_id, generation, sanitized_error)
        _record_event(database, task_id, generation, state, state, sanitized_error, kind=row["kind"])
        return {"state": state, "next_attempt_at": _iso(now + timedelta(seconds=delay))}

    return submit_background_write(operation, label=f"fail:{task_id}")


def defer_task_for_contention(task_id: str, generation: int, lease_token: str, *, kind: str | None = None) -> bool:
    """Yield an expected PostgreSQL lock timeout without spending a retry."""

    def operation(database: sqlite3.Connection) -> bool:
        next_attempt_at = _iso(_now() + timedelta(milliseconds=250))
        yielded_phase = "" if postgres_store.configured() else ",phase='yielded'"
        changed = database.execute(
            f"""UPDATE background_tasks SET state='retrying'{yielded_phase},
               attempt_count=CASE WHEN attempt_count>0 THEN attempt_count-1 ELSE 0 END,
               next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,
               last_error=NULL,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (next_attempt_at, _iso(), task_id, generation, lease_token),
        ).rowcount
        if changed:
            _record_event(database, task_id, generation, "yielded", "yielded", kind=kind)
        return bool(changed)

    return submit_background_write(operation, label=f"yield:{task_id}")


def defer_task_for_transaction_timeout(task: dict, error: Exception) -> bool:
    """Preserve a checkpoint and cool repeated deadline failures down to 60 s.

    A transaction deadline is not evidence of a competing lock. Keep its error
    visible and its episode counter durable without exhausting ordinary retries.
    """
    def operation(database):
        row = database.execute(
            "SELECT phase,payload_json,transaction_timeout_count,transaction_timeout_checkpoint "
            "FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
            (task['id'], task['generation'], task['lease_token']),
        ).fetchone()
        if row is None:
            return False
        saved_payload = json.loads(row['payload_json'])
        checkpoint = json.dumps([task['generation'], row['phase'], saved_payload], sort_keys=True)
        # Upgrade an existing episode in place; old rows lack the generation
        # prefix, and a process restart must not discard their current backoff.
        legacy_checkpoint = json.dumps([row['phase'], saved_payload], sort_keys=True)
        timeout_count = (int(row['transaction_timeout_count']) + 1
                         if row['transaction_timeout_checkpoint'] in {checkpoint, legacy_checkpoint} else 1)
        delay_seconds = min(60, 2 ** min(timeout_count - 1, 6))
        now = _now()
        sanitized_error = str(error)[:500]
        changed = database.execute(
            """UPDATE background_tasks SET state='retrying',
               attempt_count=CASE WHEN attempt_count>0 THEN attempt_count-1 ELSE 0 END,
               transaction_timeout_count=?,transaction_timeout_checkpoint=?,
               next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,
               last_error=?,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (timeout_count, checkpoint, _iso(now + timedelta(seconds=delay_seconds)),
             sanitized_error, _iso(now), task['id'], task['generation'], task['lease_token']),
        ).rowcount
        if changed:
            update_queue_refresh_status_in_transaction(
                database, task['kind'], saved_payload,
                state="refreshing", error=sanitized_error,
            )
            _record_event(database, task['id'], task['generation'], 'yielded', row['phase'],
                          sanitized_error, kind=task['kind'])
        return bool(changed)
    return submit_background_write(operation, label=f"deadline:{task['id']}")


def retry_task(task_id: str) -> dict | None:
    def operation(database: sqlite3.Connection) -> dict | None:
        row = database.execute(
            "SELECT * FROM background_tasks WHERE id=? AND state='failed'", (task_id,)
        ).fetchone()
        if not row:
            return None
        now = _iso()
        manual_phase = "" if postgres_store.configured() else ",phase='queued'"
        database.execute(
            f"""UPDATE background_tasks SET state='queued'{manual_phase},attempt_count=0,
                   transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,
                   next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   completed_at=NULL,updated_at=? WHERE id=?""",
            (now, now, task_id),
        )
        update_queue_refresh_status_in_transaction(
            database, row["kind"], json.loads(row["payload_json"]), state="refreshing",
        )
        database.execute(
            """UPDATE background_activity SET phase='Queued',completed_units=NULL,
               total_units=NULL,updated_at=? WHERE source='durable' AND work_id=?""",
            (now, task_id),
        )
        _record_event(database, task_id, row["generation"], "manual_retry", "queued", kind=row["kind"])
        return serialize_task(
            database.execute("SELECT * FROM background_tasks WHERE id=?", (task_id,)).fetchone()
        )

    return submit_foreground_write(operation, label=f"retry:{task_id}")


def requeue_interrupted_tasks() -> None:
    def operation(database: sqlite3.Connection) -> None:
        now = _iso()
        interrupted = database.execute(
            "SELECT id,generation,kind FROM background_tasks WHERE state='leased'"
        ).fetchall()
        interrupted_phase = "" if postgres_store.configured() else ",phase='reclaimed'"
        database.execute(
            f"""UPDATE background_tasks SET state='queued'{interrupted_phase},
                   lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE state='leased'""",
            (now,),
        )
        for row in interrupted:
            _record_event(database, row["id"], row["generation"], "reclaimed", "queued", kind=row["kind"])
            _record_event(database, row["id"], row["generation"], "restarted", "queued", kind=row["kind"])

    submit_foreground_write(operation, label="requeue-interrupted-tasks")


def serialize_task(row: sqlite3.Row | dict) -> dict:
    try:
        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((_now() - created_at).total_seconds()))
    except (TypeError, ValueError):
        age_seconds = 0
    return {
        "id": row["id"],
        "kind": row["kind"],
        "deduplication_key": row["deduplication_key"],
        "generation": row["generation"],
        "priority": row["priority"],
        "state": row["state"],
        "phase": row["phase"],
        "attempts": row["attempt_count"],
        "max_attempts": row["max_attempts"],
        "next_retry": row["next_attempt_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "age_seconds": age_seconds,
        "last_error": row["last_error"],
    }


def list_tasks() -> list[dict]:
    with read_connection() as database:
        rows = database.execute(
            """SELECT * FROM background_tasks
               WHERE state!='complete' OR updated_at>=datetime('now','-1 day')
               ORDER BY CASE state WHEN 'failed' THEN 0 WHEN 'leased' THEN 1 ELSE 2 END,
                        priority,created_at"""
        ).fetchall()
    return [serialize_task(row) for row in rows]


def defer_paused_defensive_task(task: dict) -> bool:
    """Return one pre-dispatched slice without consuming its failure budget or waking retries."""
    if task['kind'] not in DEFENSIVE_TASK_KINDS:
        return False

    def operation(database):
        if analysis_enabled(database):
            return False
        database.execute(
            """UPDATE background_tasks SET state='queued',phase='queued',lease_token=NULL,
               lease_expires_at=NULL,attempt_count=MAX(0,attempt_count-1),updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (_iso(), task['id'], task['generation'], task['lease_token']),
        )
        return True

    return submit_background_write(operation, label='pause:defensive-slice')


def current_delivery(task: dict) -> bool:
    """Admitted primary read; transactional publication fences remain authoritative."""
    with background_read_connection(authoritative=True) as database:
        row = database.execute(
            "SELECT generation,lease_token,state FROM background_tasks WHERE id=?", (task["id"],),
        ).fetchone()
    return bool(row and row["generation"] == task["generation"]
                and row["lease_token"] == task["lease_token"] and row["state"] == "leased")


def record_stale_delivery(task: dict) -> None:
    def operation(database):
        exists = database.execute("SELECT 1 FROM background_tasks WHERE id=?", (task["id"],)).fetchone()
        if exists:
            _record_event(database, task["id"], task["generation"], "stale_delivery", "discarded", kind=task["kind"])
        else:
            increment(database, task["kind"], task["id"], stale_deliveries=1)
    submit_background_write(operation, label="diagnostics:stale-delivery")


def _record_stale_result(database, task_id: str, generation: int, kind: str = "other") -> None:
    """A removed task has no raw-event foreign key, but its rejected result still counts."""
    row = database.execute("SELECT kind FROM background_tasks WHERE id=?", (task_id,)).fetchone()
    if row:
        _record_event(database, task_id, generation, "stale_generation_discarded", "discarded", kind=row["kind"])
    else:
        increment(database, kind, task_id, stale_results=1)
