"""Durable admission and bounded publication of a complete game analysis."""

from __future__ import annotations

from .services.background_metrics import increment

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.connections import background_read_connection, connection
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.games import analysis_publications as storage
from .services.durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction, lock_current_slice,
)


def admit_game_analysis_publication(
    database: PostgresConnection, payload: dict[str, Any],
) -> dict[str, Any]:
    game_id = str(payload["game_id"])
    version = int(payload["analysis_version"])
    evidence_version = int(payload["analysis_evidence_version"])
    lease_id = str(payload["lease_id"])
    now = datetime.now(timezone.utc).isoformat()
    job = storage.lock_analysis_admission(database, game_id)
    if job is None:
        raise HTTPException(404, "Analysis job not found")
    if job["status"] == "complete" and job["idempotency_key"] == payload["idempotency_key"]:
        published = storage.published_result(database, game_id)
        return json.loads(published["result_json"]) if published else payload["result"]
    if (job["analysis_version"] != version
            or job["analysis_evidence_version"] != int(payload["expected_evidence_version"])
            or job["status"] != "leased" or job["lease_id"] != lease_id):
        raise HTTPException(409, "Analysis lease is no longer active")
    return _queue_game_analysis_publication(database, payload, now)


def _queue_game_analysis_publication(
    database: PostgresConnection, payload: dict[str, Any], now: str,
) -> dict[str, Any]:
    game_id = str(payload["game_id"])
    version = int(payload["analysis_version"])
    evidence_version = int(payload["analysis_evidence_version"])
    storage.queue_publication(database, game_id, version, evidence_version,
                              json.dumps(payload["prepared"], separators=(",", ":")),
                              json.dumps(payload["result"], separators=(",", ":")), now)
    enqueue_compact_postgres_task_in_transaction(
        database, "game_analysis_publish", game_id,
        {"game_id": game_id, "analysis_version": version, "next_ply": 0}, priority=75,
    )
    task = storage.publication_task(database, game_id)
    return {"status": "preparing", "task_id": task["id"]}


register_command("games.analysis.finalize.admit", admit_game_analysis_publication)


def admit_manual_game_analysis(
    database: PostgresConnection, payload: dict[str, Any],
) -> dict[str, Any]:
    """Fence a manual save, then hand its large publication to durable slices."""
    game_id = str(payload["game_id"])
    job = storage.lock_manual_admission(database, game_id)
    game = storage.lock_manual_game(database, game_id)
    if game is None:
        raise HTTPException(404, "Game not found")
    prepared_game = payload["prepared"]["game"]
    if (game["color"] != prepared_game["color"]
            or game["start_fen"] != prepared_game["start_fen"]
            or game["moves_json"] != prepared_game["moves_json"]):
        raise HTTPException(409, "The game changed during analysis; refresh and retry")
    request = payload["prepared"]["request"]
    request_key = request.get("idempotency_key")
    if request_key and job is not None and job["status"] == "complete":
        if job["idempotency_key"] != request_key:
            raise HTTPException(409, "A different analysis was already submitted")
        published = storage.published_result(database, game_id)
        if published is not None:
            return {**json.loads(published["result_json"]), "idempotent": True}
    lease_id = request.get("lease_id")
    if lease_id and (job is None or job["status"] != "leased"
                     or job["lease_id"] != lease_id):
        raise HTTPException(409, "Analysis lease is no longer active")
    version = max(int(game["analysis_version"]) + 1,
                  int(job["analysis_version"]) + 1 if job is not None else 1,
                  int(request["analysis_version"]))
    now = datetime.now(timezone.utc).isoformat()
    if job is None:
        storage.add_manual_job(database, game_id, version, request["analysis_evidence_version"], now)
    else:
        storage.update_manual_job(database, game_id, version, request["analysis_evidence_version"], now)
    return _queue_game_analysis_publication(database, {
        **payload, "analysis_version": version,
        "analysis_evidence_version": request["analysis_evidence_version"],
    }, now)


register_command("games.analysis.manual.admit", admit_manual_game_analysis)


PUBLICATION_SLICE_PLIES = 8


