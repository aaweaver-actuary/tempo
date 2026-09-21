"""Versioned, idempotent gameplay events derived from one engine pass."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import chess

from ..database import connection
from .activity_gate import activity_gate
from .motif_detectors import classify_candidate_lines, select_primary_motif


CLASSIFIER_VERSION = 1
EQUIVALENT_MOVE_WINDOW_CP = 50
TACTICAL_CREATION_THRESHOLD_CP = 100


def _event_id(game_id: str, analysis_version: int, ply: int, kind: str) -> str:
    identity = f"{game_id}\0{analysis_version}\0{CLASSIFIER_VERSION}\0{ply}\0{kind}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _position_before_ply(start_fen: str, moves: list[str], ply: int) -> chess.Board:
    board = chess.Board(start_fen)
    for move_uci in moves[:ply]:
        board.push_uci(move_uci)
    return board


def refresh_gameplay_events(game_id: str, *, background: bool = False) -> None:
    """Compute outside a transaction and replace only this version in one short write."""
    with connection(background=background) as database:
        game = database.execute(
            """SELECT id,color,start_fen,moves_json,analysis_version,analysis_evidence_version
                  FROM imported_games WHERE id=?""",
            (game_id,),
        ).fetchone()
        if not game:
            return
        analysis_rows = database.execute(
            "SELECT * FROM game_move_analysis WHERE game_id=? ORDER BY ply",
            (game_id,),
        ).fetchall()
        candidate_rows = database.execute(
            "SELECT * FROM game_move_analysis_candidates WHERE game_id=? ORDER BY ply,rank",
            (game_id,),
        ).fetchall()
        settings = database.execute(
            "SELECT major_mistake_cp,engine_line_window_cp FROM settings WHERE id=1"
        ).fetchone()
    moves = json.loads(game["moves_json"])
    analysis_version = max(1, int(game["analysis_version"]))
    missed_opportunity_threshold_cp = int(settings["major_mistake_cp"])
    acceptable_tolerance_cp = int(settings["engine_line_window_cp"])
    rows_by_ply = {int(row["ply"]): row for row in analysis_rows}
    candidate_lines_by_ply: dict[int, list[dict]] = {}
    for candidate in candidate_rows:
        candidate_lines_by_ply.setdefault(int(candidate["ply"]), []).append(
            {
                "uci": candidate["candidate_uci"],
                "cp": candidate["score_cp"],
                "mate": candidate["mate"],
                "score": candidate["score_text"],
                "pv": json.loads(candidate["principal_variation_json"] or "[]"),
                "depth": candidate["depth"],
                "engine_version": candidate["engine_version"],
                "network_version": candidate["network_version"],
            }
        )
    computed_events: list[tuple] = []
    now = datetime.now(timezone.utc).isoformat()

    for row in analysis_rows:
        candidate_lines = candidate_lines_by_ply.get(int(row["ply"]), [])
        if candidate_lines:
            if not bool(row["is_player_move"]):
                continue
            opportunity_board = _position_before_ply(game["start_fen"], moves, int(row["ply"]))
            motif_evidence = classify_candidate_lines(
                opportunity_board,
                row["actual_move_uci"],
                candidate_lines,
                best_move_uci=row["best_move_uci"],
                principal_variation=json.loads(row["principal_variation_json"] or "[]"),
                actual_after_cp=row["eval_after_cp"],
                mover_color=row["mover_color"],
            )
            primary_motif = select_primary_motif(motif_evidence)
            if primary_motif is None:
                continue
            motif = primary_motif.motif
            confidence = primary_motif.confidence
            motifs = [item.motif for item in motif_evidence]
            acceptable_moves = {candidate["uci"] for candidate in candidate_lines}
            acceptable = (
                row["actual_move_uci"] in acceptable_moves
                or int(row["loss_cp"] or 0) <= acceptable_tolerance_cp
            )
            loss_cp = int(row["loss_cp"] or 0)
            outcome = (
                "exploited"
                if acceptable
                else "missed"
                if loss_cp >= missed_opportunity_threshold_cp
                else "unconverted"
            )
            event_ply = int(row["ply"])
            evidence = {
                "fen": opportunity_board.fen(),
                "candidate_motifs": motifs,
                "motif_evidence": [item.to_dict() for item in motif_evidence],
                "candidate_lines": candidate_lines,
                "opportunity_source": "decision position",
                "outcome": outcome,
                "loss_cp": loss_cp,
                "thresholds": {
                    "missed_opportunity_loss_cp": missed_opportunity_threshold_cp,
                    "acceptable_move_tolerance_cp": acceptable_tolerance_cp,
                },
                "analysis_version": analysis_version,
                "analysis_evidence_version": game["analysis_evidence_version"],
            }
            computed_events.append(
                (
                    _event_id(game_id, analysis_version, event_ply, "tactical opportunity"),
                    game_id,
                    analysis_version,
                    CLASSIFIER_VERSION,
                    event_ply,
                    "tactical opportunity",
                    motif,
                    row["mover_color"],
                    row["mover_color"],
                    outcome,
                    confidence,
                    loss_cp,
                    row["best_move_uci"],
                    row["actual_move_uci"],
                    json.dumps(json.loads(row["principal_variation_json"] or "[]")),
                    json.dumps(evidence),
                    now,
                    now,
                )
            )
            continue
        # A legacy analysis has no candidate table. Keep its existing opponent
        # blunder -> response event semantics until it is tactically backfilled.
        if candidate_lines_by_ply:
            continue
        loss_cp = int(row["loss_cp"] or 0)
        if loss_cp < TACTICAL_CREATION_THRESHOLD_CP:
            continue
        mistake_ply = int(row["ply"])
        created_by_color = row["mover_color"]
        if created_by_color not in {"white", "black"}:
            continue
        beneficiary_color = "black" if created_by_color == "white" else "white"
        response_row = rows_by_ply.get(mistake_ply + 1)
        opportunity_board = _position_before_ply(game["start_fen"], moves, mistake_ply + 1)
        best_move_uci = response_row["best_move_uci"] if response_row else None
        principal_variation = (
            json.loads(response_row["principal_variation_json"] or "[]")
            if response_row
            else []
        )
        motif_evidence = classify_candidate_lines(
            opportunity_board,
            response_row["actual_move_uci"] if response_row else None,
            [],
            best_move_uci=best_move_uci,
            principal_variation=principal_variation,
            actual_after_cp=response_row["eval_after_cp"] if response_row else None,
            mover_color=response_row["mover_color"] if response_row else None,
        )
        primary_motif = select_primary_motif(motif_evidence)
        motif = primary_motif.motif if primary_motif else "unclassified"
        confidence = primary_motif.confidence if primary_motif else 0.0
        candidates = [item.motif for item in motif_evidence]
        opportunity_taken = bool(
            response_row
            and int(response_row["loss_cp"] or 0) <= EQUIVALENT_MOVE_WINDOW_CP
        )
        if beneficiary_color == game["color"]:
            outcome = "found" if opportunity_taken else "missed"
        else:
            outcome = "punished" if opportunity_taken else "escaped"
        evidence = {
            "fen": opportunity_board.fen(),
            "candidate_motifs": candidates,
            "motif_evidence": [item.to_dict() for item in motif_evidence],
            "source_mistake_ply": mistake_ply,
            "source_loss_cp": loss_cp,
            "response_loss_cp": int(response_row["loss_cp"] or 0) if response_row else None,
            "player_was_beneficiary": beneficiary_color == game["color"],
        }
        computed_events.append(
            (
                _event_id(game_id, analysis_version, mistake_ply, "tactical opportunity"),
                game_id,
                analysis_version,
                CLASSIFIER_VERSION,
                mistake_ply + 1,
                "tactical opportunity",
                motif,
                beneficiary_color,
                created_by_color,
                outcome,
                confidence,
                loss_cp,
                best_move_uci,
                response_row["actual_move_uci"] if response_row else None,
                json.dumps(principal_variation),
                json.dumps(evidence),
                now,
                now,
            )
        )
        if motif == "hangingPiece":
            hanging_outcome = "captured" if opportunity_taken else "left available"
            computed_events.append(
                (
                    _event_id(game_id, analysis_version, mistake_ply, "hanging piece"),
                    game_id,
                    analysis_version,
                    CLASSIFIER_VERSION,
                    mistake_ply + 1,
                    "hanging piece",
                    motif,
                    beneficiary_color,
                    created_by_color,
                    hanging_outcome,
                    confidence,
                    loss_cp,
                    best_move_uci,
                    response_row["actual_move_uci"] if response_row else None,
                    json.dumps(principal_variation),
                    json.dumps(evidence),
                    now,
                    now,
                )
            )

    if background:
        activity_gate.wait_for_foreground()
    with connection(background=background) as database:
        database.execute(
            "DELETE FROM gameplay_events WHERE game_id=? AND classifier_version=?",
            (game_id, CLASSIFIER_VERSION),
        )
        database.executemany(
            """INSERT INTO gameplay_events(
                   id,game_id,analysis_version,classifier_version,ply,kind,motif,
                   beneficiary_color,created_by_color,outcome,confidence,loss_cp,
                   best_move_uci,actual_move_uci,principal_variation_json,evidence_json,
                   created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            computed_events,
        )
