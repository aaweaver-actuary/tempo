"""Embedded durable task queue and sanitized operational projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import random
import sqlite3
import uuid

from ..database import read_connection
from .database_executor import submit_background_write, submit_foreground_write
from .background_activity import claimable, control_order


ACTIVE_STATES = ("queued", "leased", "retrying")


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
) -> None:
    database.execute(
        """INSERT INTO background_task_events(
               task_id,generation,event,phase,detail,created_at
           ) VALUES(?,?,?,?,?,?)""",
        (task_id, generation, event, phase, detail, _iso()),
    )
    database.execute(
        """DELETE FROM background_task_events
           WHERE id IN (
               SELECT id FROM background_task_events WHERE task_id=?
               ORDER BY id DESC LIMIT -1 OFFSET 100
           )""",
        (task_id,),
    )


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
        existing = database.execute(
            "SELECT * FROM background_tasks WHERE kind=? AND deduplication_key=?",
            (kind, deduplication_key),
        ).fetchone()
        now = _now()
        task_id = existing["id"] if existing else str(uuid.uuid4())
        generation = int(existing["generation"]) + 1 if existing else 1
        next_attempt_at = _iso(now + timedelta(seconds=delay_seconds))
        created_at = existing["created_at"] if existing else _iso(now)
        database.execute(
            """INSERT INTO background_tasks(
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
                   max_attempts=excluded.max_attempts,next_attempt_at=excluded.next_attempt_at,
                   lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   completed_at=NULL,updated_at=excluded.updated_at""",
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
        _record_event(database, task_id, generation, "enqueued", "queued")
        return dict(
            database.execute("SELECT * FROM background_tasks WHERE id=?", (task_id,)).fetchone()
        )

    submit = submit_foreground_write if foreground else submit_background_write
    return submit(operation, label=f"enqueue:{kind}:{deduplication_key}")


def claim_task(kind: str | None = None, *, lease_seconds: int = 60) -> dict | None:
    def operation(database: sqlite3.Connection) -> dict | None:
        now = _iso()
        database.execute(
            """UPDATE background_tasks
               SET state='queued',phase='reclaimed',lease_token=NULL,lease_expires_at=NULL,
                   updated_at=?
               WHERE state='leased' AND lease_expires_at<=?""",
            (now, now),
        )
        parameters: list[str] = [now]
        kind_clause = ""
        if kind is not None:
            kind_clause = " AND kind=?"
            parameters.append(kind)
        row = database.execute(
            f"""SELECT * FROM background_tasks
                WHERE state IN ('queued','retrying') AND next_attempt_at<=?{kind_clause}
                  AND {claimable('durable', 'background_tasks.id')}
                ORDER BY priority,{control_order('durable', 'background_tasks.id')}next_attempt_at,created_at LIMIT 1""",
            parameters,
        ).fetchone()
        if not row:
            return None
        lease_token = str(uuid.uuid4())
        lease_expires_at = _iso(_now() + timedelta(seconds=lease_seconds))
        changed = database.execute(
            """UPDATE background_tasks
               SET state='leased',phase='claimed',attempt_count=attempt_count+1,
                   lease_token=?,lease_expires_at=?,started_at=COALESCE(started_at,?),updated_at=?
               WHERE id=? AND generation=? AND state IN ('queued','retrying')""",
            (lease_token, lease_expires_at, now, now, row["id"], row["generation"]),
        ).rowcount
        if not changed:
            return None
        _record_event(database, row["id"], row["generation"], "claimed", "claimed")
        claimed = dict(database.execute("SELECT * FROM background_tasks WHERE id=?", (row["id"],)).fetchone())
        claimed["payload"] = json.loads(claimed.pop("payload_json"))
        return claimed

    return submit_background_write(operation, label=f"claim:{kind or 'any'}")


def complete_task(task_id: str, generation: int, lease_token: str) -> bool:
    def operation(database: sqlite3.Connection) -> bool:
        now = _iso()
        changed = database.execute(
            """UPDATE background_tasks SET state='complete',phase='published',
                   lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   completed_at=?,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (now, now, task_id, generation, lease_token),
        ).rowcount
        _record_event(
            database,
            task_id,
            generation,
            "published" if changed else "stale_generation_discarded",
            "published" if changed else "discarded",
        )
        return bool(changed)

    return submit_background_write(operation, label=f"complete:{task_id}")


def fail_task(task_id: str, generation: int, lease_token: str, error: Exception) -> dict:
    sanitized_error = str(error)[:500]

    def operation(database: sqlite3.Connection) -> dict:
        row = database.execute(
            "SELECT attempt_count,max_attempts FROM background_tasks WHERE id=? AND generation=? AND lease_token=?",
            (task_id, generation, lease_token),
        ).fetchone()
        if not row:
            return {"state": "superseded"}
        terminal = row["attempt_count"] >= row["max_attempts"]
        delay = min(60.0, (2 ** max(0, row["attempt_count"] - 1)) + random.random())
        state = "failed" if terminal else "retrying"
        now = _now()
        database.execute(
            """UPDATE background_tasks SET state=?,phase=?,next_attempt_at=?,
                   lease_token=NULL,lease_expires_at=NULL,last_error=?,
                   completed_at=?,updated_at=? WHERE id=? AND generation=?""",
            (
                state,
                state,
                _iso(now + timedelta(seconds=delay)),
                sanitized_error,
                _iso(now) if terminal else None,
                _iso(now),
                task_id,
                generation,
            ),
        )
        _record_event(database, task_id, generation, state, state, sanitized_error)
        return {"state": state, "next_attempt_at": _iso(now + timedelta(seconds=delay))}

    return submit_background_write(operation, label=f"fail:{task_id}")


def retry_task(task_id: str) -> dict | None:
    def operation(database: sqlite3.Connection) -> dict | None:
        row = database.execute(
            "SELECT * FROM background_tasks WHERE id=? AND state='failed'", (task_id,)
        ).fetchone()
        if not row:
            return None
        now = _iso()
        database.execute(
            """UPDATE background_tasks SET state='queued',phase='queued',attempt_count=0,
                   next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   completed_at=NULL,updated_at=? WHERE id=?""",
            (now, now, task_id),
        )
        database.execute(
            """UPDATE background_activity SET phase='Queued',completed_units=NULL,
               total_units=NULL,updated_at=? WHERE source='durable' AND work_id=?""",
            (now, task_id),
        )
        _record_event(database, task_id, row["generation"], "manual_retry", "queued")
        return serialize_task(
            database.execute("SELECT * FROM background_tasks WHERE id=?", (task_id,)).fetchone()
        )

    return submit_foreground_write(operation, label=f"retry:{task_id}")


def requeue_interrupted_tasks() -> None:
    def operation(database: sqlite3.Connection) -> None:
        now = _iso()
        interrupted = database.execute(
            "SELECT id,generation FROM background_tasks WHERE state='leased'"
        ).fetchall()
        database.execute(
            """UPDATE background_tasks SET state='queued',phase='reclaimed',
                   lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE state='leased'""",
            (now,),
        )
        for row in interrupted:
            _record_event(database, row["id"], row["generation"], "reclaimed", "queued")

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
