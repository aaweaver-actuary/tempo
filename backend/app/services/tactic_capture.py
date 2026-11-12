"""One foreground tactic ingestion seam; callers own source-specific trust."""

from app.study.policy.clock import StudyCalendar
import json
import uuid

import chess

from app.contracts.transport.tactics import TacticCaptureRequest
from tempo_storage.tactics import captures as storage
from app.contracts.tactic_capture import (
    ValidatedCapture,
    TacticCaptureValidationError,
    TacticCaptureConflict,
)
from app.study.interfaces.captured_tactic import CapturedTacticAdmission
from app.chess.cards import card_id


def game_capture_id(finding_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"tempo:tactic-capture:game:{finding_id}")


def validate_tactic_line(
    starting_fen: str, moves: list[str]
) -> tuple[str, list[str], str]:
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
        raise TacticCaptureValidationError(f"Cannot capture tactic: {error}") from error


def capture_tactic(
    database,
    request: TacticCaptureRequest,
    *,
    admission: CapturedTacticAdmission,
    calendar: StudyCalendar,
    game_finding: bool = False,
) -> dict:
    """Atomically materialize one capture; the caller supplies a transaction."""
    starting_fen, moves, trained_color = validate_tactic_line(
        request.starting_fen, request.moves
    )
    identity_fen = " ".join(starting_fen.split()[:4])
    normalized_request = {
        **request.model_dump(mode="json"),
        "starting_fen": identity_fen,
        "moves": moves,
    }
    request_json = json.dumps(normalized_request, sort_keys=True, separators=(",", ":"))
    capture_id = str(request.capture_id)
    queue_date = calendar.day.isoformat()
    admission.lock_daily(queue_date)
    storage.lock_capture(database, capture_id)
    previous = storage.capture_receipt(database, capture_id)
    if previous:
        if previous["request_json"] != request_json:
            raise TacticCaptureConflict(
                "This capture ID belongs to a different position, solution, or source. Start a new capture."
            )
        return json.loads(previous["result_json"])

    tactic_card_id = card_id(starting_fen, moves)
    capture = ValidatedCapture(
        capture_id=capture_id,
        card_id=tactic_card_id,
        starting_fen=starting_fen,
        identity_fen=identity_fen,
        moves=tuple(moves),
        trained_color=trained_color,
        source_kind=request.source_kind,
        source_ref=request.source_ref,
        source_url=request.source_url,
        note=request.note,
        game_finding=game_finding,
        request_json=request_json,
    )
    now = calendar.observed_at.isoformat()
    admitted = admission.admit(capture, queue_date, now)
    result = {
        "capture_id": capture_id,
        "card_id": admitted["card_id"],
        "reused": admitted["reused"],
        "queued": True,
        "introduced": admitted["introduced"],
    }
    storage.save_capture_receipt(
        database,
        capture_id,
        tactic_card_id,
        request.source_kind,
        request.source_ref,
        request.source_url,
        request.note,
        now,
        request_json,
        json.dumps(result),
    )
    return result