def _slice_rows(prepared: dict[str, Any], start_ply: int) -> tuple[list[dict], list[dict]]:
    game_id = prepared["game_id"]
    version = prepared["analysis_version"]
    game_color = prepared["game"]["color"]
    game_moves = json.loads(prepared["game"]["moves_json"])
    request = prepared["request"]
    result = prepared["result"]
    analysis_rows: list[dict] = []
    candidate_rows: list[dict] = []
    for evaluation in prepared["evaluations"][start_ply:start_ply + PUBLICATION_SLICE_PLIES]:
        ply = int(evaluation["ply"])
        mover_color = evaluation["mover_color"]
        loss = (int(evaluation["before_cp"]) - int(evaluation["after_cp"])) * (
            1 if mover_color == "white" else -1
        )
        label = (
            "missed punishment" if ply == result["missed_punishment_ply"] else
            "major mistake" if ply == result["major_mistake_ply"] else None
        )
        analysis_rows.append({
            "game_id": game_id, "publication_generation": version, "ply": ply,
            "eval_before_cp": int(evaluation["before_cp"]),
            "eval_after_cp": int(evaluation["after_cp"]), "loss_cp": loss,
            "label": label, "depth": int(evaluation.get("depth", request["depth"])),
            "best_move_uci": evaluation.get("best_move_uci"),
            "principal_variation_json": json.dumps(evaluation.get("principal_variation", [])),
            "mate_before": evaluation.get("mate_before"),
            "mate_after": evaluation.get("mate_after"),
            "engine_version": request["engine_version"],
            "network_version": request["network_version"],
            "mover_color": mover_color, "is_player_move": int(mover_color == game_color),
            "actual_move_uci": evaluation.get("actual_move_uci") or game_moves[ply],
            "position_fen": evaluation["position_fen"],
        })
        for rank, candidate in enumerate(evaluation["candidate_lines"], start=1):
            candidate_rows.append({
                "game_id": game_id, "publication_generation": version,
                "ply": ply, "rank": rank, "candidate_uci": candidate["uci"],
                "score_cp": candidate.get("cp"), "mate": candidate.get("mate"),
                "score_text": candidate.get("score"),
                "principal_variation_json": json.dumps(candidate["pv"]),
                "depth": int(evaluation.get("depth", request["depth"])),
                "position_fen": evaluation["position_fen"],
                "engine_version": request["engine_version"],
                "network_version": request["network_version"],
            })
    return analysis_rows, candidate_rows


def _read_publication(task: dict[str, Any]) -> dict[str, Any] | None:
    with background_read_connection() as database:
        lease = storage.task_lease(database, task["id"])
        if (lease is None or lease["generation"] != task["generation"]
                or lease["lease_token"] != task["lease_token"] or lease["state"] != "leased"):
            return None
        row = storage.publication(database, task["payload"]["game_id"])
    if row is None or row["analysis_version"] != task["payload"]["analysis_version"]:
        return None
    prepared = json.loads(row["prepared_json"])
    prepared.update(game_id=task["payload"]["game_id"],
                    analysis_version=row["analysis_version"],
                    result=json.loads(row["result_json"]))
    return {**dict(row), "prepared": prepared}


def _write_publication_slice(
    database: PostgresConnection, task: dict[str, Any], publication: dict[str, Any],
    analysis_rows: list[dict], candidate_rows: list[dict],
) -> bool:
    if not lock_current_slice(database, task):
        return False
    game_id = task["payload"]["game_id"]
    version = task["payload"]["analysis_version"]
    current = storage.lock_publication_cursor(database, game_id, version)
    if (current is None or current["next_ply"] != publication["next_ply"]
            or current["status"] not in {"queued", "publishing"}):
        return False
    start_ply = int(current["next_ply"])
    end_ply = start_ply + len(analysis_rows)
    storage.reset_staged_moves(database, game_id, version, start_ply, end_ply)
    storage.write_staged_moves(database, json.dumps(analysis_rows, separators=(",", ":")))
    if candidate_rows:
        storage.write_staged_candidates(database, json.dumps(candidate_rows, separators=(",", ":")))
    storage.advance_publication_cursor(database, game_id, version, end_ply, datetime.now(timezone.utc).isoformat())
    return advance_task_slice_in_transaction(
        database, task, next_phase="publishing",
        next_payload={"game_id": game_id, "analysis_version": version, "next_ply": end_ply},
    )


