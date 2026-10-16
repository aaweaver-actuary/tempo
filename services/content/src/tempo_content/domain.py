"""Pure Content identity, legal inventory and bounded exercise validation."""

from __future__ import annotations

import hashlib
import json
from typing import Any
import uuid

import chess
from tempo_chess_core import canonical_fen_key
from tempo_content_publication_contracts.v1 import ExecutableExercise, Publication

from .models import SourceExercise, SourceSnapshot


INVENTORY_VERSION = 1
SEGMENT_PLIES = 16


class ContentError(ValueError):
    """An actionable domain failure suitable for the supported CLI boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def stable_id(workspace_id: str, source_key: str, kind: str, key: str) -> str:
    """Derive a public ID from explicit stable source identity, never ordering."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                         _canonical_json(["tempo-content", 1, workspace_id, source_key, kind, key])))


def canonical_source(snapshot: SourceSnapshot) -> str:
    """Canonicalize unordered source entities while preserving executable order."""
    payload = snapshot.model_dump(mode="json")
    for collection in ("repertoires", "policies", "routes", "memberships", "exercises", "bindings"):
        payload[collection] = sorted(payload[collection], key=_canonical_json)
    return _canonical_json(payload)


def _valid_board(fen: str) -> chess.Board:
    try:
        board = chess.Board(fen)
    except (ValueError, TypeError) as error:
        raise ContentError("invalid_fen", "Invalid chess position FEN") from error
    if not board.is_valid():
        raise ContentError("invalid_fen", "Invalid chess position FEN")
    return board


def canonical_position(fen: str) -> str:
    """Legal-state identity; exact exercise FEN remains separately preserved."""
    board = _valid_board(fen)
    return canonical_fen_key(board.fen(en_passant="legal"))


def inventory_segment(start_fen: str, moves: list[str], learner_color: str,
                      first_ply: int = 0, terminal: bool = True) -> tuple[list[dict], str]:
    """Traverse an authored slice and enumerate complete legal opponent support.

    The worker owns the 16-ply slice limit. This pure helper also supports a
    complete reference traversal for equivalence tests; it never opens storage.
    """
    if learner_color not in ("white", "black"):
        raise ContentError("invalid_learner_color", "Learner color must be white or black")
    if type(first_ply) is not int or first_ply < 0:
        raise ContentError("invalid_ply", "Starting ply must be a nonnegative integer")
    board = _valid_board(start_fen)
    learner_turn = learner_color == "white"
    occurrences = []
    for local_ply in range(len(moves) + int(terminal)):
        authored_move = moves[local_ply] if local_ply < len(moves) else None
        parsed_move = None
        if authored_move is not None:
            try:
                parsed_move = chess.Move.from_uci(authored_move)
            except (ValueError, TypeError) as error:
                raise ContentError("illegal_route_move",
                                   f"Illegal authored move at ply {first_ply + local_ply}: {authored_move}") from error
            if parsed_move not in board.legal_moves:
                raise ContentError("illegal_route_move",
                                   f"Illegal authored move at ply {first_ply + local_ply}: {authored_move}")
        opponent = board.turn != learner_turn
        legal_replies = []
        if opponent:
            for legal_move in sorted(board.legal_moves, key=lambda move: move.uci()):
                reply_board = board.copy(stack=False)
                reply_board.push(legal_move)
                legal_replies.append({"move_uci": legal_move.uci(),
                                      "resulting_fen_key": canonical_fen_key(reply_board.fen(en_passant="legal"))})
        occurrences.append({"ply": first_ply + local_ply, "fen": board.fen(),
                            "fen_key": canonical_fen_key(board.fen(en_passant="legal")),
                            "opponent": opponent,
                            "authored_reply": authored_move if opponent else None,
                            "authored_move": authored_move, "legal_replies": legal_replies})
        if parsed_move is not None:
            board.push(parsed_move)
    return occurrences, board.fen()


