"""Foreground PostgreSQL controls for durable background activity."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.commands import activity as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .services.background_activity import set_control_in_transaction
from .services.durable_tasks import serialize_task, update_queue_refresh_status_in_transaction


def control_activity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    source = payload.get("source")
    work_id = payload.get("id")
    action = payload.get("action")
    if not all(isinstance(value, str) for value in (source, work_id, action)):
        raise HTTPException(422, "Invalid activity control")
    if not set_control_in_transaction(database, source, work_id, action):
        raise HTTPException(404, "Background activity not found or cannot be controlled")
    return {"ok": True}


register_command("activity.control", control_activity)


_DISCOVERY_PARKED_AT = "9999-12-31T23:59:59+00:00"
_DISCOVERY_RETRY_REASONS = frozenset({
    "graph_prerequisite_missing", "graph_authority_busy_retry_exhausted",
    "current_integrity_missing", "current_integrity_stale", "current_integrity_busy_retry_exhausted",
    "current_integrity_pending_retry_exhausted", "published_card_unavailable",
    "discovery_target_unavailable", "queue_receipt_capacity_exceeded",
})
_DISCOVERY_PREMATERIALIZATION_REASONS = frozenset({"graph_prerequisite_missing", "graph_authority_busy_retry_exhausted"})


def _retry_retained_discovery(database, payload):
    task_id, generation = payload.get("task_id"), payload.get("generation")
    intent_id, reason = payload.get("intent_id"), payload.get("hold_reason")
    if (not isinstance(task_id, str) or not task_id or type(generation) is not int or generation < 1
            or not isinstance(intent_id, str) or not intent_id or not isinstance(reason, str)):
        raise HTTPException(422, "Exact discovery task, generation, intent and hold reason required")
    task = storage.parked_discovery_for_update(database, task_id)
    if task is None:
        raise HTTPException(409, "Discovery task is missing, busy or no longer parked")
    try:
        saved_payload = json.loads(task["payload_json"])
    except (TypeError, ValueError):
        raise HTTPException(409, "Discovery task identity is invalid")
    if (task["generation"] != generation or task["deduplication_key"] != intent_id
            or saved_payload != {"intent_id": intent_id} or task["next_attempt_at"] != _DISCOVERY_PARKED_AT):
        raise HTTPException(409, "Discovery task identity or parked state changed")
    intent = storage.parked_intent_for_update(database, intent_id)
    if (not intent or intent["id"] != intent_id or intent["state"] != "preparing"
            or intent["last_error"] != reason):
        raise HTTPException(409, "Discovery intent or hold reason changed")
    if reason == "sqlite_current_authority_unavailable":
        raise HTTPException(409, "SQLite discovery authority is unavailable; owner disposition required")
    if reason not in _DISCOVERY_RETRY_REASONS:
        raise HTTPException(409, "Discovery hold is not eligible for owner retry")
    from .services.postgres_integrity import try_inspect_retained_integrity
    authority = try_inspect_retained_integrity(database, intent["repertoire_id"])
    if authority.state != "current":
        raise HTTPException(409, {"code": "discovery_authority_" + authority.state})
    if reason not in _DISCOVERY_PREMATERIALIZATION_REASONS:
        from .services.discovery_admission import _admission_witness_matches
        from tempo_legacy_database.adapters.authored_repertoires.discovery_admission import storage as discovery_storage
        step = discovery_storage.published_line_card(database, (intent["repertoire_id"], intent["line_id"]))
        card = discovery_storage.try_lock_admission_card(database, step["card_id"]) if step else None
        line = discovery_storage.locked_source_line(database, intent["line_id"]) if card else None
        if (not _admission_witness_matches(intent, line, step, card)
                or card["archived"] or card["pending_validation"]
                or discovery_storage.deleted_card(database, card["id"])
                or discovery_storage.card_integrity_block(database, (intent["repertoire_id"], card["id"]))):
            raise HTTPException(409, "Discovery target is missing, busy or no longer current")
    if reason == "queue_receipt_capacity_exceeded":
        from tempo_legacy_database.adapters.queue_position_lock import discovery_queue_exceeds_bound
        from datetime import date
        if discovery_queue_exceeds_bound(database, date.today().isoformat(), 4095):
            raise HTTPException(409, "Discovery queue receipt remains over capacity")
    now = datetime.now(timezone.utc).isoformat()
    if storage.wake_parked_discovery(database, task_id, generation, intent_id, _DISCOVERY_PARKED_AT, now) != 1:
        raise HTTPException(409, "Discovery task changed before retry")
    return serialize_task(storage.record_manual_retry(database, task_id, generation, now))


def retry_failed_task(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    if any(key in payload for key in ("generation", "intent_id", "hold_reason")):
        return _retry_retained_discovery(database, payload)
    task_id = str(payload["task_id"])
    failed = storage.failed_task_for_update(database, task_id)
    if failed is None:
        raise HTTPException(404, "Terminal task not found")
    from .contracts.command_registry import RETIRED_TASK_KINDS
    if failed["kind"] in RETIRED_TASK_KINDS:
        raise HTTPException(410, "Task retired")
    now = datetime.now(timezone.utc).isoformat()
    storage.reset_failed_task(database, task_id, now)
    if failed["kind"] == "daily_queue":
        from .services.queue_refresh_wakeup import mark_queue_refresh_requested
        mark_queue_refresh_requested()
        update_queue_refresh_status_in_transaction(
            database, failed["kind"], json.loads(failed["payload_json"]), state="refreshing",
        )
    storage.record_retry_activity(database, task_id, now)
    retried = storage.record_manual_retry(database, task_id, failed["generation"], now)
    return serialize_task(retried)


register_command("activity.task.retry", retry_failed_task)
