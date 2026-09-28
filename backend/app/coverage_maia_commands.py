"""PostgreSQL commands for the external Maia coverage worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .services.repertoire_coverage import (
    CoverageCandidate, blend_probabilities, required_reply_moves,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease(database: PostgresConnection, payload: dict[str, Any]):
    node_id = str(payload.get("node_id", ""))
    lease_id = str(payload.get("lease_id", ""))
    if not node_id or not lease_id:
        raise HTTPException(422, "Invalid coverage lease")
    row = database.execute_native(
        "SELECT n.*,r.settings_json,r.status AS run_status FROM repertoire_coverage_nodes n "
        "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
        "WHERE n.id=%s FOR UPDATE OF n,r", (node_id,),
    ).fetchone()
    if row is None or row["maia_status"] != "leased" or row["lease_id"] != lease_id:
        raise HTTPException(409, "Coverage lease is no longer active")
    if row["run_status"] in {"failed", "building"}:
        raise HTTPException(409, "Coverage run is no longer active")
    return row


def claim_maia_node(database: PostgresConnection, _payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    row = database.execute_native(
        "SELECT n.id,n.run_id,n.fen,r.settings_json FROM repertoire_coverage_nodes n "
        "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
        "LEFT JOIN background_activity control ON control.source='coverage' "
        "AND control.work_id=n.run_id "
        "WHERE n.explorer_status='complete' AND r.status IN ('queued','running','complete') "
        "AND (n.maia_status='queued' OR (n.maia_status='leased' AND n.lease_expires_at<%s)) "
        "AND COALESCE(control.paused,0)=0 "
        "ORDER BY COALESCE(control.promoted,0) DESC,r.created_at,n.ply,n.id "
        "LIMIT 1 FOR UPDATE OF n SKIP LOCKED", (now,),
    ).fetchone()
    if row is None:
        return {"job": None}
    lease_id = str(uuid.uuid4())
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET maia_status='leased',lease_id=%s,"
        "lease_expires_at=%s,updated_at=%s WHERE id=%s",
        (lease_id, (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), now, row["id"]),
    )
    return {"job": {"node_id": row["id"], "run_id": row["run_id"],
                    "lease_id": lease_id, "fen": row["fen"],
                    "elo": json.loads(row["settings_json"])["maia_elo"]}}


def heartbeat_maia_node(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    row = _lease(database, payload)
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET lease_expires_at=%s,updated_at=%s WHERE id=%s",
        ((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), _now(), row["id"]),
    )
    return {"status": "leased"}


def release_maia_node(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    node_id = str(payload.get("node_id", ""))
    lease_id = str(payload.get("lease_id", ""))
    if not node_id or not lease_id:
        raise HTTPException(422, "Invalid coverage lease")
    changed = database.execute_native(
        "UPDATE repertoire_coverage_nodes SET maia_status='queued',lease_id=NULL,"
        "lease_expires_at=NULL,updated_at=%s "
        "WHERE id=%s AND maia_status='leased' AND lease_id=%s RETURNING id",
        (_now(), node_id, lease_id),
    ).fetchone()
    return {"status": "queued" if changed else "stale"}


def fail_maia_node(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    row = _lease(database, payload)
    error = payload.get("error")
    if not isinstance(error, str) or not error:
        raise HTTPException(422, "Invalid coverage failure report")
    message = f"Maia coverage failed: {error[:900]}"
    now = _now()
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET maia_status='failed',lease_id=NULL,"
        "lease_expires_at=NULL,last_error=%s,updated_at=%s WHERE id=%s",
        (message, now, row["id"]),
    )
    database.execute_native(
        "UPDATE repertoire_coverage_runs SET status='failed',last_error=%s,updated_at=%s "
        "WHERE id=%s", (message, now, row["run_id"]),
    )
    return {"status": "failed"}


def submit_maia_node(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    row = _lease(database, payload)
    moves = payload.get("moves")
    if not isinstance(moves, list):
        raise HTTPException(422, "Invalid Maia coverage moves")
    # The old row-by-row UPSERT accepted duplicate moves and kept the final value.
    moves = list({move["move_uci"]: move for move in moves}.values())
    covered_replies = list(json.loads(row["covered_replies_json"]))
    if moves:
        database.execute_native(
            "INSERT INTO repertoire_coverage_candidates("
            "node_id,move_uci,maia_probability,covered,source_state) "
            "SELECT %s,move_uci,probability,"
            "CASE WHEN move_uci=ANY(%s::text[]) THEN 1 ELSE 0 END,'maia-only' "
            "FROM jsonb_to_recordset(%s::jsonb) "
            "AS move(move_uci text,probability double precision) "
            "ON CONFLICT(node_id,move_uci) DO UPDATE SET "
            "maia_probability=excluded.maia_probability,covered=excluded.covered",
            (row["id"], covered_replies, json.dumps(moves)),
        )
    candidates = database.execute_native(
        "SELECT move_uci,explorer_probability,maia_probability "
        "FROM repertoire_coverage_candidates WHERE node_id=%s", (row["id"],),
    ).fetchall()
    settings = json.loads(row["settings_json"])
    blended = {
        candidate["move_uci"]: blend_probabilities(
            candidate["explorer_probability"], candidate["maia_probability"],
            explorer_games=int(row["explorer_games"])
            if candidate["explorer_probability"] is not None else 0,
        ) for candidate in candidates
    }
    total = sum(probability for probability in blended.values() if probability is not None)
    normalized = {
        move_uci: probability / total if probability is not None and total else None
        for move_uci, probability in blended.items()
    }
    required = required_reply_moves(
        [CoverageCandidate(move_uci, probability) for move_uci, probability in normalized.items()
         if probability is not None],
        denominator=int(settings["reply_denominator"]),
        cumulative_target=float(settings["cumulative_target"]),
    )
    updates = [{
        "move_uci": candidate["move_uci"],
        "blended": normalized[candidate["move_uci"]],
        "required": int(candidate["move_uci"] in required),
        "source_state": "blended" if candidate["explorer_probability"] is not None
        and candidate["maia_probability"] is not None else "explorer-only"
        if candidate["explorer_probability"] is not None else "maia-only"
        if candidate["maia_probability"] is not None else "unknown",
    } for candidate in candidates]
    if updates:
        database.execute_native(
            "UPDATE repertoire_coverage_candidates candidate SET "
            "blended_probability=updated.blended,required=updated.required,"
            "source_state=updated.source_state "
            "FROM jsonb_to_recordset(%s::jsonb) AS updated("
            "move_uci text,blended double precision,required bigint,source_state text) "
            "WHERE candidate.node_id=%s AND candidate.move_uci=updated.move_uci",
            (json.dumps(updates), row["id"]),
        )
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET maia_status='complete',lease_id=NULL,"
        "lease_expires_at=NULL,updated_at=%s WHERE id=%s", (_now(), row["id"]),
    )
    database.execute_native(
        "INSERT INTO background_activity(source,work_id,generation_key,phase,"
        "completed_units,total_units,updated_at) "
        "SELECT 'coverage',%s,%s,'Checking positions',"
        "COUNT(*) FILTER (WHERE explorer_status='complete') + "
        "COUNT(*) FILTER (WHERE maia_status='complete'),COUNT(*)*2,%s "
        "FROM repertoire_coverage_nodes WHERE run_id=%s "
        "ON CONFLICT(source,work_id) DO UPDATE SET "
        "generation_key=excluded.generation_key,phase=excluded.phase,"
        "completed_units=excluded.completed_units,total_units=excluded.total_units,"
        "updated_at=excluded.updated_at",
        (row["run_id"], row["run_id"], _now(), row["run_id"]),
    )
    priority_now = datetime.now(timezone.utc)
    database.execute_native(
        "INSERT INTO repertoire_priority_jobs(repertoire_id,generation,status,attempts,"
        "next_attempt_at,last_error,updated_at) "
        "VALUES(%s,1,'queued',0,%s,NULL,%s) "
        "ON CONFLICT(repertoire_id) DO UPDATE SET "
        "generation=repertoire_priority_jobs.generation+1,status='queued',attempts=0,"
        "next_attempt_at=excluded.next_attempt_at,last_error=NULL,updated_at=excluded.updated_at",
        (row["repertoire_id"], (priority_now + timedelta(seconds=5)).isoformat(),
         priority_now.isoformat()),
    )
    remaining = database.execute_native(
        "SELECT 1 FROM repertoire_coverage_nodes WHERE run_id=%s "
        "AND maia_status!='complete' LIMIT 1", (row["run_id"],),
    ).fetchone()
    if remaining is None:
        followup_now = datetime.now(timezone.utc)
        database.execute_native(
            "WITH queued AS ("
            "INSERT INTO background_tasks("
            "id,kind,deduplication_key,generation,priority,state,phase,payload_version,"
            "payload_json,attempt_count,max_attempts,next_attempt_at,lease_token,"
            "lease_expires_at,last_error,created_at,started_at,completed_at,updated_at) "
            "VALUES(%s,'repertoire_opportunity',%s,1,130,'queued','queued',1,%s,0,5,%s,"
            "NULL,NULL,NULL,%s,NULL,NULL,%s) "
            "ON CONFLICT(kind,deduplication_key) DO UPDATE SET "
            "generation=background_tasks.generation+1,"
            "priority=LEAST(background_tasks.priority,excluded.priority),"
            "state='queued',phase='queued',payload_version=1,payload_json=excluded.payload_json,"
            "attempt_count=0,max_attempts=5,next_attempt_at=excluded.next_attempt_at,"
            "lease_token=NULL,lease_expires_at=NULL,last_error=NULL,"
            "completed_at=NULL,updated_at=excluded.updated_at RETURNING id,generation) "
            "INSERT INTO background_task_events(task_id,generation,event,phase,detail,created_at) "
            "SELECT id,generation,'enqueued','queued',NULL,%s FROM queued",
            (str(uuid.uuid4()), row["repertoire_id"],
             json.dumps({"repertoire_id": row["repertoire_id"], "phase": "summaries", "cursor": ""}),
             (followup_now + timedelta(seconds=5)).isoformat(),
             followup_now.isoformat(), followup_now.isoformat(), followup_now.isoformat()),
        )
    return {"status": "complete"}


register_command("coverage.maia.claim", claim_maia_node)
register_command("coverage.maia.heartbeat", heartbeat_maia_node)
register_command("coverage.maia.release", release_maia_node)
register_command("coverage.maia.failure", fail_maia_node)
register_command("coverage.maia.submit", submit_maia_node)
