"""Durable admission and bounded publication of a complete game analysis."""

from __future__ import annotations

from .services.background_metrics import increment

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .database import background_read_connection, connection
from .postgres_store import PostgresConnection
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
    job = database.execute_native(
        "SELECT status,lease_id,analysis_version,analysis_evidence_version,idempotency_key "
        "FROM game_analysis_jobs WHERE game_id=%s FOR UPDATE", (game_id,),
    ).fetchone()
    if job is None:
        raise HTTPException(404, "Analysis job not found")
    if job["status"] == "complete" and job["idempotency_key"] == payload["idempotency_key"]:
        published = database.execute_native(
            "SELECT result_json FROM game_analysis_publications WHERE game_id=%s",
            (game_id,),
        ).fetchone()
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
    database.execute_native(
        "INSERT INTO game_analysis_publications("
        "game_id,analysis_version,analysis_evidence_version,prepared_json,next_ply,"
        "status,result_json,updated_at) "
        "VALUES(%s,%s,%s,%s,0,'queued',%s,%s) "
        "ON CONFLICT(game_id) DO UPDATE SET "
        "analysis_version=excluded.analysis_version,"
        "analysis_evidence_version=excluded.analysis_evidence_version,"
        "prepared_json=excluded.prepared_json,next_ply=0,status='queued',"
        "result_json=excluded.result_json,last_error=NULL,updated_at=excluded.updated_at",
        (game_id, version, evidence_version,
         json.dumps(payload["prepared"], separators=(",", ":")),
         json.dumps(payload["result"], separators=(",", ":")), now),
    )
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='publishing',lease_id=NULL,"
        "lease_expires_at=NULL,updated_at=%s WHERE game_id=%s",
        (now, game_id),
    )
    enqueue_compact_postgres_task_in_transaction(
        database, "game_analysis_publish", game_id,
        {"game_id": game_id, "analysis_version": version, "next_ply": 0}, priority=75,
    )
    task = database.execute_native(
        "SELECT id FROM background_tasks WHERE kind='game_analysis_publish' "
        "AND deduplication_key=%s", (game_id,),
    ).fetchone()
    return {"status": "preparing", "task_id": task["id"]}


register_command("games.analysis.finalize.admit", admit_game_analysis_publication)


