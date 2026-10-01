"""Read-only previews and receipt-backed saves of cards from game findings."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from typing import Any

import chess
from fastapi import HTTPException

from .command_gateway import register_command
from .models import GameFindingCardRequest, TacticCaptureRequest
from .postgres_store import PostgresConnection
from .queue_position_lock import lock_queue_date_for_position
from .services.cards import card_id
from .services.review_service import ensure_card_queued_after
from .services.tactic_capture import capture_tactic, game_capture_id, validate_tactic_line
from .queue_commands import request_queue_refresh_in_transaction


def _finding_card_inputs(database, finding_id: str, request: GameFindingCardRequest,
                         *, lock: bool) -> tuple[dict, dict, str, list[str], str | None]:
    if lock:
        source = database.execute(
            "SELECT game_id,source_opportunity_id FROM game_findings WHERE id=? FOR UPDATE",
            (finding_id,),
        ).fetchone()
        if source is not None:
            database.execute(
                "SELECT id FROM imported_games WHERE id=? FOR UPDATE", (source["game_id"],),
            )
            if source["source_opportunity_id"]:
                database.execute(
                    "SELECT id FROM tactical_opportunities WHERE id=? FOR UPDATE",
                    (source["source_opportunity_id"],),
                )
    finding = database.execute(
        """SELECT f.*,g.color,g.adaptive_excluded,g.analysis_version AS game_analysis_version,
                  o.active AS opportunity_active,o.analysis_version AS opportunity_analysis_version,
                  o.accepted_moves_json,o.evidence_json AS opportunity_evidence_json
           FROM game_findings f JOIN imported_games g ON g.id=f.game_id
           LEFT JOIN tactical_opportunities o ON o.id=f.source_opportunity_id WHERE f.id=?""",
        (finding_id,),
    ).fetchone()
    if finding is None:
        raise HTTPException(404, "Gameplay finding not found")
    if finding["kind"] not in {"first big mistake", "repertoire gap", "tactical miss"}:
        raise HTTPException(422, "This finding cannot create a study card")
    if finding["adaptive_excluded"]:
        raise HTTPException(409, "This game is excluded from adaptation")
    evidence = json.loads(finding["evidence_json"])
    if finding["kind"] == "tactical miss":
        if (not finding["source_opportunity_id"] or not finding["opportunity_active"]
                or finding["opportunity_analysis_version"] != finding["game_analysis_version"]):
            raise HTTPException(409, "This tactical opportunity is stale and must be re-analyzed")
        if float(finding["confidence"]) < 0.8:
            raise HTTPException(422, "This tactical miss does not meet the confidence threshold")
        opportunity_evidence = json.loads(finding["opportunity_evidence_json"] or "{}")
        evidence = {**opportunity_evidence, **evidence}
    starting_fen = request.starting_fen or evidence.get("fen")
    default_line = evidence.get("principal_variation", [])
    if not default_line:
        default_line = (evidence.get("candidate_lines") or [{}])[0].get("pv", [])
    moves = request.moves or default_line[:6]
    if finding["kind"] == "tactical miss":
        accepted_moves = set(json.loads(finding["accepted_moves_json"] or "[]"))
        if not accepted_moves:
            raise HTTPException(422, "The tactical opportunity has no accepted conversion")
        if not moves or moves[0] not in accepted_moves:
            raise HTTPException(422, "The solution must begin with an accepted tactical conversion")
        if len(moves) < 2:
            raise HTTPException(422, "The tactical solution is too short to demonstrate the payoff")
    if not starting_fen or not moves:
        raise HTTPException(422, "The finding has no legal study line")
    try:
        board = chess.Board(starting_fen)
        trained_color = request.trained_color or finding["color"]
        if finding["kind"] == "tactical miss" and (board.turn == chess.WHITE) != (trained_color == "white"):
            raise ValueError("trained color is not on move")
        normalized_moves = []
        for move_uci in moves[:6]:
            move = chess.Move.from_uci(move_uci)
            if move not in board.legal_moves:
                raise ValueError("illegal move")
            normalized_moves.append(move.uci())
            board.push(move)
    except ValueError as error:
        raise HTTPException(422, "The proposed study line contains an illegal move") from error
    normalized_fen = chess.Board(starting_fen).fen()
    if finding["kind"] == "tactical miss":
        normalized_fen, normalized_moves, trained_color = validate_tactic_line(starting_fen, normalized_moves)
        existing = database.execute(
            "SELECT id FROM cards WHERE id=? AND content_type='tactic' AND archived=0 AND superseded_by IS NULL",
            (card_id(normalized_fen, normalized_moves),),
        ).fetchone()
    else:
        existing = database.execute(
            "SELECT id FROM cards WHERE content_type='middlegame' AND source_fen=? AND moves_json=? AND archived=0",
            (normalized_fen, json.dumps(normalized_moves)),
        ).fetchone()
    existing_card_id = existing["id"] if existing else None
    preview = {
        "starting_fen": starting_fen, "moves": normalized_moves,
        "best_move": normalized_moves[0], "trained_color": trained_color,
        "existing_card_id": existing_card_id,
    }
    return dict(finding), preview, normalized_fen, normalized_moves, existing_card_id


def preview_finding_card(database, finding_id: str, request: GameFindingCardRequest) -> dict:
    _finding, preview, _fen, _moves, _existing = _finding_card_inputs(
        database, finding_id, request, lock=False,
    )
    return {"preview": preview, "saved": False}


def save_finding_card(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    finding_id = str(payload["finding_id"])
    request = GameFindingCardRequest.model_validate(payload["request"])
    if not request.save:
        raise HTTPException(422, "Save command requires save=true")
    finding, preview, normalized_fen, normalized_moves, existing_card_id = (
        _finding_card_inputs(database, finding_id, request, lock=hasattr(database, "execute_native"))
    )
    created_at = datetime.now(timezone.utc).isoformat()
    if finding["kind"] == "tactical miss":
        captured = capture_tactic(database, TacticCaptureRequest(
            capture_id=game_capture_id(finding_id), starting_fen=preview["starting_fen"],
            moves=normalized_moves, source_kind="game", source_ref=finding_id,
        ), game_finding=True)
        database.execute(
            "UPDATE game_findings SET card_id=?,status='accepted',updated_at=? WHERE id=?",
            (captured["card_id"], created_at, finding_id),
        )
        if hasattr(database, "execute_native"):
            request_queue_refresh_in_transaction(database, date.today().isoformat())
        return {"preview": {**preview, "existing_card_id": captured["card_id"]},
                "saved": True, "card_id": captured["card_id"], "reused": captured["reused"]}
    repertoire_id = "__game_mistakes__"
    repertoire_name = "Game mistakes"
    database.execute(
        """INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at,is_main)
           VALUES(?,?,?, ?,0)""",
        (repertoire_id, repertoire_name, "Accepted personal game findings", created_at),
    )
    study_card_id = existing_card_id or card_id(preview["starting_fen"], normalized_moves)
    reused = existing_card_id is not None
    if existing_card_id is None:
        inserted_card = database.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
               content_type,source_ref,source_fen,trained_color,introduced_at)
               VALUES(?,?,?,?,?,'learning',?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
            (study_card_id, repertoire_id, "checkpoint", preview["starting_fen"],
             json.dumps(normalized_moves), date.today().isoformat(),
             "middlegame",
             finding_id, normalized_fen, preview["trained_color"], date.today().isoformat()),
        )
        reused = inserted_card.rowcount == 0
        database.execute(
            "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?) ON CONFLICT DO NOTHING",
            (repertoire_id, study_card_id),
        )
    saved_card = database.execute(
        "SELECT archived,content_type,source_fen,moves_json FROM cards WHERE id=?"
        + (" FOR UPDATE" if hasattr(database, "execute_native") else ""),
        (study_card_id,),
    ).fetchone()
    expected_content_type = "middlegame"
    if (saved_card is None or saved_card["archived"]
            or saved_card["content_type"] != expected_content_type
            or saved_card["source_fen"] != normalized_fen
            or saved_card["moves_json"] != json.dumps(normalized_moves)):
        raise HTTPException(409, "The study card changed while this save was pending")
    if hasattr(database, "execute_native"):
        lock_queue_date_for_position(database, date.today().isoformat())
    ensure_card_queued_after(database, study_card_id, 4)
    database.execute(
        "UPDATE game_findings SET card_id=?,status='accepted',updated_at=? WHERE id=?",
        (study_card_id, created_at, finding_id),
    )
    return {"preview": {**preview, "existing_card_id": study_card_id},
            "saved": True, "card_id": study_card_id, "reused": reused}


register_command("game_findings.card.save", save_finding_card)
