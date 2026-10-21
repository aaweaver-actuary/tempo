"""Leased game-analysis claims executed by the background Celery worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .services.engine_diagnostics import record_engine_outcome
from .services.background_metrics import increment
from tempo_legacy_database.connections import background_read_connection
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.games import analysis_leases as storage
from .services.game_analysis_worker import _confirmed_indices, _positions
from .services.threat_pipeline import ENGINE_VERSION, NETWORK_VERSION
from .services.threat_pipeline import report_from_json, validate_analysis_report
from .services.threat_validation import AnalysisRequest
from .services.engine_scheduling import ordinary_selection_allowed, record_ordinary_selection


def claim_game_analysis(database: PostgresConnection, _payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    if not ordinary_selection_allowed(database):
        return {"job": None}
    job = storage.next_parent(database, now_text)
    if job is None:
        return {"job": None}
    lease_id = str(uuid.uuid4())
    expires_at = (now + timedelta(minutes=5)).isoformat()
    accepted_parent = storage.lease_parent(database, job["game_id"], lease_id, expires_at, now_text)
    if accepted_parent != 1:
        return {"job": None}
    storage.mark_game_analyzing(database, job["game_id"])
    record_ordinary_selection(database)
    return {"job": {**dict(job), "moves": json.loads(job["moves_json"]),
                    "lease_id": lease_id, "lease_expires_at": expires_at}}


register_command("games.analysis.claim", claim_game_analysis)


def prepare_position_claim(preferred_game_id: str | None = None) -> dict[str, Any] | None:
    """Read a candidate, then traverse its chess history after closing PostgreSQL."""

    with background_read_connection() as database:
        candidate = storage.prepared_candidate(database, preferred_game_id, datetime.now(timezone.utc).isoformat())
        if candidate is None:
            return None
        saved_reports = storage.saved_position_reports(database, candidate["game_id"], candidate["analysis_version"])
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
    if not ordinary_selection_allowed(database):
        return {"job": None}
    parent = storage.lock_parent(database, plan["game_id"])
    if (parent is None or parent["analysis_version"] != plan["analysis_version"]
            or parent["analysis_evidence_version"] != plan["analysis_evidence_version"]
            or not parent["rated"] or parent["speed"] not in {"blitz", "rapid", "classical"}
            or not (parent["status"] == "queued" or
                    parent["status"] == "leased" and parent["lease_expires_at"] is not None
                    and parent["lease_expires_at"] < now_text)):
        return {"job": None}
    control = storage.analysis_control(database, plan["game_id"])
    if control is not None and control["paused"]:
        return {"job": None}
    position_index = plan["position_index"]
    existing = None
    if position_index is not None:
        existing = storage.lock_position(database, plan["game_id"], plan["analysis_version"], plan["scan_pass"], position_index)
        if existing is not None and existing["state"] == "failed":
            storage.fail_exhausted_parent(database, plan["game_id"], now_text)
            storage.mark_game_failed(database, plan["game_id"])
            return {"job": None}
        if existing is not None and (existing["state"] == "complete" or
                existing["state"] == "leased" and existing["lease_expires_at"] is not None
                and existing["lease_expires_at"] >= now_text):
            return {"job": None}
    parent_lease_id = str(uuid.uuid4())
    accepted_parent = storage.lease_parent(database, plan["game_id"], parent_lease_id, (now + timedelta(minutes=5)).isoformat(), now_text)
    if accepted_parent != 1:
        return {"job": None}
    storage.mark_game_analyzing(database, plan["game_id"])
    increment(database, "engine_game", plan["game_id"], claims=1,
              **({"lease_expiries": 1, "lease_reclaims": 1, "generation_restarts": 1}
                 if parent["status"] == "leased" else {}))
    if plan["kind"] == "finalize":
        record_ordinary_selection(database)
        return {"job": {"kind": "finalize", "game_id": plan["game_id"],
                        "lease_id": parent_lease_id}}
    if plan["kind"] == "terminal":
        accepted_position = storage.add_terminal_position(database, str(uuid.uuid4()), plan["game_id"], plan["analysis_version"], plan["scan_pass"], position_index, json.dumps({"terminal": plan["terminal"]}), now_text)
        if accepted_position != 1:
            raise HTTPException(409, "Game position changed; retry the claim")
        storage.requeue_terminal_parent(database, plan["game_id"], now_text)
        storage.mark_game_pending(database, plan["game_id"])
        record_ordinary_selection(database)
        return {"job": None}
    position_lease_id = str(uuid.uuid4())
    report_id = existing["id"] if existing else str(uuid.uuid4())
    request_json = json.dumps(plan["request"], sort_keys=True)
    expires_at = (now + timedelta(minutes=2)).isoformat()
    if existing:
        accepted_position = storage.lease_existing_position(database, report_id, position_lease_id, parent_lease_id, expires_at, request_json, now_text)
    else:
        accepted_position = storage.lease_new_position(database, report_id, plan["game_id"], plan["analysis_version"], plan["scan_pass"], position_index, request_json, position_lease_id, parent_lease_id, expires_at, now_text)
    if accepted_position != 1:
        raise HTTPException(409, "Game position changed; retry the claim")
    record_ordinary_selection(database)
    return {"job": {"kind": "search", "id": report_id, "game_id": plan["game_id"],
                    "position_index": position_index, "scan_pass": plan["scan_pass"],
                    "lease_id": position_lease_id, "request": plan["request"]}}


register_command("games.analysis.position.claim", claim_game_analysis_position)


def prepare_position_report(report_id: str, raw_report: dict[str, Any]) -> str:
    with background_read_connection() as database:
        row = storage.report_request(database, report_id)
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
    stored = storage.lock_report_for_publication(database, report_id)
    report_json = json.dumps(raw_report)
    if stored is None:
        raise HTTPException(422, "Game position request not found")
    if stored["state"] == "complete" and stored["report_json"] == report_json:
        return {"status": "complete"}
    if (stored["state"] != "leased" or stored["lease_id"] != lease_id
            or stored["request_json"] != payload["request_json"]):
        raise HTTPException(422, "Game position lease is stale")
    now = datetime.now(timezone.utc).isoformat()
    storage.complete_position(database, report_id, report_json, now)
    record_engine_outcome(database, "engine_game", report_id, payload, completed=True)
    released = storage.release_completed_position_parent(database, stored["game_id"], stored["parent_lease_id"], now)
    if released is not None:
        storage.mark_game_pending(database, stored["game_id"])
    return {"status": "complete"}


def release_position_report(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    report_id = str(payload["report_id"])
    lease_id = str(payload["lease_id"])
    error = payload.get("error")
    row = storage.lock_position_release(database, report_id, lease_id)
    if row is None:
        return {"status": "stale"}
    failed = error is not None and row["attempts"] >= 3
    now = datetime.now(timezone.utc).isoformat()
    storage.release_position(database, report_id, "failed" if failed else "queued", error, now)
    record_engine_outcome(database, "engine_game", report_id, payload)
    if error:
        storage.record_position_error(database, report_id, row["game_id"], error, now)
    parent = storage.release_failed_position_parent(database, row["game_id"], row["parent_lease_id"], "failed" if failed else "queued", error, now)
    if parent is not None:
        storage.set_game_analysis_state(database, row["game_id"], "failed" if failed else "pending")
    return {"status": "failed" if failed else "queued"}


register_command("games.analysis.position.report", publish_position_report)
register_command("games.analysis.position.release", release_position_report)


def fail_parent_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    game_id, lease_id = str(payload["game_id"]), str(payload["lease_id"])
    error = str(payload["error"])
    now = datetime.now(timezone.utc).isoformat()
    failed = storage.fail_parent(database, game_id, lease_id, error, now)
    if failed is None:
        exists = storage.parent_exists(database, game_id)
        raise HTTPException(409 if exists else 404,
                            "Analysis lease is no longer active" if exists else "Analysis job not found")
    storage.mark_game_failed(database, game_id)
    return {"status": "failed", "retryable": True}


def heartbeat_parent_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    updated = storage.heartbeat_parent(database, str(payload["game_id"]), str(payload["lease_id"]), (now + timedelta(minutes=5)).isoformat(), now.isoformat())
    if updated is None:
        raise HTTPException(409, "Analysis lease is no longer active")
    return {"status": "leased"}


def release_parent_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    game_id = str(payload["game_id"])
    updated = storage.release_parent(database, game_id, str(payload["lease_id"]), datetime.now(timezone.utc).isoformat())
    if updated is not None:
        storage.mark_game_pending(database, game_id)
    return {"status": "queued" if updated is not None else "stale"}


def retry_parent_analysis(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    game_id = str(payload["game_id"])
    now = datetime.now(timezone.utc).isoformat()
    retried = storage.retry_failed_parent(database, game_id, now)
    if retried is None:
        raise HTTPException(409, "Only failed analysis jobs can be retried")
    storage.retry_failed_positions(database, game_id, retried["analysis_version"], now)
    storage.mark_game_pending(database, game_id)
    storage.mark_activity_queued(database, game_id, now)
    return {"status": "queued"}


register_command("games.analysis.failure", fail_parent_analysis)
register_command("games.analysis.heartbeat", heartbeat_parent_analysis)
register_command("games.analysis.release", release_parent_analysis)
register_command("games.analysis.retry", retry_parent_analysis)


def repair_one_stockfish_timeout(
    database: PostgresConnection, _payload: dict[str, Any],
) -> dict[str, Any]:
    """Requeue one legacy timeout while fencing concurrent repair workers."""
    row = storage.legacy_timeout_candidate(database)
    if row is None:
        return {"requeued": False}
    now = datetime.now(timezone.utc).isoformat()
    storage.record_position_error(database, f"legacy:{row['game_id']}", row["game_id"], row["last_error"], now)
    storage.requeue_legacy_timeout(database, row["game_id"], now)
    storage.mark_game_pending(database, row["game_id"])
    return {"requeued": True, "game_id": row["game_id"]}


def repair_one_legacy_network_identity(
    database: PostgresConnection, _payload: dict[str, Any],
) -> dict[str, Any]:
    """Queue one completed game with invalid historical NNUE provenance."""
    row = storage.legacy_network_candidate(database)
    if row is None:
        return {"requeued": False}
    now = datetime.now(timezone.utc).isoformat()
    storage.requeue_legacy_network(database, row["game_id"], max(row["analysis_version"] + 1, row["published_version"] + 1), now)
    storage.mark_game_pending(database, row["game_id"])
    storage.record_position_error(database, f"legacy-network:{row['game_id']}", row["game_id"], "Stored network identity differs from the browser's loaded NNUE; queued for verified Docker reanalysis", now)
    return {"requeued": True, "game_id": row["game_id"]}


register_command("games.analysis.repair_timeout", repair_one_stockfish_timeout)
register_command("games.analysis.repair_provenance", repair_one_legacy_network_identity)
