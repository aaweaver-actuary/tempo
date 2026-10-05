"""Lease-fenced PostgreSQL callbacks for the Docker defensive engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .services.engine_diagnostics import record_engine_outcome
from .services.background_metrics import increment
from .postgres_store import PostgresConnection
from .services.durable_tasks import enqueue_compact_postgres_task_in_transaction
from .services.threat_pipeline import (
    _request_from_json, report_from_json, validate_analysis_report,
)

from .services.defensive_analysis import search_admission_sql, recommendation_request_ids_sql


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ELIGIBLE_THREAT_REQUEST = (
    "SELECT request.id,request.request_json FROM threat_analysis_requests request "
    "LEFT JOIN background_activity control ON control.source='threat_analysis' "
    "AND control.work_id=request.id "
    "WHERE request.state='queued' AND COALESCE(control.paused,0)=0 "
    "AND " + search_admission_sql("request.id") + " "
    "AND (EXISTS(SELECT 1 FROM threat_candidate_requests relation "
    "JOIN threat_training_candidates candidate ON candidate.id=relation.candidate_id "
    "JOIN imported_games game ON game.id=candidate.game_id "
    "WHERE relation.request_id=request.id AND candidate.superseded_at IS NULL "
    "AND candidate.analysis_version=game.analysis_version "
    # OFFSET 0 keeps these as indexed per-request probes. Flattening the view
    # into a global semi-join scanned the entire restored backlog per claim.
    "AND (candidate.validation_state='needs_analysis' OR relation.role='attempt') "
    "LIMIT 1 OFFSET 0) "
    "OR EXISTS(SELECT 1 FROM discovery_recommendation_requests recommendation "
    "JOIN repertoire_opportunities opportunity ON opportunity.id=recommendation.opportunity_id "
    "WHERE recommendation.request_id=request.id AND opportunity.status='active' "
    "AND opportunity.card_id IS NULL LIMIT 1 OFFSET 0) "
    "OR EXISTS(SELECT 1 FROM coverage_discovery_recommendation_requests recommendation "
    "JOIN repertoire_opportunities opportunity ON opportunity.id=recommendation.opportunity_id "
    "WHERE recommendation.request_id=request.id AND opportunity.status='active' "
    "AND opportunity.card_id IS NULL LIMIT 1 OFFSET 0)) "
)


def claim_threat_analysis(database: PostgresConnection, _payload: dict[str, Any]) -> dict:
    now = _now()
    setting = database.execute_native("SELECT defensive_analysis_enabled FROM settings WHERE id=1").fetchone()
    if setting is None:
        raise HTTPException(503, "Defensive analysis settings are unavailable; restore the database and retry")
    recommendation_filter = ("" if setting[0] else
        "AND request.id IN (" + recommendation_request_ids_sql() + ") ")
    reclaimed = database.execute_native(
        "UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,"
        "lease_expires_at=NULL,updated_at=%s "
        "WHERE id=(SELECT id FROM threat_analysis_requests "
        "WHERE state='leased' AND lease_expires_at<=%s "
        "ORDER BY lease_expires_at,id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id",
        (now, now),
    ).fetchone()
    if reclaimed:
        increment(database, "engine_defense", reclaimed["id"],
                  lease_expiries=1, lease_reclaims=1, generation_restarts=1)
    # Keep foreground attempts, promoted work, ordinary work, and negative
    # legacy promotion values in their existing priority order. The common
    # case can stop at the first eligible request in created order.
    row = None
    for priority_filter, ordering in (
        ("AND request.id IN (SELECT request_id FROM threat_candidate_requests "
         "WHERE role='attempt') ",
         "COALESCE(control.promoted,0) DESC,request.created_at,request.id"),
        ("AND COALESCE(control.promoted,0)>0 ",
         "control.promoted DESC,request.created_at,request.id"),
        ("AND COALESCE(control.promoted,0)=0 ", "request.created_at,request.id"),
        ("AND control.promoted<0 ",
         "control.promoted DESC,request.created_at,request.id"),
    ):
        if priority_filter.startswith("AND COALESCE(control.promoted,0)>0") and not database.execute_native(
            "SELECT 1 FROM background_activity WHERE source='threat_analysis' "
            "AND promoted>0 AND paused=0 LIMIT 1",
        ).fetchone():
            continue
        row = database.execute_native(
            _ELIGIBLE_THREAT_REQUEST + recommendation_filter + priority_filter + "ORDER BY " + ordering +
            " LIMIT 1 FOR UPDATE OF request SKIP LOCKED",
        ).fetchone()
        if row is not None:
            break
    if row is None:
        return {"job": None}
    lease_id = str(uuid.uuid4())
    database.execute_native(
        "UPDATE threat_analysis_requests SET state='leased',lease_id=%s,"
        "lease_expires_at=%s,attempts=attempts+1,updated_at=%s WHERE id=%s",
        (lease_id, (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
         now, row["id"]),
    )
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
        saved = database.execute_native(
            "SELECT state,lease_id,request_json FROM threat_analysis_requests "
            "WHERE id=%s FOR UPDATE", (request_id,),
        ).fetchone()
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
    database.execute_native(
        "UPDATE threat_analysis_requests SET state='complete',report_json=%s,"
        "lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=%s WHERE id=%s",
        (json.dumps(raw_report), _now(), request_id),
    )
    record_engine_outcome(database, "engine_defense", request_id, payload, completed=True)
    candidate_ids = [row[0] for row in database.execute_native(
        "SELECT DISTINCT candidate_id FROM threat_candidate_requests WHERE request_id=%s",
        (request_id,),
    )]
    for candidate_id in candidate_ids:
        enqueue_compact_postgres_task_in_transaction(
            database, "defensive_threat_validate", candidate_id,
            {"candidate_id": candidate_id}, priority=140,
        )
    return {"status": "complete", "candidate_count": len(candidate_ids)}


def fail_threat_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    changed = database.execute_native(
        "UPDATE threat_analysis_requests SET "
        "state=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,"
        "last_error=%s,lease_id=NULL,lease_expires_at=NULL,updated_at=%s "
        "WHERE id=%s AND state='leased' AND lease_id=%s RETURNING state",
        (str(payload["error"]), _now(), str(payload["request_id"]),
         str(payload["lease_id"])),
    ).fetchone()
    if changed is None:
        raise HTTPException(409, "Analysis lease is no longer active")
    record_engine_outcome(database, "engine_defense", str(payload["request_id"]), payload)
    return {"status": "retrying" if changed[0] == "queued" else changed[0]}


def release_threat_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    changed = database.execute_native(
        "UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,"
        "attempts=GREATEST(0,attempts-1),"
        "lease_expires_at=NULL,updated_at=%s "
        "WHERE id=%s AND state='leased' AND lease_id=%s RETURNING id",
        (_now(), str(payload["request_id"]), str(payload["lease_id"])),
    ).fetchone()
    if changed:
        record_engine_outcome(database, "engine_defense", str(payload["request_id"]), payload)
    return {"status": "queued" if changed else "stale"}


def retry_threat_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    changed = database.execute_native(
        "UPDATE threat_analysis_requests SET state='queued',attempts=0,last_error=NULL,"
        "updated_at=%s WHERE id=%s AND state='failed' RETURNING id",
        (_now(), str(payload["request_id"])),
    ).fetchone()
    if changed is None:
        raise HTTPException(409, "Only failed analysis can be retried")
    return {"status": "queued"}


register_command("threat.analysis.claim", claim_threat_analysis)
register_command("threat.analysis.report", submit_threat_report)
register_command("threat.analysis.failure", fail_threat_analysis)
register_command("threat.analysis.release", release_threat_analysis)
register_command("threat.analysis.retry", retry_threat_analysis)
