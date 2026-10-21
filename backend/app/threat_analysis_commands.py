"""Lease-fenced PostgreSQL callbacks for the Docker defensive engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.commands import threat_analysis as storage
from .services.engine_diagnostics import record_engine_outcome
from .services.background_metrics import increment
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .services.durable_tasks import enqueue_compact_postgres_task_in_transaction
from .services.threat_pipeline import (
    _request_from_json, report_from_json, validate_analysis_report,
)

from .services.engine_scheduling import (
    lock_engine_scheduling_state, reserved_ordinary_game, record_automated_selection,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()




def claim_threat_analysis(database: PostgresConnection, _payload: dict[str, Any]) -> dict:
    now = _now()
    setting = storage.analysis_setting(database)
    if setting is None:
        raise HTTPException(503, "Defensive analysis settings are unavailable; restore the database and retry")
    request_reader = storage.request_reader(database, setting[0])
    reclaimed = storage.reclaim_expired(database, now, now)
    if reclaimed:
        increment(database, "engine_defense", reclaimed["id"],
                  lease_expiries=1, lease_reclaims=1, generation_restarts=1)
    # Keep foreground attempts, promoted work, ordinary work, and negative
    # legacy promotion values in their existing priority order. The common
    # case can stop at the first eligible request in created order.
    row = None
    automated_selection = False
    for priority_index in range(4):
        if priority_index == 1:
            automated_streak = lock_engine_scheduling_state(database)
            if automated_streak is None:
                return {"job": None}
            preferred_game_id = reserved_ordinary_game(database, automated_streak, now)
            if preferred_game_id is not None:
                return {"job": None, "preferred_game_id": preferred_game_id}
            automated_selection = True
        if priority_index == 1 and not storage.promoted_work(database):
            continue
        row = request_reader(priority_index)
        if row is not None:
            break
    if row is None:
        return {"job": None}
    lease_id = str(uuid.uuid4())
    accepted = storage.accept_claim(database, lease_id, (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), now, row["id"])
    if accepted != 1:
        return {"job": None}
    if automated_selection:
        record_automated_selection(database)
    increment(database, "engine_defense", row["id"], claims=1)
    return {"job": {"id": row["id"], "request": json.loads(row["request_json"]),
                    "lease_id": lease_id}}


def submit_threat_report(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    request_id = str(payload["request_id"])
    lease_id = str(payload["lease_id"])
    raw_report = payload["report"]
    try:
        report = report_from_json(raw_report)
        if report.request.request_id != request_id:
            raise ValueError("Analysis report does not match its request")
        saved = storage.locked_request(database, request_id)
        if saved is None:
            raise HTTPException(404, "Analysis request not found")
        validate_analysis_report(
            _request_from_json(json.loads(saved["request_json"])), report,
        )
    except (TypeError, KeyError, ValueError) as error:
        raise HTTPException(409, str(error)) from error
    if saved["state"] == "complete":
        return {"status": "complete", "candidate_count": 0}
    if saved["state"] != "leased" or saved["lease_id"] != lease_id:
        raise HTTPException(409, "Analysis lease is stale")
    storage.complete_request(database, json.dumps(raw_report), _now(), request_id)
    record_engine_outcome(database, "engine_defense", request_id, payload, completed=True)
    candidate_ids = [row[0] for row in storage.candidate_rows(database, request_id)]
    for candidate_id in candidate_ids:
        enqueue_compact_postgres_task_in_transaction(
            database, "defensive_threat_validate", candidate_id,
            {"candidate_id": candidate_id}, priority=140,
        )
    return {"status": "complete", "candidate_count": len(candidate_ids)}


def fail_threat_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    changed = storage.fail(database, str(payload["error"]), _now(), str(payload["request_id"]), str(payload["lease_id"]))
    if changed is None:
        raise HTTPException(409, "Analysis lease is no longer active")
    record_engine_outcome(database, "engine_defense", str(payload["request_id"]), payload)
    return {"status": "retrying" if changed[0] == "queued" else changed[0]}


def release_threat_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    changed = storage.release(database, _now(), str(payload["request_id"]), str(payload["lease_id"]))
    if changed:
        record_engine_outcome(database, "engine_defense", str(payload["request_id"]), payload)
    return {"status": "queued" if changed else "stale"}


def retry_threat_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    changed = storage.retry(database, _now(), str(payload["request_id"]))
    if changed is None:
        raise HTTPException(409, "Only failed analysis can be retried")
    return {"status": "queued"}


register_command("threat.analysis.claim", claim_threat_analysis)
register_command("threat.analysis.report", submit_threat_report)
register_command("threat.analysis.failure", fail_threat_analysis)
register_command("threat.analysis.release", release_threat_analysis)
register_command("threat.analysis.retry", retry_threat_analysis)
