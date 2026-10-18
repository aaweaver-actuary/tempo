"""Pure, presentation-independent opening identities and advisory segmentation."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import chess
from ._json import CanonicalObject

POSITION_VERSION = 1
POLICY_VERSION = 1
RECOMMENDATION_VERSION = 1
MAX_PRESENTATION_DECISIONS = 20


def stable_key(*components: Any) -> str:
    return hashlib.sha256(json.dumps(components, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def opening_position_key(fen: str) -> str:
    return " ".join(chess.Board(fen).fen(en_passant="legal").split()[:4])


def decision_identity(repertoire_id: str, fen: str, trained_color: str,
                      expected_uci: str, policy_version: int = POLICY_VERSION) -> str:
    if trained_color not in {"white", "black"}:
        raise ValueError("Unknown trained color")
    return stable_key("opening-decision", POSITION_VERSION, opening_position_key(fen),
                      repertoire_id, trained_color, expected_uci, policy_version)


def presentation_occurrences(repertoire_id: str, presentation: dict) -> tuple[dict, ...]:
    """Traverse one bounded published presentation exactly once; never mutate it."""
    board = chess.Board(presentation["start_fen"])
    trained_color = presentation["trained_color"]
    if trained_color not in {"white", "black"}:
        raise ValueError("Unknown trained color")
    trained_turn = trained_color == "white"
    moves = tuple(json.loads(presentation["moves_json"]))
    if len(moves) > MAX_PRESENTATION_DECISIONS * 2:
        raise ValueError("Presentation exceeds the bounded segmentation limit")
    occurrences: list[dict] = []
    full_move_fens: list[str] = [board.fen()]
    for move_offset, move_uci in enumerate(moves):
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal repertoire move {move_uci}")
        if board.turn == trained_turn:
            occurrences.append({
                "decision_id": decision_identity(repertoire_id, board.fen(), trained_color, move_uci),
                "decision_index": len(occurrences), "move_offset": move_offset,
                "decision_fen": board.fen(), "expected_uci": move_uci,
            })
        board.push(move)
        full_move_fens.append(board.fen())
    if not occurrences or board.turn == trained_turn:
        raise ValueError("Presentation must end on a learner decision")
    for occurrence in occurrences:
        decision_index = occurrence["decision_index"]
        move_offset = occurrence["move_offset"]
        cut_offset = move_offset + 1
        occurrence.update({
            "card_id": presentation["id"], "revision": int(presentation["revision"]),
            "trained_color": trained_color, "start_fen": presentation["start_fen"],
            "moves": moves, "decision_count": len(occurrences),
            "ending_fen": full_move_fens[-1], "cut_fen": full_move_fens[cut_offset],
            "prefix_moves": moves[:cut_offset], "branch_moves": moves[cut_offset:],
            "bridge_moves": moves[:(occurrences[decision_index - 1]["move_offset"] + 1)] if decision_index else (),
            "bridge_end_fen": full_move_fens[occurrences[decision_index - 1]["move_offset"] + 1] if decision_index else presentation["start_fen"],
            "suffix_moves": moves[move_offset:],
            "suffix_decision_ids": tuple(item["decision_id"] for item in occurrences[decision_index:]),
            "incoming_key": stable_key(opening_position_key(presentation["start_fen"]), moves[:move_offset]),
            "next_context": moves[cut_offset:cut_offset + 2],
            "trunk_key": stable_key(opening_position_key(presentation["start_fen"]), trained_color, moves[:cut_offset]),
        })
        occurrence["suffix_key"] = stable_key(occurrence["suffix_decision_ids"])
    return tuple(PresentationOccurrence(item) for item in occurrences)



class PresentationOccurrence(CanonicalObject):
    """Immutable mapping of the existing bounded presentation occurrence fields."""
