"""PostgreSQL commands for the external Maia coverage worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.commands import coverage_maia as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .services.postgres_coverage_candidates import recalculate_coverage_node
from .services.repertoire_opportunities import enqueue_opportunity_refresh_in_transaction
from .services.canonical_prefix import read_prefix
from .services.canonical_scope_freshness import coverage_run_is_current
from .services.introduction_priorities import enqueue_priority_refresh_in_transaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease(database: PostgresConnection, payload: dict[str, Any]):
    node_id = str(payload.get("node_id", ""))
    lease_id = str(payload.get("lease_id", ""))
    if not node_id or not lease_id:
        raise HTTPException(422, "Invalid coverage lease")
    owner = storage.node_owner(database, node_id)
    if owner:
        read_prefix(database, owner[0], lock=True)
    row = storage.locked_lease(database, node_id)
    if row is None or row["maia_status"] != "leased" or row["lease_id"] != lease_id:
        raise HTTPException(409, "Coverage lease is no longer active")
    if (row["run_status"] == "building" or not coverage_run_is_current(database, row, row["repertoire_id"])):
        raise HTTPException(409, "Coverage run is no longer active")
    return row


def claim_maia_node(database: PostgresConnection, _payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    row = storage.claim_candidate(database, now)
    if row is None:
        return {"job": None}
    read_prefix(database, row["repertoire_id"], lock=True)
    current = storage.locked_candidate(database, row["id"])
    if current is None or (current["maia_status"] != "queued" and not (current["maia_status"] == "leased" and current["lease_expires_at"] < now)) or not coverage_run_is_current(database, row, row["repertoire_id"]):
        return {"job": None}
    lease_id = str(uuid.uuid4())
    storage.mark_leased(database, lease_id, (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), now, row["id"])
    return {"job": {"node_id": row["id"], "run_id": row["run_id"],
                    "lease_id": lease_id, "fen": row["fen"],
                    "elo": json.loads(row["settings_json"])["maia_elo"]}}


def heartbeat_maia_node(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    row = _lease(database, payload)
    storage.heartbeat(database, (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), _now(), row["id"])
    return {"status": "leased"}


def release_maia_node(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    node_id = str(payload.get("node_id", ""))
    lease_id = str(payload.get("lease_id", ""))
    if not node_id or not lease_id:
        raise HTTPException(422, "Invalid coverage lease")
    changed = storage.release(database, _now(), node_id, lease_id)
    return {"status": "queued" if changed else "stale"}


def fail_maia_node(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    row = _lease(database, payload)
    error = payload.get("error")
    if not isinstance(error, str) or not error:
        raise HTTPException(422, "Invalid coverage failure report")
    message = f"Maia coverage failed: {error[:900]}"
    now = _now()
    storage.fail_node(database, message, now, row["id"])
    storage.fail_run(database, message, now, row["run_id"])
    enqueue_opportunity_refresh_in_transaction(database, row["repertoire_id"])
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
        storage.upsert_candidates(database, row["id"], covered_replies, json.dumps(moves))
    settings = json.loads(row["settings_json"])
    recalculate_coverage_node(database, row["id"], settings, int(row["explorer_games"]))
    storage.mark_complete(database, _now(), row["id"])
    storage.record_progress(database, row["run_id"], row["run_id"], _now(), row["run_id"])
    enqueue_priority_refresh_in_transaction(database, row["repertoire_id"])
    enqueue_opportunity_refresh_in_transaction(database, row["repertoire_id"])
    return {"status": "complete"}


register_command("coverage.maia.claim", claim_maia_node)
register_command("coverage.maia.heartbeat", heartbeat_maia_node)
register_command("coverage.maia.release", release_maia_node)
register_command("coverage.maia.failure", fail_maia_node)
register_command("coverage.maia.submit", submit_maia_node)
