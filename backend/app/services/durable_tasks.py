"""Embedded durable task queue and sanitized operational projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import random
import sqlite3
import uuid
from typing import Callable

from tempo_legacy_database.connections import read_connection, background_read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.durable_tasks import storage
from tempo_legacy_database.adapters.durable_tasks.storage import (
    _EVENT_INSERT_SQL, _EVENT_PRUNE_SQL, _TASK_BY_KIND_SQL, _TASK_UPSERT_SQL,
    _TASK_BY_ID_SQL, _SELECTED_REBUILD_UPSERT_FENCE, _COMPLETE_SLICE_SQL,
    _QUEUE_REFRESH_STATUS_SQL, _QUEUE_REFRESH_PROGRESS_SQL, warm_completion_sql,
)
from tempo_legacy_database.adapters.database_executor import submit_background_write, submit_foreground_write
from .background_activity import claimable, control_order
from .defensive_analysis import DEFENSIVE_TASK_KINDS, analysis_enabled, task_admission_sql
from .background_metrics import increment, record_event_counts
from .queue_refresh_wakeup import mark_queue_refresh_requested, wake_queue_refresh
from .activity_gate import activity_gate
from .durable_scheduling import warm_scheduling_sql, select_scheduled_task, persist_scheduling_turn


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
    *,
    kind: str | None = None,
) -> None:
    storage.insert_event(database, (task_id, generation, event, phase, detail, _iso()))
    record_event_counts(database, task_id, event, kind=kind)
    storage.prune_events(database, (task_id,))


def update_queue_refresh_status_in_transaction(
    database, kind: str, payload: dict, *, state: str, error: str | None = None,
) -> None:
    """Mirror an accepted task transition while its row lock still fences publication."""

    if kind != "daily_queue" or not payload.get("queue_date"):
        return
    storage.update_queue_status(database, (state, int(state == "refreshing"), error, payload["queue_date"]))


def clear_queue_refresh_error_in_transaction(
    database, task_id: str, *, kind: str | None, payload: dict | None = None,
) -> None:
    """Committed progress clears its error without declaring the queue ready."""

    if kind not in {None, "daily_queue"}:
        return
    if payload is None:
        task_row = storage.task_by_id(database, (task_id,))
        if task_row is None or task_row["kind"] != "daily_queue":
            return
        payload = json.loads(task_row["payload_json"])
    if payload.get("queue_date"):
        storage.clear_queue_error(database, (payload["queue_date"],))


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


def ensure_task_in_transaction(database, kind: str, deduplication_key: str, payload: dict, *, priority: int) -> None:
    """Keep a live requested task's generation/lease intact on alias requests."""
    existing = storage.task_by_kind(database, (kind, deduplication_key))
    if existing is None or existing["state"] in {"failed", "complete"}:
        enqueue_task_in_transaction(database, kind, deduplication_key, payload, priority=priority)


