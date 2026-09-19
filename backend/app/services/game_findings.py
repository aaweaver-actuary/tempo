"""Durable, reviewable findings derived from repertoire and engine evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import chess

from ..database import connection
from .tactical_catalog import catalog_status


PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,
}


def _finding_id(game_id: str, analysis_version: int, kind: str, ply: int) -> str:
    return hashlib.sha256(f"{game_id}\0{analysis_version}\0{kind}\0{ply}".encode()).hexdigest()


def _position_before_ply(start_fen: str, moves: list[str], ply: int) -> chess.Board:
    board = chess.Board(start_fen)
    for move_uci in moves[:ply]:
        board.push_uci(move_uci)
    return board


def _classify_motif(board: chess.Board, best_move_uci: str | None, principal_variation: list[str]) -> tuple[str, float, list[str]]:
    if not best_move_uci:
        return "unclassified", 0.0, []
    try:
        move = chess.Move.from_uci(best_move_uci)
        if move not in board.legal_moves:
            return "unclassified", 0.0, []
    except ValueError:
        return "unclassified", 0.0, []
    candidates: list[str] = []
    captured_piece = board.piece_at(move.to_square)
    if captured_piece and not board.attackers(captured_piece.color, move.to_square):
        candidates.append("hangingPiece")
    moved_color = board.turn
    board.push(move)
    moved_piece = board.piece_at(move.to_square)
    if moved_piece:
        valuable_targets = [
            square
            for square in board.attacks(move.to_square)
            if (piece := board.piece_at(square))
            and piece.color != moved_color
            and PIECE_VALUES[piece.piece_type] >= 3
        ]
        if len(valuable_targets) >= 2:
            candidates.append("fork")
    opponent_color = not moved_color
    if any(
        board.piece_at(square)
        and board.piece_at(square).color == opponent_color
        and board.piece_at(square).piece_type != chess.KING
        and board.is_pinned(opponent_color, square)
        for square in chess.SQUARES
    ):
        candidates.append("pin")
    variation_board = board.copy()
    for move_uci in principal_variation[1:]:
        try:
            variation_board.push_uci(move_uci)
        except ValueError:
            break
    if variation_board.is_checkmate():
        losing_king = variation_board.king(variation_board.turn)
        if losing_king is not None and chess.square_rank(losing_king) in {0, 7}:
            candidates.insert(0, "backRankMate")
    return (candidates[0], 0.9, candidates) if candidates else ("unclassified", 0.4, [])


def _upsert_finding(database, *, game_id: str, analysis_version: int, ply: int, kind: str,
                    confidence: float, evidence: dict, repertoire_id: str | None = None,
                    card_id: str | None = None, motif: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    database.execute(
        """INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,repertoire_id,card_id,motif,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET confidence=excluded.confidence,evidence_json=excluded.evidence_json,
           repertoire_id=excluded.repertoire_id,card_id=excluded.card_id,motif=excluded.motif,updated_at=excluded.updated_at""",
        (
            _finding_id(game_id, analysis_version, kind, ply), game_id, analysis_version, ply,
            kind, confidence, json.dumps(evidence), repertoire_id, card_id, motif, now, now,
        ),
    )


def refresh_game_findings(game_id: str | None = None) -> None:
    with connection() as database:
        where = "WHERE g.id=?" if game_id else ""
        games = database.execute(
            f"""SELECT g.*,m.repertoire_id,m.first_player_deviation_ply,m.first_player_deviation_fen,
                       m.first_player_deviation_expected_json,m.first_player_deviation_actual_uci,m.deviation_card_id
                FROM imported_games g LEFT JOIN game_repertoire_matches m ON m.game_id=g.id AND m.is_primary=1 {where}""",
            (game_id,) if game_id else (),
        ).fetchall()
        threshold = database.execute("SELECT major_mistake_cp FROM settings WHERE id=1").fetchone()[0]
        for game in games:
            version = max(1, int(game["analysis_version"]))
            moves = json.loads(game["moves_json"])
            deviation_ply = game["first_player_deviation_ply"]
            if deviation_ply is not None:
                _upsert_finding(
                    database, game_id=game["id"], analysis_version=version, ply=deviation_ply,
                    kind="repertoire lapse", confidence=1.0,
                    evidence={
                        "fen": game["first_player_deviation_fen"],
                        "expected": json.loads(game["first_player_deviation_expected_json"] or "[]"),
                        "actual": game["first_player_deviation_actual_uci"],
                    }, repertoire_id=game["repertoire_id"], card_id=game["deviation_card_id"],
                )
            analysis_rows = database.execute(
                "SELECT * FROM game_move_analysis WHERE game_id=? ORDER BY ply", (game["id"],)
            ).fetchall()
            first_big_mistake = next((row for row in analysis_rows if row["loss_cp"] >= threshold), None)
            for row in analysis_rows:
                if row["loss_cp"] < threshold:
                    continue
                evidence = {
                    "fen": _position_before_ply(game["start_fen"], moves, row["ply"]).fen(),
                    "loss_cp": row["loss_cp"], "best_move_uci": row["best_move_uci"],
                    "principal_variation": json.loads(row["principal_variation_json"] or "[]"),
                    "eval_before_cp": row["eval_before_cp"], "eval_after_cp": row["eval_after_cp"],
                    "mate_before": row["mate_before"], "mate_after": row["mate_after"],
                }
                kind = "blunder" if row["loss_cp"] >= 250 or (row["mate_before"] is not None) != (row["mate_after"] is not None) else "major mistake"
                _upsert_finding(database, game_id=game["id"], analysis_version=version, ply=row["ply"], kind=kind, confidence=1.0, evidence=evidence)
                board = _position_before_ply(game["start_fen"], moves, row["ply"])
                motif, confidence, candidates = _classify_motif(board, row["best_move_uci"], evidence["principal_variation"])
                _upsert_finding(
                    database, game_id=game["id"], analysis_version=version, ply=row["ply"],
                    kind="tactical miss", confidence=confidence,
                    evidence={**evidence, "candidate_motifs": candidates}, motif=motif,
                )
            if first_big_mistake:
                evidence = {
                    "fen": _position_before_ply(game["start_fen"], moves, first_big_mistake["ply"]).fen(),
                    "loss_cp": first_big_mistake["loss_cp"],
                    "best_move_uci": first_big_mistake["best_move_uci"],
                    "principal_variation": json.loads(first_big_mistake["principal_variation_json"] or "[]"),
                }
                _upsert_finding(database, game_id=game["id"], analysis_version=version,
                                ply=first_big_mistake["ply"], kind="first big mistake",
                                confidence=1.0, evidence=evidence)


def motif_recommendations() -> list[dict]:
    with connection() as database:
        eligible_games = [row[0] for row in database.execute(
            "SELECT id FROM imported_games WHERE adaptive_excluded=0 ORDER BY played_at DESC LIMIT 30"
        )]
        if not eligible_games:
            return []
        placeholders = ",".join("?" for _ in eligible_games)
        rows = database.execute(
            f"""SELECT * FROM game_findings WHERE game_id IN ({placeholders}) AND kind='tactical miss'
                  AND confidence>=0.8 AND motif!='unclassified' AND status NOT IN ('ignored','excluded')""",
            eligible_games,
        ).fetchall()
        grouped: dict[str, dict] = {}
        for row in rows:
            evidence = json.loads(row["evidence_json"])
            item = grouped.setdefault(row["motif"], {"motif": row["motif"], "miss_count": 0, "total_loss_cp": 0, "supporting_games": []})
            item["miss_count"] += 1
            item["total_loss_cp"] += int(evidence.get("loss_cp", 0))
            if row["game_id"] not in item["supporting_games"]:
                item["supporting_games"].append(row["game_id"])
        catalog = catalog_status(database)
        recommendations = []
        database.execute("DELETE FROM game_insight_recommendations")
        now = datetime.now(timezone.utc).isoformat()
        for item in grouped.values():
            if item["miss_count"] < 3:
                continue
            pack = next((pack for pack in catalog["packs"] if pack["id"].startswith(f"{item['motif']}-") and not pack["active"] and pack["introduced"] < pack["count"]), None)
            item["recommended_pack_id"] = pack["id"] if pack else None
            database.execute(
                "INSERT INTO game_insight_recommendations VALUES(?,?,?,?,?,?)",
                (item["motif"], item["miss_count"], item["total_loss_cp"], json.dumps(item["supporting_games"]), item["recommended_pack_id"], now),
            )
            recommendations.append(item)
        recommendations.sort(key=lambda item: (-item["miss_count"], -item["total_loss_cp"], item["motif"]))
        return recommendations
