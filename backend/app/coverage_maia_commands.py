"""PostgreSQL commands for the external Maia coverage worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .services.postgres_coverage_candidates import recalculate_coverage_node
<<<<<<< HEAD
from .services.canonical_prefix import read_prefix
from .services.canonical_scope_freshness import coverage_run_is_current, coverage_scope_predicate
from .services.durable_tasks import enqueue_compact_postgres_task_in_transaction
=======
from .services.repertoire_opportunities import enqueue_opportunity_refresh_in_transaction
from .services.canonical_prefix import read_prefix
from .services.canonical_scope_freshness import coverage_run_is_current, coverage_scope_predicate, latest_coverage_attempt_predicate
>>>>>>> main
from .services.introduction_priorities import enqueue_priority_refresh_in_transaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease(database: PostgresConnection, payload: dict[str, Any]):
    node_id = str(payload.get("node_id", ""))
    lease_id = str(payload.get("lease_id", ""))
    if not node_id or not lease_id:
        raise HTTPException(422, "Invalid coverage lease")
    owner = database.execute_native("SELECT repertoire_id FROM repertoire_coverage_nodes WHERE id=%s", (node_id,)).fetchone()
    if owner:
        read_prefix(database, owner[0], lock=True)
    row = database.execute_native(
        "SELECT n.*,r.settings_json,r.status AS run_status FROM repertoire_coverage_nodes n "
        "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
<<<<<<< HEAD
        "WHERE n.id=%s FOR UPDATE OF n,r", (node_id,),
    ).fetchone()
    if row is None or row["maia_status"] != "leased" or row["lease_id"] != lease_id:
        raise HTTPException(409, "Coverage lease is no longer active")
    if (row["run_status"] in {"failed", "building"} or not coverage_run_is_current(database, row, row["repertoire_id"])):
=======
        f"WHERE n.id=%s AND {latest_coverage_attempt_predicate(database, native=True)} FOR UPDATE OF n,r", (node_id,),
    ).fetchone()
    if row is None or row["maia_status"] != "leased" or row["lease_id"] != lease_id:
        raise HTTPException(409, "Coverage lease is no longer active")
    if (row["run_status"] == "building" or not coverage_run_is_current(database, row, row["repertoire_id"])):
>>>>>>> main
        raise HTTPException(409, "Coverage run is no longer active")
    return row


def claim_maia_node(database: PostgresConnection, _payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    row = database.execute_native(
        "SELECT n.id,n.run_id,n.repertoire_id,n.fen,r.settings_json FROM repertoire_coverage_nodes n "
        "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
        "LEFT JOIN background_activity control ON control.source='coverage' "
        "AND control.work_id=n.run_id "
<<<<<<< HEAD
        "WHERE n.explorer_status='complete' AND r.status IN ('queued','running','complete') "
        "AND (n.maia_status='queued' OR (n.maia_status='leased' AND n.lease_expires_at<%s)) "
        "AND COALESCE(control.paused,0)=0 "
        f"AND {coverage_scope_predicate(database, native=True)} "
=======
        "WHERE r.status IN ('queued','running','complete','failed') "
        "AND (n.maia_status='queued' OR (n.maia_status='leased' AND n.lease_expires_at<%s)) "
        "AND COALESCE(control.paused,0)=0 "
        f"AND {coverage_scope_predicate(database, native=True)} "
        f"AND {latest_coverage_attempt_predicate(database, native=True)} "
>>>>>>> main
        "ORDER BY COALESCE(control.promoted,0) DESC,r.created_at,n.ply,n.id "
        "LIMIT 1", (now,),
    ).fetchone()
    if row is None:
        return {"job": None}
    read_prefix(database, row["repertoire_id"], lock=True)
    current = database.execute_native("SELECT maia_status,lease_expires_at FROM repertoire_coverage_nodes WHERE id=%s FOR UPDATE SKIP LOCKED", (row["id"],)).fetchone()
    if current is None or (current["maia_status"] != "queued" and not (current["maia_status"] == "leased" and current["lease_expires_at"] < now)) or not coverage_run_is_current(database, row, row["repertoire_id"]):
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
<<<<<<< HEAD
=======
    enqueue_opportunity_refresh_in_transaction(database, row["repertoire_id"])
>>>>>>> main
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
    settings = json.loads(row["settings_json"])
    recalculate_coverage_node(database, row["id"], settings, int(row["explorer_games"]))
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
    enqueue_priority_refresh_in_transaction(database, row["repertoire_id"])
<<<<<<< HEAD
    remaining = database.execute_native(
        "SELECT 1 FROM repertoire_coverage_nodes WHERE run_id=%s "
        "AND maia_status!='complete' LIMIT 1", (row["run_id"],),
    ).fetchone()
    if remaining is None:
        enqueue_compact_postgres_task_in_transaction(
            database, "repertoire_opportunity", row["repertoire_id"],
            {"repertoire_id": row["repertoire_id"], "phase": "summaries", "cursor": ""},
            priority=130, delay_seconds=5,
        )
=======
    enqueue_opportunity_refresh_in_transaction(database, row["repertoire_id"])
>>>>>>> main
    return {"status": "complete"}


register_command("coverage.maia.claim", claim_maia_node)
register_command("coverage.maia.heartbeat", heartbeat_maia_node)
register_command("coverage.maia.release", release_maia_node)
register_command("coverage.maia.failure", fail_maia_node)
register_command("coverage.maia.submit", submit_maia_node)
