"""One foreground tactic ingestion seam; callers own source-specific trust."""

from datetime import date, datetime, timezone
import json
import uuid

import chess
from fastapi import HTTPException

from ..models import TacticCaptureRequest
from ..queue_position_lock import lock_queue_date_for_position
from .cards import card_id
from .review_service import ensure_card_queued_after
from .tactic_admission import lock_daily_tactic_admission


def game_capture_id(finding_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"tempo:tactic-capture:game:{finding_id}")


def validate_tactic_line(starting_fen: str, moves: list[str]) -> tuple[str, list[str], str]:
    try:
        board = chess.Board(starting_fen)
        if not board.is_valid():
            raise ValueError("The starting position is not playable")
        # Retain the supplied valid en-passant field, matching Python card_id.
        normalized_fen = board.fen(en_passant="fen")
        trained_color = "white" if board.turn else "black"
        if not moves:
            raise ValueError("Enter at least one solution move")
        normalized_moves = []
        for index, move_text in enumerate(moves):
            move = chess.Move.from_uci(move_text.strip())
            if move not in board.legal_moves:
                raise ValueError(f"Solution move {index + 1} ({move_text}) is illegal")
            normalized_moves.append(move.uci())
            board.push(move)
        return normalized_fen, normalized_moves, trained_color
    except ValueError as error:
        raise HTTPException(422, f"Cannot capture tactic: {error}") from error


def capture_tactic(database, request: TacticCaptureRequest, *, game_finding: bool = False) -> dict:
    """Atomically materialize one capture; the caller supplies a transaction."""
    starting_fen, moves, trained_color = validate_tactic_line(request.starting_fen, request.moves)
    identity_fen = " ".join(starting_fen.split()[:4])
    normalized_request = {**request.model_dump(mode="json"),
                          "starting_fen": identity_fen, "moves": moves}
    request_json = json.dumps(normalized_request, sort_keys=True, separators=(",", ":"))
    capture_id = str(request.capture_id)
    queue_date = date.today().isoformat()
    lock_daily_tactic_admission(database, queue_date)
    if hasattr(database, "execute_native"):
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"tempo:tactic-capture:{capture_id}",),
        )
    previous = database.execute(
        "SELECT request_json,result_json FROM tactic_captures WHERE id=?", (capture_id,),
    ).fetchone()
    if previous:
        if previous["request_json"] != request_json:
            raise HTTPException(409, "This capture ID belongs to a different position, solution, or source. Start a new capture.")
        return json.loads(previous["result_json"])

    tactic_card_id = card_id(starting_fen, moves)
    repertoire_id = "__game_tactics__" if game_finding else "__captured_tactics__"
    now = datetime.now(timezone.utc).isoformat()
    database.execute(
        "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at,is_main) VALUES(?,?,?,?,0)",
        (repertoire_id, "Game tactics" if game_finding else "Captured tactics",
         "Accepted personal game findings" if game_finding else "User-captured tactical positions", now),
    )
    inserted = database.execute(
        """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
           content_type,scheduling_mode,source_ref,source_fen,trained_color,introduced_at)
           VALUES(?,?,'checkpoint',?,?,'learning',?,'tactic','normal',?,?,?,?)
           ON CONFLICT(id) DO NOTHING""",
        (tactic_card_id, repertoire_id, starting_fen, json.dumps(moves), queue_date,
         request.source_ref if game_finding else None, starting_fen, trained_color, queue_date),
    ).rowcount == 1
    saved_card = database.execute(
        "SELECT * FROM cards WHERE id=?" + (" FOR UPDATE" if hasattr(database, "execute_native") else ""),
        (tactic_card_id,),
    ).fetchone()
    if (saved_card is None or saved_card["content_type"] != "tactic"
            or saved_card["archived"] or saved_card["superseded_by"]
            or saved_card["pending_validation"]):
        raise HTTPException(409, "This position and solution belong to another card type, or an archived, superseded, or unvalidated card. Resolve that card before capturing it.")
    if (" ".join(saved_card["start_fen"].split()[:4]) != identity_fen
            or json.loads(saved_card["moves_json"]) != moves):
        raise HTTPException(409, "The existing card has different content. Repair it before capturing this tactic.")
    if inserted:
        database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
                         (repertoire_id, tactic_card_id))
    database.execute(
        "UPDATE cards SET state='learning',due_date=?,"
        "scheduling_mode=CASE WHEN scheduling_mode='light' THEN 'normal' ELSE scheduling_mode END WHERE id=?",
        (queue_date, tactic_card_id),
    )
    if hasattr(database, "execute_native"):
        lock_queue_date_for_position(database, queue_date)
    reason = {"puzzle_rush": "Captured tactic · Puzzle Rush", "game": "Captured tactic · game",
              "manual": "Captured tactic", "other": "Captured tactic · other"}[request.source_kind]
    ensure_card_queued_after(database, tactic_card_id, 4, "guided", reason)
    database.execute(
        "UPDATE daily_queue SET attempt_state='guided' WHERE queue_date=? AND card_id=? AND status='queued'",
        (queue_date, tactic_card_id),
    )
    result = {"capture_id": capture_id, "card_id": tactic_card_id, "reused": not inserted,
              "queued": True, "introduced": inserted}
    database.execute(
        """INSERT INTO tactic_captures(id,card_id,source_kind,source_ref,source_url,note,captured_at,
           request_json,result_json) VALUES(?,?,?,?,?,?,?,?,?)""",
        (capture_id, tactic_card_id, request.source_kind, request.source_ref, request.source_url,
         request.note, now, request_json, json.dumps(result)),
    )
    return result