def validate_exercise_step(exercise: SourceExercise, state: dict | None = None) -> dict | None:
    """Validate at most 16 accepted-line plies; return a durable JSON cursor.

    The cursor is internal trusted worker state, fenced by the full immutable
    exercise digest. None means complete. Unreachable knight problems remain
    valid: their supported answer can be that no route exists.
    """
    starting_board = _valid_board(exercise.starting_fen)
    specification = exercise.specification
    if specification.type != "move_line":
        if state is not None:
            raise ContentError("invalid_validation_checkpoint", "Unexpected exercise validation checkpoint")
        if specification.type == "knight_path":
            start = chess.parse_square(specification.start_square)
            piece = starting_board.piece_at(start)
            if piece is None or piece.piece_type != chess.KNIGHT:
                raise ContentError("invalid_knight_start", "Knight-path starting square must contain a knight")
            if len(set(specification.target_squares)) != len(specification.target_squares):
                raise ContentError("duplicate_knight_targets", "Knight-path target squares must be distinct")
        return None

    if specification.mode == "single" and any(len(line) != 1 for line in specification.accepted_lines):
        raise ContentError("invalid_move_mode", "Single-move exercises require one move in every accepted line")
    digest = hashlib.sha256(_canonical_json(exercise.model_dump(mode="json")).encode()).hexdigest()
    line_index = 0
    move_index = 0
    board = starting_board
    if state is not None:
        if (set(state) != {"exercise_digest", "line_index", "move_index", "fen"}
                or state.get("exercise_digest") != digest
                or type(state.get("line_index")) is not int
                or type(state.get("move_index")) is not int):
            raise ContentError("invalid_validation_checkpoint", "Exercise validation checkpoint does not match this exercise")
        line_index, move_index = state["line_index"], state["move_index"]
        if (not 0 <= line_index < len(specification.accepted_lines)
                or not 0 <= move_index < len(specification.accepted_lines[line_index])):
            raise ContentError("invalid_validation_checkpoint", "Exercise validation checkpoint is out of bounds")
        board = _valid_board(state["fen"])

    checked_plies = 0
    while line_index < len(specification.accepted_lines) and checked_plies < SEGMENT_PLIES:
        line = specification.accepted_lines[line_index]
        move_text = line[move_index]
        move = chess.Move.from_uci(move_text)
        if move not in board.legal_moves:
            raise ContentError("illegal_exercise_move",
                               f"Illegal accepted move in exercise {exercise.key}, line {line_index}, ply {move_index}: {move_text}")
        board.push(move)
        move_index += 1
        checked_plies += 1
        if move_index == len(line):
            line_index += 1
            move_index = 0
            board = starting_board.copy(stack=False)
    if line_index == len(specification.accepted_lines):
        return None
    return {"exercise_digest": digest, "line_index": line_index,
            "move_index": move_index, "fen": board.fen()}


def build_publication(snapshot: SourceSnapshot, source_revision: int, generation: int,
                      built_at: str, exercise_revisions: dict[str, int]) -> Publication:
    """Create the accepted shared v1 type after durable validation completes.

    This pure constructor does not imply Learning acceptance or activation and
    does not repeat the worker's potentially large chess-validation traversal.
    """
    if not snapshot.exercises:
        raise ContentError("retirement_required", "An empty source requires the separate retirement protocol")
    if set(exercise_revisions) != {exercise.key for exercise in snapshot.exercises}:
        raise ContentError("exercise_revision_mismatch", "Exercise revisions must exactly cover the source exercises")
    publication_id = stable_id(snapshot.workspace_id, snapshot.source_key, "publication", "source")
    definitions = []
    for exercise in sorted(snapshot.exercises, key=lambda item: item.key):
        definitions.append(ExecutableExercise(
            card_id=stable_id(snapshot.workspace_id, snapshot.source_key, "card", exercise.key),
            decision_id=stable_id(snapshot.workspace_id, snapshot.source_key, "decision",
                                  _canonical_json([exercise.policy_key, exercise.decision_key])),
            policy_id=stable_id(snapshot.workspace_id, snapshot.source_key, "policy", exercise.policy_key),
            source_revision=source_revision, publication_id=publication_id,
            publication_generation=generation, exercise_revision=exercise_revisions[exercise.key],
            starting_fen=exercise.starting_fen, prompt=exercise.prompt,
            specification=exercise.specification,
        ))
    return Publication(contract_version=1, workspace_id=snapshot.workspace_id,
                       publication_id=publication_id, source_revision=source_revision,
                       publication_generation=generation, built_at=built_at, definitions=definitions)


__all__ = ["ContentError", "INVENTORY_VERSION", "SEGMENT_PLIES", "stable_id",
           "canonical_source", "canonical_position", "inventory_segment",
           "validate_exercise_step", "build_publication"]