def task_state(kind: str, deduplication_key: str) -> str | None:
    reader = background_read_connection if activity_gate.in_background else read_connection
    with reader() as database:
        row = storage.task_by_kind(database, (kind, deduplication_key))
    return row["state"] if row else None


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

    existing = storage.task_by_kind(database, (kind, deduplication_key))
    handoff = getattr(database, '_selected_rebuild_handoff', None)
    selected = isinstance(handoff, dict) and kind == handoff['kind']
    write_task = storage.prepare_task_upsert()
    if selected:
        from .opening_graph_recovery import validate_selected_enqueue
        validate_selected_enqueue(handoff, kind, deduplication_key, existing, payload)
        prior = handoff['pin']['prior']
        write_task = storage.prepare_task_upsert(prior, selected=True)

    now = _now()
    task_id = existing["id"] if existing else str(uuid.uuid4())
    generation = max(
        int(existing["generation"]) + 1 if existing else 1,
        minimum_generation + 1,
    )
    next_attempt_at = _iso(now + timedelta(seconds=delay_seconds))
    created_at = existing["created_at"] if existing else _iso(now)
    authoritative = write_task(
        database,
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
    if selected:
        validate_selected_enqueue(handoff, kind, deduplication_key, authoritative, payload, after=True)
    task_id = authoritative["id"]
    generation = authoritative["generation"]
    _record_event(database, task_id, generation, "enqueued", "queued", kind=kind)
    queued_row = storage.task_by_id(database, (task_id,))
    if queued_row["replaced_pending_generation"]:
        _record_event(database, task_id, generation, "generation_replaced", "queued", kind=kind)
    if kind == "daily_queue":
        mark_queue_refresh_requested()
    return dict(queued_row)


def enqueue_compact_postgres_task_in_transaction(
    database, kind: str, deduplication_key: str, payload: dict,
    *, priority: int, delay_seconds: float = 0,
) -> None:
    """Checkpoint a follow-up and its event in one bounded PostgreSQL statement."""

    from datetime import timedelta

    now = _now()
    storage.enqueue_compact_followup(
        database,
        (str(uuid.uuid4()), kind, deduplication_key, priority,
         json.dumps(payload, separators=(",", ":")),
         _iso(now + timedelta(seconds=delay_seconds)),
         _iso(now), _iso(now), _iso(now)),
    )
    queued = storage.task_by_kind(database, (kind, deduplication_key))
    increment(database, kind, queued["id"], generations_started=1,
              generation_replacements=int(queued["replaced_pending_generation"]))
    storage.prune_events(database, (queued["id"],))
    if kind == "daily_queue":
        mark_queue_refresh_requested()


def claim_task(
    kind: str | None = None, *, allowed_kinds: tuple[str, ...] | None = None,
    lease_seconds: int = 60, explicit_imports_only: bool = False,
) -> dict | None:
    if explicit_imports_only and (kind is not None or allowed_kinds is not None):
        raise ValueError("Explicit import claims use only their receipt-bound scope")
    if kind is not None and allowed_kinds is not None:
        raise ValueError("Use one durable-task kind filter")
    if allowed_kinds is not None and not allowed_kinds:
        raise ValueError("Allowed durable-task kinds cannot be empty")
    if allowed_kinds is not None:
        warm_scheduling_sql(allowed_kinds)

    prepared_import_claim = (storage.prepare_task_claim(None,None,_iso(),DEFENSIVE_TASK_KINDS,explicit_imports_only=True)
                             if explicit_imports_only else None)

    def operation(database: sqlite3.Connection) -> dict | None:
        now = _iso()
        reclaim, read_candidate, kind_parameters = prepared_import_claim or storage.prepare_task_claim(kind,allowed_kinds,now,DEFENSIVE_TASK_KINDS)




        reclaimed = reclaim(database)
        # Reuse bounded aggregate shards rather than add one query/write per expired lease.
        from collections import Counter
        from .background_metrics import metric_shard
        reclaimed_groups = Counter((task["kind"],metric_shard(task["id"])) for task in reclaimed)
        for (reclaimed_kind, shard), reclaimed_count in reclaimed_groups.items():
            increment(database, reclaimed_kind, "", shard_override=shard,
                      lease_expiries=reclaimed_count, lease_reclaims=reclaimed_count,
                      generation_restarts=reclaimed_count)
        parameters = [now, *kind_parameters]
        scheduling_turn = None
        if allowed_kinds is not None:
            row, scheduling_turn = select_scheduled_task(database, allowed_kinds, now)
        else:
            row = read_candidate(database, parameters)
        if not row:
            return None
        lease_token = str(uuid.uuid4())
        lease_expires_at = _iso(_now() + timedelta(seconds=lease_seconds))
        changed = storage.claim_lease(database, (lease_token, lease_expires_at, now, now, row["id"], row["generation"]))
        if not changed:
            return None
        if scheduling_turn is not None:
            persist_scheduling_turn(database, scheduling_turn)
        _record_event(database, row["id"], row["generation"], "claimed", "claimed", kind=row["kind"])
        claimed = dict(storage.read_task(database, (row["id"],)))
        claimed["payload"] = json.loads(claimed.pop("payload_json"))
        return claimed

    return submit_background_write(
        operation,
        label=f"claim:{kind or (','.join(allowed_kinds) if allowed_kinds else 'any')}",
    )


def complete_task(task_id: str, generation: int, lease_token: str, *, kind: str | None = None) -> bool:
    def operation(database: sqlite3.Connection) -> bool:
        now = _iso()
        changed = storage.complete_task(database, (now, now, task_id, generation, lease_token))
        if changed:
            clear_queue_refresh_error_in_transaction(database, task_id, kind=kind)
            _record_event(database, task_id, generation, "published", "published", kind=kind)
        else:
            _record_stale_result(database, task_id, generation, kind or "other")
        return bool(changed)

    return submit_background_write(operation, label=f"complete:{task_id}")


def lock_current_slice(database, task: dict) -> bool:
    """Lock a claimed PostgreSQL task before publishing a slice's effects."""

    row = storage.lock_slice(database, (task["id"],))
    current = bool(
        row and row["generation"] == task["generation"]
        and row["lease_token"] == task["lease_token"] and row["state"] == "leased"
    )
    if not current:
        increment(database, task.get("kind", "other"), task["id"], stale_results=1)
    elif task.get('explicit_import_preparation'):
        from .explicit_repertoire_import import require_delivery
        require_delivery(database,task)
    elif 'maintenance_scope' in task:
        # Only explicit selected-rebuild deliveries carry this process-local pin.
        # The check and source/policy locks belong inside the owner's transaction.
        from .opening_graph_recovery import validate_selected_delivery
        validate_selected_delivery(database, task)
    return current


def advance_task_slice_in_transaction(
    database, task: dict, *, next_phase: str, next_payload: dict, eligible_at: str | None = None,
) -> bool:
    """Commit slice effects and restart state together in the caller's transaction."""

    now = _iso()
    changed = storage.advance_slice(
        database,
        (next_phase, json.dumps(next_payload, separators=(",", ":")), eligible_at or now, now,
         task["id"], task["generation"], task["lease_token"]),
    )
    if changed:
        clear_queue_refresh_error_in_transaction(database, task["id"], kind=task.get("kind"),
                                                payload=task.get("payload"))
        _record_event(database, task["id"], task["generation"], "slice_complete", next_phase, kind=task.get("kind"))
        if 'maintenance_scope' in task:
            from .opening_graph_recovery import validate_selected_delivery
            # Generated provenance is excluded by the existing source triggers.
            # Any authoritative source increment must roll this whole slice back.
            validate_selected_delivery(database, task, after_checkpoint=True)
    return bool(changed)


def complete_task_slice_in_transaction(database, task: dict) -> bool:
    """Complete a PostgreSQL task in the same transaction as its final publication."""

    now = _iso()
    changed = storage.complete_slice(database, (now, now, task["id"], task["generation"], task["lease_token"]))
    if changed:
        clear_queue_refresh_error_in_transaction(database, task["id"], kind=task.get("kind"),
                                                payload=task.get("payload"))
        _record_event(database, task["id"], task["generation"], "published", "published", kind=task.get("kind"))
        if 'maintenance_scope' in task:
            from .opening_graph_recovery import validate_selected_delivery
            validate_selected_delivery(database, task, after_checkpoint=True)
    else:
        _record_stale_result(database, task["id"], task["generation"], task.get("kind", "other"))
    return bool(changed)


def fail_task(task_id: str, generation: int, lease_token: str, error: Exception) -> dict:
    sanitized_error = str(error)[:500]

    def operation(database: sqlite3.Connection) -> dict:
        if storage.configured():
            from .prefix_transition_application import lock_linked_receipts
            lock_linked_receipts(database, task_id, generation)
        row = storage.failure_budget(database, (task_id, generation, lease_token))
        if not row:
            return {"state": "superseded"}
        terminal = row["attempt_count"] >= row["max_attempts"]
        delay = min(60.0, (2 ** max(0, row["attempt_count"] - 1)) + random.random())
        state = "failed" if terminal else "retrying"
        now = _now()
        write_failure = storage.prepare_task_failure()
        changed = write_failure(
            database,
            (
                state,
                *storage.failure_state_parameters(state),
                _iso(now + timedelta(seconds=delay)),
                sanitized_error,
                _iso(now) if terminal else None,
                _iso(now),
                task_id,
                generation,
                lease_token,
            ),
        )
        if not changed:
            return {"state": "superseded"}
        update_queue_refresh_status_in_transaction(
            database, row["kind"], json.loads(row["payload_json"]),
            state="failed" if terminal else "refreshing", error=sanitized_error,
        )
        if terminal and row["kind"] in {"game_sync_window", "game_sync_record"}:
            sync_payload = json.loads(row["payload_json"])
            job_id = sync_payload["job_id"]
            storage.fail_sync_job(database, (sanitized_error, _iso(now), _iso(now), job_id))
            if row["kind"] == "game_sync_window":
                provider_row = storage.sync_window_provider(database, (sync_payload["window_id"],))
                provider = provider_row["provider"] if provider_row else None
            else:
                provider = sync_payload["record"]["provider"]
            if provider:
                storage.fail_sync_provider(database, (sanitized_error, provider))
        if row["kind"] == "integrity_scan":
            integrity_payload = json.loads(row["payload_json"])
            storage.update_integrity_failure(
                database,
                ("failed" if terminal else "retrying", sanitized_error,
                 integrity_payload["repertoire_id"], f"{task_id}:{generation}"),
            )
        if terminal and row["kind"] in {"coverage_seed", "coverage_explorer"}:
            coverage_payload = json.loads(row["payload_json"])
            storage.fail_coverage_run(database, (sanitized_error, _iso(now), coverage_payload["run_id"]))
        if terminal and row["kind"] == "game_analysis_publish":
            publication_payload = json.loads(row["payload_json"])
            game_id = publication_payload["game_id"]
            storage.fail_analysis_publication(database, (sanitized_error, _iso(now), game_id))
            storage.fail_analysis_job(database, (sanitized_error, _iso(now), game_id))
            storage.fail_imported_game(database, (game_id,))
        if terminal and postgres_store.configured():
            from .prefix_transition_application import record_linked_failure
            record_linked_failure(database, task_id, generation, sanitized_error)
        _record_event(database, task_id, generation, state, state, sanitized_error, kind=row["kind"])
        return {"state": state, "next_attempt_at": _iso(now + timedelta(seconds=delay))}

    with activity_gate.background_control():
        return submit_background_write(operation, label=f"fail:{task_id}")


def defer_task_for_foreground(task: dict) -> bool:
    """Release a raced claim without changing its checkpoint or failure budget."""
    def operation(database):
        changed = storage.defer_for_foreground(
            database,
            (_iso(_now() + timedelta(seconds=1)), _iso(),
             task['id'], task['generation'], task['lease_token']),
        )
        if changed:
            _record_event(database, task['id'], task['generation'], 'foreground_deferred',
                          task.get('phase'), kind=task['kind'])
        return bool(changed)

    with activity_gate.background_control():
        return submit_background_write(operation, label=f"foreground-defer:{task['id']}")


def defer_task_for_contention(task_id: str, generation: int, lease_token: str, *, kind: str | None = None) -> bool:
    """Yield an expected PostgreSQL lock timeout without spending a retry."""

    def operation(database: sqlite3.Connection) -> bool:
        next_attempt_at = _iso(_now() + timedelta(milliseconds=250))
        write_contention = storage.prepare_contention_defer()
        changed = write_contention(database, (next_attempt_at, _iso(), task_id, generation, lease_token))
        if changed:
            _record_event(database, task_id, generation, "yielded", "yielded", kind=kind)
        return bool(changed)

    with activity_gate.background_control():
        return submit_background_write(operation, label=f"yield:{task_id}")


def defer_task_for_connection_failure(task: dict, *, wake: Callable[[datetime], None] | None = None) -> bool:
    """Reconcile an unchanged claim on a fresh writer after connection loss."""
    eligible_at = _now() + timedelta(seconds=1)
    def operation(database):
        changed = storage.defer_for_connection_failure(database, (
            _iso(eligible_at), _iso(), task['id'], task['generation'], task['lease_token']))
        if changed:
            _record_event(database, task['id'], task['generation'], 'yielded',
                          task.get('phase'), 'database_connection_unavailable', kind=task['kind'])
        return bool(changed)
    with activity_gate.background_control():
        changed = submit_background_write(operation, label=f"connection-recovery:{task['id']}")
    if changed and wake is not None:
        wake(eligible_at)
    return changed


def defer_task_for_transaction_timeout(task: dict, error: Exception, *, wake: Callable[[datetime], None] | None = None) -> bool:
    """Preserve a checkpoint and cool repeated deadline failures down to 60 s.

    A transaction deadline is not evidence of a competing lock. Keep its error
    visible and its episode counter durable without exhausting ordinary retries.
    """
    def operation(database):
        row = storage.timeout_checkpoint(database, (task['id'], task['generation'], task['lease_token']))
        if row is None:
            return None
        saved_payload = json.loads(row['payload_json'])
        checkpoint = json.dumps([task['generation'], row['phase'], saved_payload], sort_keys=True)
        # Upgrade an existing episode in place; old rows lack the generation
        # prefix, and a process restart must not discard their current backoff.
        legacy_checkpoint = json.dumps([row['phase'], saved_payload], sort_keys=True)
        timeout_count = (int(row['transaction_timeout_count']) + 1
                         if row['transaction_timeout_checkpoint'] in {checkpoint, legacy_checkpoint} else 1)
        delay_seconds = min(60, 2 ** min(timeout_count - 1, 6))
        now = _now()
        next_attempt_at = now + timedelta(seconds=delay_seconds)
        sanitized_error = str(error)[:500]
        changed = storage.defer_for_timeout(
            database,
            (timeout_count, checkpoint, _iso(next_attempt_at),
             sanitized_error, _iso(now), task['id'], task['generation'], task['lease_token']),
        )
        if changed:
            update_queue_refresh_status_in_transaction(
                database, task['kind'], saved_payload,
                state="refreshing", error=sanitized_error,
            )
            _record_event(database, task['id'], task['generation'], 'yielded', row['phase'],
                          sanitized_error, kind=task['kind'])
        return next_attempt_at if changed else None

    with activity_gate.background_control():
        committed_retry_at = submit_background_write(operation, label=f"deadline:{task['id']}")
    # The write boundary has committed and released its PostgreSQL connection.
    # ETA is only a capacity hint; execution-time claims still enforce eligibility.
    if committed_retry_at is not None and task['kind'] == 'daily_queue':
        wake_queue_refresh(eligible_at=committed_retry_at)
    if committed_retry_at is not None and wake is not None:
        wake(committed_retry_at)
    return committed_retry_at is not None


def retry_task(task_id: str) -> dict | None:
    def operation(database: sqlite3.Connection) -> dict | None:
        row = storage.failed_task(database, (task_id,))
        if not row:
            return None
        now = _iso()
        write_retry = storage.prepare_manual_retry()
        write_retry(database, (now, now, task_id))
        update_queue_refresh_status_in_transaction(
            database, row["kind"], json.loads(row["payload_json"]), state="refreshing",
        )
        storage.reset_activity_progress(database, (now, task_id))
        _record_event(database, task_id, row["generation"], "manual_retry", "queued", kind=row["kind"])
        return serialize_task(
            storage.read_task(database, (task_id,))
        )

    return submit_foreground_write(operation, label=f"retry:{task_id}")


def requeue_interrupted_tasks() -> None:
    def operation(database: sqlite3.Connection) -> None:
        now = _iso()
        interrupted = storage.interrupted_tasks(database)
        write_interrupted = storage.prepare_interrupted_requeue()
        write_interrupted(database, (now,))
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
        rows = storage.visible_tasks(database)
    return [serialize_task(row) for row in rows]


def defer_paused_defensive_task(task: dict) -> bool:
    """Return one pre-dispatched slice without consuming its failure budget or waking retries."""
    if task['kind'] not in DEFENSIVE_TASK_KINDS:
        return False

    def operation(database):
        if analysis_enabled(database):
            return False
        storage.defer_disabled_defensive_task(database, (_iso(), task['id'], task['generation'], task['lease_token']))
        return True

    return submit_background_write(operation, label='pause:defensive-slice')


def current_delivery(task: dict) -> bool:
    """Admitted primary read; transactional publication fences remain authoritative."""
    with background_read_connection(authoritative=True) as database:
        row = storage.delivery_lease(database, (task["id"],))
    return bool(row and row["generation"] == task["generation"]
                and row["lease_token"] == task["lease_token"] and row["state"] == "leased")


def record_stale_delivery(task: dict) -> None:
    def operation(database):
        exists = storage.task_exists(database, (task["id"],))
        if exists:
            _record_event(database, task["id"], task["generation"], "stale_delivery", "discarded", kind=task["kind"])
        else:
            increment(database, task["kind"], task["id"], stale_deliveries=1)
    submit_background_write(operation, label="diagnostics:stale-delivery")


def _record_stale_result(database, task_id: str, generation: int, kind: str = "other") -> None:
    """A removed task has no raw-event foreign key, but its rejected result still counts."""
    row = storage.task_kind(database, (task_id,))
    if row:
        _record_event(database, task_id, generation, "stale_generation_discarded", "discarded", kind=row["kind"])
    else:
        increment(database, kind, task_id, stale_results=1)
