"""Fetch and publish one Explorer coverage node per durable background slice."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from ..database import background_read_connection, connection
from ..postgres_store import PostgresConnection
from .canonical_scope_freshness import coverage_run_is_current, latest_coverage_attempt_predicate
from .canonical_prefix import read_prefix
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    lock_current_slice,
)
from .postgres_coverage_candidates import recalculate_coverage_node
from .introduction_priorities import enqueue_priority_refresh_in_transaction
from .repertoire_opportunities import enqueue_opportunity_refresh_in_transaction
from .repertoire_coverage import (
    ExplorerAuthenticationError, _cached_explorer_payload, _fetch_explorer,
    get_explorer_session_token,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prepare_next_node(task: dict[str, Any]) -> dict[str, Any] | None:
    payload = task["payload"]
    with background_read_connection() as database:
        lease = database.execute_native(
            "SELECT generation,lease_token,state FROM background_tasks WHERE id=%s",
            (task["id"],),
        ).fetchone()
        if (lease is None or lease["generation"] != task["generation"]
                or lease["lease_token"] != task["lease_token"] or lease["state"] != "leased"):
            return None
        row = database.execute_native(
            "SELECT n.id,n.run_id,n.repertoire_id,n.fen,n.fen_key,n.covered_replies_json,"
            "r.settings_json FROM repertoire_coverage_nodes n "
            "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
            "LEFT JOIN background_activity control ON control.source='coverage' "
            "AND control.work_id=n.run_id "
            "WHERE n.run_id=%s AND n.id>%s AND n.explorer_status='queued' "
            "AND r.status IN ('queued','running','failed') AND COALESCE(control.paused,0)=0 "
            f"AND {latest_coverage_attempt_predicate(database, native=True)} ORDER BY n.id LIMIT 1",
            (payload["run_id"], payload.get("after_node_id", "")),
        ).fetchone()
        return dict(row) if row and coverage_run_is_current(database, row, row["repertoire_id"]) else None


def _cached_or_fetched_payload(node: dict[str, Any]) -> tuple[dict, str, str, str]:
    settings = json.loads(node["settings_json"])
    rating_bucket = int(settings.get("explorer_rating", settings["maia_elo"]))
    speed_weights = settings.get(
        "speed_weights", {"blitz": 1 / 3, "rapid": 1 / 3, "classical": 1 / 3},
    )
    speeds = ",".join(
        f"{speed}:{weight:.6f}" for speed, weight in sorted(speed_weights.items())
    )
    ratings = str(rating_bucket)
    with background_read_connection() as database:
        cached, cache_key = _cached_explorer_payload(database, node["fen"], speeds, ratings)
    if cached is not None:
        return cached, cache_key, speeds, ratings
    token = get_explorer_session_token()
    if not token:
        raise ExplorerAuthenticationError()
    return _fetch_explorer(node["fen"], speeds, ratings, token), cache_key, speeds, ratings


def _candidate_rows(node: dict[str, Any], explorer_payload: dict) -> tuple[list[dict], int]:
    counts = {
        str(move["uci"]): int(move.get("white", 0)) + int(move.get("draws", 0))
        + int(move.get("black", 0))
        for move in explorer_payload.get("moves", []) if move.get("uci")
    }
    total_games = int(explorer_payload.get("_explorer_games", sum(counts.values())))
    probabilities = explorer_payload.get("_probabilities") or {}
    denominator = sum(counts.values())
    covered = set(json.loads(node["covered_replies_json"]))
    return ([{
        "move_uci": move_uci,
        "explorer_probability": float(probabilities[move_uci])
        if move_uci in probabilities else move_games / denominator if denominator else None,
        "covered": int(move_uci in covered),
    } for move_uci, move_games in counts.items()], total_games)


def _fail_for_missing_token(task: dict[str, Any], node: dict[str, Any]) -> bool:
    message = "Set an Explorer token in Tempo or TEMPO_LICHESS_EXPLORER_TOKEN and refresh coverage"
    with connection(background=True) as database:
        read_prefix(database, node["repertoire_id"], lock=True)
        if not lock_current_slice(database, task):
            return False
        if not coverage_run_is_current(database, node, node["repertoire_id"]):
            return complete_task_slice_in_transaction(database, task)
        database.execute_native(
            "UPDATE repertoire_coverage_nodes SET explorer_status='failed',last_error=%s,updated_at=%s "
            "WHERE id=%s AND explorer_status='queued'",
            (message, _now(), node["id"]),
        )
        database.execute_native(
            "UPDATE repertoire_coverage_runs SET status='failed',last_error=%s,updated_at=%s "
            "WHERE id=%s AND status IN ('queued','running')",
            (message, _now(), node["run_id"]),
        )
        enqueue_opportunity_refresh_in_transaction(database, node["repertoire_id"])
        return complete_task_slice_in_transaction(database, task)


def _publish_node(
    database: PostgresConnection, task: dict[str, Any], prepared: dict[str, Any],
    explorer_payload: dict, cache_key: str, speeds: str, ratings: str,
) -> bool:
    read_prefix(database, prepared["repertoire_id"], lock=True)
    if not lock_current_slice(database, task):
        return False
    node = database.execute_native(
        "SELECT n.*,r.settings_json,r.status AS run_status,r.total_nodes "
        "FROM repertoire_coverage_nodes n JOIN repertoire_coverage_runs r ON r.id=n.run_id "
        f"WHERE n.id=%s AND {latest_coverage_attempt_predicate(database, native=True)} FOR UPDATE OF n,r", (prepared["id"],),
    ).fetchone()
    if node is None or node["run_status"] not in {"queued", "running", "failed"} or not coverage_run_is_current(database, node, prepared["repertoire_id"]):
        return complete_task_slice_in_transaction(database, task)
    if node["explorer_status"] != "queued":
        return advance_task_slice_in_transaction(
            database, task, next_phase="explorer",
            next_payload={**task["payload"], "after_node_id": prepared["id"]},
        )
    candidate_rows, total_games = _candidate_rows(dict(node), explorer_payload)
    database.execute_native(
        "INSERT INTO explorer_position_cache(cache_key,fen_key,speeds,ratings,response_json,fetched_at) "
        "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(cache_key) DO UPDATE SET "
        "response_json=excluded.response_json,fetched_at=excluded.fetched_at",
        (cache_key, node["fen_key"], speeds, ratings,
         json.dumps(explorer_payload), _now()),
    )
    if candidate_rows:
        database.execute_native(
            "INSERT INTO repertoire_coverage_candidates("
            "node_id,move_uci,explorer_probability,covered,source_state) "
            "SELECT %s,move_uci,explorer_probability,covered,'explorer-only' "
            "FROM jsonb_to_recordset(%s::jsonb) AS candidate("
            "move_uci text,explorer_probability double precision,covered bigint) "
            "ON CONFLICT(node_id,move_uci) DO UPDATE SET "
            "explorer_probability=excluded.explorer_probability,covered=excluded.covered",
            (node["id"], json.dumps(candidate_rows)),
        )
    recalculate_coverage_node(database, node["id"], json.loads(node["settings_json"]), total_games)
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET explorer_status='complete',explorer_games=%s,"
        "last_error=NULL,updated_at=%s WHERE id=%s",
        (total_games, _now(), node["id"]),
    )
    progress = database.execute_native(
        "UPDATE repertoire_coverage_runs SET completed_nodes=completed_nodes+1,"
        "status=CASE WHEN completed_nodes+1>=total_nodes THEN 'complete' ELSE 'running' END,"
        "updated_at=%s WHERE id=%s RETURNING completed_nodes,status,total_nodes",
        (_now(), node["run_id"]),
    ).fetchone()
    maia_done = database.execute_native(
        "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=%s AND maia_status='complete'",
        (node["run_id"],),
    ).fetchone()[0]
    database.execute_native(
        "INSERT INTO background_activity(source,work_id,generation_key,phase,"
        "completed_units,total_units,updated_at) "
        "VALUES('coverage',%s,%s,'Checking positions',%s,%s,%s) "
        "ON CONFLICT(source,work_id) DO UPDATE SET "
        "generation_key=excluded.generation_key,phase=excluded.phase,"
        "completed_units=excluded.completed_units,total_units=excluded.total_units,"
        "updated_at=excluded.updated_at",
        (node["run_id"], node["run_id"], progress["completed_nodes"] + maia_done,
         progress["total_nodes"] * 2, _now()),
    )
    enqueue_priority_refresh_in_transaction(database, node["repertoire_id"])
    enqueue_opportunity_refresh_in_transaction(database, node["repertoire_id"])
    return advance_task_slice_in_transaction(
        database, task, next_phase="explorer",
        next_payload={**task["payload"], "after_node_id": node["id"]},
    )


def execute_coverage_explorer_slice(task: dict[str, Any]) -> bool:
    prepared = _prepare_next_node(task)
    if prepared is None:
        with connection(background=True) as database:
            if not lock_current_slice(database, task):
                return False
            return complete_task_slice_in_transaction(database, task)
    try:
        explorer_payload, cache_key, speeds, ratings = _cached_or_fetched_payload(prepared)
    except ExplorerAuthenticationError:
        return _fail_for_missing_token(task, prepared)
    with connection(background=True) as database:
        return _publish_node(database, task, prepared, explorer_payload, cache_key, speeds, ratings)