def execute_game_analysis_publication_slice(task: dict[str, Any]) -> bool:
    publication = _read_publication(task)
    if publication is None:
        with connection(background=True) as database:
            if not lock_current_slice(database, task):
                return False
            return complete_task_slice_in_transaction(database, task)
    prepared = publication["prepared"]
    next_ply = int(publication["next_ply"])
    total_plies = len(prepared["evaluations"])
    if next_ply < total_plies:
        analysis_rows, candidate_rows = _slice_rows(prepared, next_ply)
        with connection(background=True) as database:
            return _write_publication_slice(database, task, publication,
                                            analysis_rows, candidate_rows)
    with connection(background=True) as database:
        return _switch_published_generation(database, task, publication)


def _switch_published_generation(
    database: PostgresConnection, task: dict[str, Any], publication: dict[str, Any],
) -> bool:
    if not lock_current_slice(database, task):
        return False
    game_id = task["payload"]["game_id"]
    version = task["payload"]["analysis_version"]
    current = storage.lock_publication_cursor(database, game_id, version)
    expected_rows = len(publication["prepared"]["evaluations"])
    if (current is None or current["next_ply"] != expected_rows
            or current["status"] not in {"queued", "publishing"}):
        return False
    job = storage.lock_generation_job(database, game_id, version)
    if job is None or job["status"] != "publishing":
        return False
    staged_count = storage.staged_move_count(database, game_id, version)
    if staged_count != expected_rows:
        raise RuntimeError("Staged game analysis has incomplete move rows")
    result = json.loads(publication["result_json"])
    request = publication["prepared"]["request"]
    now = datetime.now(timezone.utc).isoformat()
    storage.publish_game_generation(database, game_id, version, publication["analysis_evidence_version"],
                                    result["major_mistake_ply"], result["missed_punishment_ply"])
    storage.complete_publication_job(database, game_id, publication["analysis_evidence_version"], request["idempotency_key"], now)
    enqueue_compact_postgres_task_in_transaction(
        database, "game_analysis_followup", game_id,
        {"game_id": game_id, "analysis_version": version, "phase": "derivation", "cursor": ""},
        priority=120,
    )
    completed = complete_task_slice_in_transaction(database, task)
    if completed:
        increment(database, "game_analysis_publish", task["id"], useful_completions=1)
    return completed


def execute_game_analysis_followup_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = payload["game_id"]
    version = payload["analysis_version"]
    phase = payload["phase"]
    next_repertoire_id = None
    if phase == "repertoires":
        with background_read_connection() as database:
            row = storage.next_affected_repertoire(database, game_id, payload["cursor"])
            next_repertoire_id = row["repertoire_id"] if row else None
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        published = storage.lock_published_game(database, game_id)
        if (published is None or published["analysis_version"] != version
                or published["published_analysis_generation"] != version):
            return complete_task_slice_in_transaction(database, task)
        now = datetime.now(timezone.utc).isoformat()
        if phase == "derivation":
            storage.restart_derivation(database, game_id, now)
            return advance_task_slice_in_transaction(
                database, task, next_phase="derivation_task",
                next_payload={**payload, "phase": "derivation_task"},
            )
        if phase == "derivation_task":
            derivation = storage.derivation_version(database, game_id)
            enqueue_compact_postgres_task_in_transaction(
                database, "game_derivation_positions", game_id,
                {"game_id": game_id, "derivation_version": derivation["derivation_version"],
                 "cursor": 0},
                priority=125,
            )
            return advance_task_slice_in_transaction(
                database, task, next_phase="threat",
                next_payload={**payload, "phase": "threat"},
            )
        if phase == "threat":
            enqueue_compact_postgres_task_in_transaction(
                database, "defensive_threat_scan", game_id,
                {"game_id": game_id, "analysis_version": version, "cursor": 0},
                priority=145,
            )
            return advance_task_slice_in_transaction(
                database, task, next_phase="repertoires",
                next_payload={**payload, "phase": "repertoires", "cursor": ""},
            )
        if phase != "repertoires":
            raise ValueError(f"Unknown game analysis follow-up phase: {phase}")
        if next_repertoire_id is None:
            return complete_task_slice_in_transaction(database, task)
        exists = storage.current_repertoire_match(database, game_id, next_repertoire_id)
        if exists is not None:
            from .services.repertoire_opportunities import enqueue_opportunity_refresh_in_transaction
            enqueue_opportunity_refresh_in_transaction(database, next_repertoire_id)
        return advance_task_slice_in_transaction(
            database, task, next_phase="repertoires",
            next_payload={**payload, "cursor": next_repertoire_id},
        )
