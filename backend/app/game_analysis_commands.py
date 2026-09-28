"""Leased game-analysis claims executed by the background Celery worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .database import background_read_connection
from .postgres_store import PostgresConnection
from .services.game_analysis_worker import _confirmed_indices, _positions
from .services.threat_pipeline import ENGINE_VERSION, NETWORK_VERSION
from .services.threat_pipeline import report_from_json, validate_analysis_report
from .services.threat_validation import AnalysisRequest


def claim_game_analysis(database: PostgresConnection, _payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    job = database.execute_native(
        "SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,"
        "g.provider,g.username,g.played_at,g.color,g.start_fen,g.moves_json,"
        "c.divergence_ply "
        "FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
        "LEFT JOIN repertoire_comparisons c ON c.game_id=g.id "
        "LEFT JOIN background_activity control ON control.source='game_analysis' "
        "AND control.work_id=j.game_id "
        "WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<%s)) "
        "AND g.rated=1 AND g.speed IN ('blitz','rapid','classical') "
        "AND COALESCE(control.paused,0)=0 "
        "ORDER BY COALESCE(control.promoted,0) DESC,g.played_at DESC,j.game_id "
        "LIMIT 1 FOR UPDATE OF j SKIP LOCKED",
        (now_text,),
    ).fetchone()
    if job is None:
        return {"job": None}
    lease_id = str(uuid.uuid4())
    expires_at = (now + timedelta(minutes=5)).isoformat()
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='leased',lease_id=%s,lease_expires_at=%s,"
        "attempts=attempts+1,updated_at=%s WHERE game_id=%s",
        (lease_id, expires_at, now_text, job["game_id"]),
    )
    database.execute_native(
        "UPDATE imported_games SET analysis_state='analyzing' WHERE id=%s",
        (job["game_id"],),
    )
    return {"job": {**dict(job), "moves": json.loads(job["moves_json"]),
                    "lease_id": lease_id, "lease_expires_at": expires_at}}


register_command("games.analysis.claim", claim_game_analysis)


def prepare_position_claim() -> dict[str, Any] | None:
    """Read a candidate, then traverse its chess history after closing PostgreSQL."""

    with background_read_connection() as database:
        candidate = database.execute_native(
            "SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,"
            "g.start_fen,g.moves_json,c.divergence_ply "
            "FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
            "LEFT JOIN repertoire_comparisons c ON c.game_id=g.id "
            "LEFT JOIN background_activity control ON control.source='game_analysis' "
            "AND control.work_id=j.game_id "
            "WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<%s)) "
            "AND g.rated=1 AND g.speed IN ('blitz','rapid','classical') "
            "AND COALESCE(control.paused,0)=0 "
            "ORDER BY COALESCE(control.promoted,0) DESC,g.played_at DESC,j.game_id LIMIT 1",
            (datetime.now(timezone.utc).isoformat(),),
        ).fetchone()
        if candidate is None:
            return None
        saved_reports = [dict(row) for row in database.execute_native(
            "SELECT scan_pass,position_index,report_json,state FROM game_analysis_position_reports "
            "WHERE game_id=%s AND analysis_version=%s",
            (candidate["game_id"], candidate["analysis_version"]),
        )]
    moves = json.loads(candidate["moves_json"])
    positions = _positions(candidate["start_fen"], moves)
    completed = {(row["scan_pass"], row["position_index"]): json.loads(row["report_json"])
                 for row in saved_reports if row["state"] == "complete" and row["report_json"]}
    shallow = {index: completed[("shallow", index)] for index in range(len(positions))
               if ("shallow", index) in completed}
    next_index = next((index for index in range(len(positions)) if index not in shallow), None)
    scan_pass = "shallow"
    if next_index is None:
        confirmed = _confirmed_indices(positions, moves, shallow, candidate["divergence_ply"])
        next_index = next((index for index in sorted(confirmed)
                           if ("confirmed", index) not in completed), None)
        scan_pass = "confirmed"
    plan: dict[str, Any] = {
        "game_id": candidate["game_id"], "analysis_version": candidate["analysis_version"],
        "analysis_evidence_version": candidate["analysis_evidence_version"],
        "scan_pass": scan_pass, "position_index": next_index,
    }
    if next_index is None:
        plan["kind"] = "finalize"
        return plan
    board = positions[next_index]
    if board.is_game_over(claim_draw=True):
        plan.update(kind="terminal", terminal="checkmate" if board.is_checkmate() else "draw")
        return plan
    request = AnalysisRequest(
        position_start_fen=candidate["start_fen"],
        position_prefix_uci=tuple(moves[:next_index]),
        engine_version=ENGINE_VERSION, network_version=NETWORK_VERSION,
        depth=8 if scan_pass == "shallow" else 14, multipv=5,
    )
    plan.update(kind="search", request=asdict(request))
    return plan


def claim_game_analysis_position(database: PostgresConnection, plan: dict[str, Any]) -> dict[str, Any]:
    """Validate the prepared generation and lease one position atomically."""

    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    parent = database.execute_native(
        "SELECT j.analysis_version,j.analysis_evidence_version,j.status,j.lease_expires_at,"
        "g.rated,g.speed FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
        "WHERE j.game_id=%s FOR UPDATE OF j",
        (plan["game_id"],),
    ).fetchone()
    if (parent is None or parent["analysis_version"] != plan["analysis_version"]
            or parent["analysis_evidence_version"] != plan["analysis_evidence_version"]
            or not parent["rated"] or parent["speed"] not in {"blitz", "rapid", "classical"}
            or not (parent["status"] == "queued" or
                    parent["status"] == "leased" and parent["lease_expires_at"] is not None
                    and parent["lease_expires_at"] < now_text)):
        return {"job": None}
    control = database.execute_native(
        "SELECT paused FROM background_activity WHERE source='game_analysis' AND work_id=%s",
        (plan["game_id"],),
    ).fetchone()
    if control is not None and control["paused"]:
        return {"job": None}
    position_index = plan["position_index"]
    existing = None
    if position_index is not None:
        existing = database.execute_native(
            "SELECT id,state FROM game_analysis_position_reports "
            "WHERE game_id=%s AND analysis_version=%s AND scan_pass=%s AND position_index=%s FOR UPDATE",
            (plan["game_id"], plan["analysis_version"], plan["scan_pass"], position_index),
        ).fetchone()
        if existing is not None and existing["state"] == "failed":
            database.execute_native(
                "UPDATE game_analysis_jobs SET status='failed',lease_id=NULL,lease_expires_at=NULL,"
                "last_error='A game position exhausted its engine retries',updated_at=%s "
                "WHERE game_id=%s", (now_text, plan["game_id"]),
            )
            database.execute_native(
                "UPDATE imported_games SET analysis_state='failed' WHERE id=%s",
                (plan["game_id"],),
            )
            return {"job": None}
        if existing is not None and existing["state"] == "complete":
            return {"job": None}
    parent_lease_id = str(uuid.uuid4())
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='leased',lease_id=%s,lease_expires_at=%s,"
        "attempts=attempts+1,updated_at=%s WHERE game_id=%s",
        (parent_lease_id, (now + timedelta(minutes=5)).isoformat(), now_text, plan["game_id"]),
    )
    database.execute_native(
        "UPDATE imported_games SET analysis_state='analyzing' WHERE id=%s", (plan["game_id"],),
    )
    if plan["kind"] == "finalize":
        return {"job": {"kind": "finalize", "game_id": plan["game_id"],
                        "lease_id": parent_lease_id}}
    if plan["kind"] == "terminal":
        database.execute_native(
            "INSERT INTO game_analysis_position_reports("
            "id,game_id,analysis_version,scan_pass,position_index,request_json,report_json,state,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,'{}',%s,'complete',%s) "
            "ON CONFLICT(game_id,analysis_version,scan_pass,position_index) DO NOTHING",
            (str(uuid.uuid4()), plan["game_id"], plan["analysis_version"], plan["scan_pass"],
             position_index, json.dumps({"terminal": plan["terminal"]}), now_text),
        )
        database.execute_native(
            "UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,"
            "updated_at=%s WHERE game_id=%s", (now_text, plan["game_id"]),
        )
        database.execute_native(
            "UPDATE imported_games SET analysis_state='pending' WHERE id=%s", (plan["game_id"],),
        )
        return {"job": None}
    position_lease_id = str(uuid.uuid4())
    report_id = existing["id"] if existing else str(uuid.uuid4())
    request_json = json.dumps(plan["request"], sort_keys=True)
    expires_at = (now + timedelta(minutes=2)).isoformat()
    if existing:
        database.execute_native(
            "UPDATE game_analysis_position_reports SET state='leased',lease_id=%s,"
            "parent_lease_id=%s,lease_expires_at=%s,request_json=%s,attempts=attempts+1,"
            "updated_at=%s WHERE id=%s",
            (position_lease_id, parent_lease_id, expires_at, request_json, now_text, report_id),
        )
    else:
        database.execute_native(
            "INSERT INTO game_analysis_position_reports("
            "id,game_id,analysis_version,scan_pass,position_index,request_json,state,"
            "lease_id,parent_lease_id,lease_expires_at,attempts,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,'leased',%s,%s,%s,1,%s)",
            (report_id, plan["game_id"], plan["analysis_version"], plan["scan_pass"],
             position_index, request_json, position_lease_id, parent_lease_id, expires_at, now_text),
        )
    return {"job": {"kind": "search", "id": report_id, "game_id": plan["game_id"],
                    "position_index": position_index, "scan_pass": plan["scan_pass"],
                    "lease_id": position_lease_id, "request": plan["request"]}}


register_command("games.analysis.position.claim", claim_game_analysis_position)


def prepare_position_report(report_id: str, raw_report: dict[str, Any]) -> str:
    with background_read_connection() as database:
        row = database.execute_native(
            "SELECT request_json FROM game_analysis_position_reports WHERE id=%s", (report_id,),
        ).fetchone()
    if row is None:
        raise KeyError("Game position request not found")
    request_json = row["request_json"]
    request_data = json.loads(request_json)
    request = AnalysisRequest(**{
        **request_data, "position_prefix_uci": tuple(request_data["position_prefix_uci"]),
    })
    validate_analysis_report(request, report_from_json(raw_report))
    return request_json


def publish_position_report(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    report_id = str(payload["report_id"])
    lease_id = str(payload["lease_id"])
    raw_report = payload["report"]
    stored = database.execute_native(
        "SELECT game_id,request_json,report_json,state,lease_id,parent_lease_id "
        "FROM game_analysis_position_reports WHERE id=%s FOR UPDATE", (report_id,),
    ).fetchone()
    report_json = json.dumps(raw_report)
    if stored is None:
        raise HTTPException(422, "Game position request not found")
    if stored["state"] == "complete" and stored["report_json"] == report_json:
        return {"status": "complete"}
    if (stored["state"] != "leased" or stored["lease_id"] != lease_id
            or stored["request_json"] != payload["request_json"]):
        raise HTTPException(422, "Game position lease is stale")
    now = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "UPDATE game_analysis_position_reports SET state='complete',report_json=%s,"
        "lease_id=NULL,parent_lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=%s "
        "WHERE id=%s", (report_json, now, report_id),
    )
    released = database.execute_native(
        "UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,"
        "last_error=NULL,updated_at=%s WHERE game_id=%s AND status='leased' AND lease_id=%s "
        "RETURNING game_id", (now, stored["game_id"], stored["parent_lease_id"]),
    ).fetchone()
    if released is not None:
        database.execute_native(
            "UPDATE imported_games SET analysis_state='pending' WHERE id=%s", (stored["game_id"],),
        )
    return {"status": "complete"}


def release_position_report(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    report_id = str(payload["report_id"])
    lease_id = str(payload["lease_id"])
    error = payload.get("error")
    row = database.execute_native(
        "SELECT game_id,parent_lease_id,attempts FROM game_analysis_position_reports "
        "WHERE id=%s AND state='leased' AND lease_id=%s FOR UPDATE",
        (report_id, lease_id),
    ).fetchone()
    if row is None:
        return {"status": "stale"}
    failed = error is not None and row["attempts"] >= 3
    now = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "UPDATE game_analysis_position_reports SET state=%s,lease_id=NULL,parent_lease_id=NULL,"
        "lease_expires_at=NULL,last_error=%s,updated_at=%s WHERE id=%s",
        ("failed" if failed else "queued", error, now, report_id),
    )
    if error:
        database.execute_native(
            "INSERT INTO game_analysis_position_errors(report_id,game_id,error,recorded_at) "
            "VALUES(%s,%s,%s,%s)", (report_id, row["game_id"], error, now),
        )
    parent = database.execute_native(
        "UPDATE game_analysis_jobs SET status=%s,lease_id=NULL,lease_expires_at=NULL,"
        "last_error=%s,updated_at=%s WHERE game_id=%s AND status='leased' AND lease_id=%s "
        "RETURNING game_id",
        ("failed" if failed else "queued", error, now, row["game_id"], row["parent_lease_id"]),
    ).fetchone()
    if parent is not None:
        database.execute_native(
            "UPDATE imported_games SET analysis_state=%s WHERE id=%s",
            ("failed" if failed else "pending", row["game_id"]),
        )
    return {"status": "failed" if failed else "queued"}


register_command("games.analysis.position.report", publish_position_report)
register_command("games.analysis.position.release", release_position_report)