def admit_manual_game_analysis(
    database: PostgresConnection, payload: dict[str, Any],
) -> dict[str, Any]:
    """Fence a manual save, then hand its large publication to durable slices."""
    game_id = str(payload["game_id"])
    job = database.execute_native(
        "SELECT status,lease_id,analysis_version,idempotency_key "
        "FROM game_analysis_jobs WHERE game_id=%s FOR UPDATE", (game_id,),
    ).fetchone()
    game = database.execute_native(
        "SELECT color,start_fen,moves_json,analysis_version FROM imported_games "
        "WHERE id=%s FOR UPDATE", (game_id,),
    ).fetchone()
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
        published = database.execute_native(
            "SELECT result_json FROM game_analysis_publications WHERE game_id=%s",
            (game_id,),
        ).fetchone()
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
        database.execute_native(
            "INSERT INTO game_analysis_jobs(game_id,analysis_version,"
            "analysis_evidence_version,status,updated_at) "
            "VALUES(%s,%s,%s,'publishing',%s)",
            (game_id, version, request["analysis_evidence_version"], now),
        )
    else:
        database.execute_native(
            "UPDATE game_analysis_jobs SET status='publishing',analysis_version=%s,"
            "analysis_evidence_version=%s,lease_id=NULL,lease_expires_at=NULL,"
            "last_error=NULL,updated_at=%s WHERE game_id=%s",
            (version, request["analysis_evidence_version"], now, game_id),
        )
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
        lease = database.execute_native(
            "SELECT generation,lease_token,state FROM background_tasks WHERE id=%s",
            (task["id"],),
        ).fetchone()
        if (lease is None or lease["generation"] != task["generation"]
                or lease["lease_token"] != task["lease_token"] or lease["state"] != "leased"):
            return None
        row = database.execute_native(
            "SELECT analysis_version,analysis_evidence_version,prepared_json,next_ply,"
            "status,result_json FROM game_analysis_publications WHERE game_id=%s",
            (task["payload"]["game_id"],),
        ).fetchone()
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
    current = database.execute_native(
        "SELECT next_ply,status FROM game_analysis_publications "
        "WHERE game_id=%s AND analysis_version=%s FOR UPDATE",
        (game_id, version),
    ).fetchone()
    if (current is None or current["next_ply"] != publication["next_ply"]
            or current["status"] not in {"queued", "publishing"}):
        return False
    start_ply = int(current["next_ply"])
    end_ply = start_ply + len(analysis_rows)
    database.execute_native(
        "DELETE FROM game_move_analysis_staged WHERE game_id=%s "
        "AND publication_generation=%s AND ply>=%s AND ply<%s",
        (game_id, version, start_ply, end_ply),
    )
    database.execute_native(
        "INSERT INTO game_move_analysis_staged("
        "game_id,publication_generation,ply,eval_before_cp,eval_after_cp,loss_cp,label,"
        "depth,best_move_uci,principal_variation_json,mate_before,mate_after,"
        "engine_version,network_version,mover_color,is_player_move,actual_move_uci,position_fen) "
        "SELECT game_id,publication_generation,ply,eval_before_cp,eval_after_cp,loss_cp,label,"
        "depth,best_move_uci,principal_variation_json,mate_before,mate_after,"
        "engine_version,network_version,mover_color,is_player_move,actual_move_uci,position_fen "
        "FROM jsonb_to_recordset(%s::jsonb) AS item("
        "game_id text,publication_generation bigint,ply bigint,eval_before_cp bigint,"
        "eval_after_cp bigint,loss_cp bigint,label text,depth bigint,best_move_uci text,"
        "principal_variation_json text,mate_before bigint,mate_after bigint,"
        "engine_version text,network_version text,mover_color text,is_player_move bigint,"
        "actual_move_uci text,position_fen text)",
        (json.dumps(analysis_rows, separators=(",", ":")),),
    )
    if candidate_rows:
        database.execute_native(
            "INSERT INTO game_move_analysis_candidates_staged("
            "game_id,publication_generation,ply,rank,candidate_uci,score_cp,mate,"
            "score_text,principal_variation_json,depth,position_fen,engine_version,network_version) "
            "SELECT game_id,publication_generation,ply,rank,candidate_uci,score_cp,mate,"
            "score_text,principal_variation_json,depth,position_fen,engine_version,network_version "
            "FROM jsonb_to_recordset(%s::jsonb) AS item("
            "game_id text,publication_generation bigint,ply bigint,rank bigint,"
            "candidate_uci text,score_cp bigint,mate bigint,score_text text,"
            "principal_variation_json text,depth bigint,position_fen text,"
            "engine_version text,network_version text)",
            (json.dumps(candidate_rows, separators=(",", ":")),),
        )
    database.execute_native(
        "UPDATE game_analysis_publications SET next_ply=%s,status='publishing',updated_at=%s "
        "WHERE game_id=%s AND analysis_version=%s",
        (end_ply, datetime.now(timezone.utc).isoformat(), game_id, version),
    )
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
    current = database.execute_native(
        "SELECT next_ply,status FROM game_analysis_publications "
        "WHERE game_id=%s AND analysis_version=%s FOR UPDATE",
        (game_id, version),
    ).fetchone()
    expected_rows = len(publication["prepared"]["evaluations"])
    if (current is None or current["next_ply"] != expected_rows
            or current["status"] not in {"queued", "publishing"}):
        return False
    job = database.execute_native(
        "SELECT status,analysis_evidence_version FROM game_analysis_jobs "
        "WHERE game_id=%s AND analysis_version=%s FOR UPDATE", (game_id, version),
    ).fetchone()
    if job is None or job["status"] != "publishing":
        return False
    staged_count = database.execute_native(
        "SELECT COUNT(*) FROM game_move_analysis_staged "
        "WHERE game_id=%s AND publication_generation=%s",
        (game_id, version),
    ).fetchone()[0]
    if staged_count != expected_rows:
        raise RuntimeError("Staged game analysis has incomplete move rows")
    result = json.loads(publication["result_json"])
    request = publication["prepared"]["request"]
    now = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "UPDATE imported_games SET analysis_state='ready',analysis_version=%s,"
        "published_analysis_generation=%s,analysis_evidence_version=%s,"
        "major_mistake_ply=%s,missed_punishment_ply=%s WHERE id=%s",
        (version, version, publication["analysis_evidence_version"],
         result["major_mistake_ply"], result["missed_punishment_ply"], game_id),
    )
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='complete',analysis_evidence_version=%s,"
        "idempotency_key=%s,lease_id=NULL,lease_expires_at=NULL,last_error=NULL,"
        "updated_at=%s WHERE game_id=%s",
        (publication["analysis_evidence_version"], request["idempotency_key"], now, game_id),
    )
    database.execute_native(
        "UPDATE game_analysis_publications SET status='complete',updated_at=%s "
        "WHERE game_id=%s", (now, game_id),
    )
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
            row = database.execute_native(
                "SELECT DISTINCT repertoire_id FROM current_game_repertoire_matches game_repertoire_matches "
                "WHERE game_id=%s AND repertoire_id>%s ORDER BY repertoire_id LIMIT 1",
                (game_id, payload["cursor"]),
            ).fetchone()
            next_repertoire_id = row["repertoire_id"] if row else None
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        published = database.execute_native(
            "SELECT analysis_version,published_analysis_generation FROM imported_games "
            "WHERE id=%s FOR UPDATE", (game_id,),
        ).fetchone()
        if (published is None or published["analysis_version"] != version
                or published["published_analysis_generation"] != version):
            return complete_task_slice_in_transaction(database, task)
        now = datetime.now(timezone.utc).isoformat()
        if phase == "derivation":
            database.execute_native(
                "INSERT INTO game_derivation_jobs(game_id,status,updated_at) "
                "VALUES(%s,'queued',%s) ON CONFLICT(game_id) DO UPDATE SET "
                "status='queued',last_error=NULL,"
                "derivation_version=game_derivation_jobs.derivation_version+1,"
                "completed_phases=0,next_attempt_at=NULL,updated_at=excluded.updated_at",
                (game_id, now),
            )
            return advance_task_slice_in_transaction(
                database, task, next_phase="derivation_task",
                next_payload={**payload, "phase": "derivation_task"},
            )
        if phase == "derivation_task":
            derivation = database.execute_native(
                "SELECT derivation_version FROM game_derivation_jobs WHERE game_id=%s",
                (game_id,),
            ).fetchone()
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
        exists = database.execute_native(
            "SELECT 1 FROM current_game_repertoire_matches game_repertoire_matches WHERE game_id=%s AND repertoire_id=%s",
            (game_id, next_repertoire_id),
        ).fetchone()
        if exists is not None:
            from .services.repertoire_opportunities import enqueue_opportunity_refresh_in_transaction
            enqueue_opportunity_refresh_in_transaction(database, next_repertoire_id)
        return advance_task_slice_in_transaction(
            database, task, next_phase="repertoires",
            next_payload={**payload, "cursor": next_repertoire_id},
        )
