"""One durable Stockfish position per Docker worker lease."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import chess

from ..database import connection, read_connection
from .activity_gate import activity_gate
from .threat_pipeline import (ENGINE_VERSION, NETWORK_VERSION, report_from_json,
                              validate_analysis_report)
from .threat_validation import AnalysisRequest


EVIDENCE_VERSION = 3
MAX_POSITION_ATTEMPTS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positions(start_fen: str, moves: list[str]) -> list[chess.Board]:
    board = chess.Board(start_fen)
    positions = [board.copy()]
    for move_uci in moves:
        board.push_uci(move_uci)
        positions.append(board.copy())
    return positions


def _white_score(board: chess.Board, raw_report: dict) -> tuple[int, int | None]:
    if board.is_checkmate():
        return (-10_000 if board.turn else 10_000), (-1 if board.turn else 1)
    if board.is_game_over(claim_draw=True):
        return 0, None
    lines = raw_report.get("lines", [])
    if not lines:
        raise ValueError("Missing engine analysis cannot be used as a zero evaluation")
    score = lines[0]["score"]
    if score.get("mate") is not None:
        return (10_000 if score["mate"] > 0 else -10_000), score["mate"]
    if score.get("cp") is None:
        raise ValueError("Engine analysis has neither a centipawn nor a mate score")
    return int(score["cp"]), None


def _confirmed_indices(positions: list[chess.Board], moves: list[str],
                       shallow: dict[int, dict], divergence_ply: int | None) -> set[int]:
    selected: set[int] = set()
    for ply in range(len(moves)):
        before, before_mate = _white_score(positions[ply], shallow[ply])
        after, after_mate = _white_score(positions[ply + 1], shallow[ply + 1])
        mover_sign = 1 if positions[ply].turn else -1
        if ((before - after) * mover_sign >= 75 or ply == divergence_ply
                or before_mate is not None or after_mate is not None):
            selected.update((ply, ply + 1))
    return {index for index in selected if not positions[index].is_game_over(claim_draw=True)}


def _release_parent(game_id: str, parent_lease_id: str, *, failed: bool = False,
                    error: str | None = None) -> None:
    with connection(background=activity_gate.in_background) as database:
        database.execute(
            """UPDATE game_analysis_jobs SET status=?,lease_id=NULL,lease_expires_at=NULL,
                   last_error=?,updated_at=? WHERE game_id=? AND status='leased' AND lease_id=?""",
            ("failed" if failed else "queued", error, _now(), game_id, parent_lease_id),
        )
        database.execute("UPDATE imported_games SET analysis_state=? WHERE id=?",
                         ("failed" if failed else "pending", game_id))


def claim_position(parent_job: dict | None) -> dict | None:
    if not parent_job:
        return None
    game_id = parent_job["game_id"]
    parent_lease_id = parent_job["lease_id"]
    moves = parent_job["moves"]
    positions = _positions(parent_job["start_fen"], moves)
    analysis_version = parent_job["analysis_version"]
    with read_connection() as database:
        rows = database.execute(
            """SELECT scan_pass,position_index,report_json FROM game_analysis_position_reports
               WHERE game_id=? AND analysis_version=? AND state='complete'""",
            (game_id, analysis_version),
        ).fetchall()
    reports = {(row["scan_pass"], row["position_index"]): json.loads(row["report_json"])
               for row in rows}
    shallow = {index: reports[("shallow", index)] for index in range(len(positions))
               if ("shallow", index) in reports}
    next_position = next((index for index in range(len(positions)) if index not in shallow), None)
    scan_pass = "shallow"
    if next_position is None:
        confirmed = _confirmed_indices(positions, moves, shallow, parent_job["divergence_ply"])
        next_position = next((index for index in sorted(confirmed)
                              if ("confirmed", index) not in reports), None)
        scan_pass = "confirmed"
    if next_position is None:
        return {"kind": "finalize", "game_id": game_id,
                "lease_id": parent_lease_id}
    board = positions[next_position]
    if board.is_game_over(claim_draw=True):
        # A terminal board has no legal engine root; store its typed terminal
        # state as one bounded slice and resume at the following position.
        with connection(background=activity_gate.in_background) as database:
            database.execute(
                """INSERT OR IGNORE INTO game_analysis_position_reports
                   (id,game_id,analysis_version,scan_pass,position_index,request_json,report_json,state,updated_at)
                   VALUES(?,?,?,?,?,?,?,'complete',?)""",
                (str(uuid.uuid4()), game_id, analysis_version, scan_pass, next_position,
                 "{}", json.dumps({"terminal": "checkmate" if board.is_checkmate() else "draw"}), _now()),
            )
        _release_parent(game_id, parent_lease_id)
        return None
    depth = 8 if scan_pass == "shallow" else 14
    request = AnalysisRequest(
        position_start_fen=parent_job["start_fen"],
        position_prefix_uci=tuple(moves[:next_position]),
        engine_version=ENGINE_VERSION, network_version=NETWORK_VERSION,
        depth=depth, multipv=5,
    )
    request_json = json.dumps(asdict(request), sort_keys=True)
    lease_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())
    with connection(background=activity_gate.in_background) as database:
        existing = database.execute(
            """SELECT id,state,attempts FROM game_analysis_position_reports
               WHERE game_id=? AND analysis_version=? AND scan_pass=? AND position_index=?""",
            (game_id, analysis_version, scan_pass, next_position),
        ).fetchone()
        if existing and existing["state"] in {"complete", "failed"}:
            existing_state = existing["state"]
        else:
            existing_state = None
        if existing_state:
            pass
        elif existing:
            report_id = existing["id"]
            database.execute(
                """UPDATE game_analysis_position_reports SET state='leased',lease_id=?,parent_lease_id=?,
                   lease_expires_at=?,request_json=?,attempts=attempts+1,updated_at=? WHERE id=?""",
                (lease_id, parent_lease_id,
                 (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
                 request_json, _now(), report_id),
            )
        else:
            database.execute(
                """INSERT INTO game_analysis_position_reports
                   (id,game_id,analysis_version,scan_pass,position_index,request_json,state,
                    lease_id,parent_lease_id,lease_expires_at,attempts,updated_at)
                   VALUES(?,?,?,?,?,?,'leased',?,?,?,1,?)""",
                (report_id, game_id, analysis_version, scan_pass, next_position,
                 request_json, lease_id, parent_lease_id,
                 (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), _now()),
            )
    if existing_state:
        _release_parent(game_id, parent_lease_id,
                        failed=existing_state == "failed",
                        error="A game position exhausted its engine retries" if existing_state == "failed" else None)
        return None
    return {"kind": "search", "id": report_id, "game_id": game_id,
            "position_index": next_position, "scan_pass": scan_pass,
            "lease_id": lease_id, "request": asdict(request)}


def save_position_report(report_id: str, lease_id: str, raw_report: dict) -> str:
    with read_connection() as database:
        row = database.execute("SELECT * FROM game_analysis_position_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise KeyError("Game position request not found")
        saved = dict(row)
    request = AnalysisRequest(**{**json.loads(saved["request_json"]),
                                "position_prefix_uci": tuple(json.loads(saved["request_json"])["position_prefix_uci"])})
    validate_analysis_report(request, report_from_json(raw_report))
    with connection(background=activity_gate.in_background) as database:
        updated = database.execute(
            """UPDATE game_analysis_position_reports SET state='complete',report_json=?,lease_id=NULL,
                   parent_lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=?
                   WHERE id=? AND state='leased' AND lease_id=? AND request_json=?""",
            (json.dumps(raw_report), _now(), report_id, lease_id, saved["request_json"]),
        ).rowcount
    if not updated:
        with read_connection() as database:
            current = database.execute("SELECT state,report_json FROM game_analysis_position_reports WHERE id=?", (report_id,)).fetchone()
        if current and current["state"] == "complete" and current["report_json"] == json.dumps(raw_report):
            return "complete"
        raise ValueError("Game position lease is stale")
    _release_parent(saved["game_id"], saved["parent_lease_id"])
    return "complete"


def release_position(report_id: str, lease_id: str, error: str | None = None) -> str:
    with connection(background=activity_gate.in_background) as database:
        row = database.execute(
            "SELECT game_id,parent_lease_id,attempts FROM game_analysis_position_reports WHERE id=? AND state='leased' AND lease_id=?",
            (report_id, lease_id),
        ).fetchone()
        if not row:
            return "stale"
        failed = error is not None and row["attempts"] >= MAX_POSITION_ATTEMPTS
        database.execute(
            """UPDATE game_analysis_position_reports SET state=?,lease_id=NULL,parent_lease_id=NULL,
                   lease_expires_at=NULL,last_error=?,updated_at=? WHERE id=?""",
            ("failed" if failed else "queued", error, _now(), report_id),
        )
        if error:
            database.execute(
                "INSERT INTO game_analysis_position_errors(report_id,game_id,error,recorded_at) VALUES(?,?,?,?)",
                (report_id, row["game_id"], error, _now()),
            )
    _release_parent(row["game_id"], row["parent_lease_id"], failed=failed, error=error)
    return "failed" if failed else "queued"


def build_game_evaluations(parent_job: dict) -> list[dict]:
    moves = parent_job["moves"]
    positions = _positions(parent_job["start_fen"], moves)
    with read_connection() as database:
        rows = database.execute(
            """SELECT scan_pass,position_index,report_json FROM game_analysis_position_reports
               WHERE game_id=? AND analysis_version=? AND state='complete'""",
            (parent_job["game_id"], parent_job["analysis_version"]),
        ).fetchall()
    reports = {(row["scan_pass"], row["position_index"]): json.loads(row["report_json"])
               for row in rows}
    shallow = {index: reports[("shallow", index)] for index in range(len(positions))}
    confirmed = _confirmed_indices(positions, moves, shallow, parent_job["divergence_ply"])
    if any(("confirmed", index) not in reports for index in confirmed):
        raise ValueError("Confirmed game positions are incomplete")
    effective = {index: reports.get(("confirmed", index), shallow[index])
                 for index in range(len(positions))}
    evaluations = []
    for ply, move_uci in enumerate(moves):
        before, mate_before = _white_score(positions[ply], effective[ply])
        after, mate_after = _white_score(positions[ply + 1], effective[ply + 1])
        lines = effective[ply].get("lines", [])
        candidates = [{"uci": line["root_move_uci"], "cp": line["score"]["cp"],
                       "mate": line["score"]["mate"], "pv": line["pv_uci"]}
                      for line in lines]
        evaluations.append({
            "ply": ply, "before_cp": before, "after_cp": after,
            "mate_before": mate_before, "mate_after": mate_after,
            "opponent_created_chance": False,
            "depth": 14 if ply in confirmed or ply + 1 in confirmed else 8,
            "position_fen": positions[ply].fen(),
            "best_move_uci": lines[0]["root_move_uci"] if lines else None,
            "principal_variation": lines[0]["pv_uci"] if lines else [],
            "candidate_lines": candidates,
            "mover_color": "white" if positions[ply].turn else "black",
            "is_player_move": positions[ply].turn == (parent_job["color"] == "white"),
            "actual_move_uci": move_uci,
        })
    return evaluations
