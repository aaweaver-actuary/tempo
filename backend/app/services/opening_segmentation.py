"""Pure, presentation-independent opening identities and advisory segmentation."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import chess

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
    return tuple(occurrences)


def segment_preview(role: str, starting_fen: str, ending_fen: str, moves: tuple,
                    decision_count: int, trained_color: str) -> dict:
    return {"id": stable_key(role, opening_position_key(starting_fen), moves, trained_color),
            "role": role, "starting_fen": starting_fen, "ending_fen": ending_fen,
            "moves": list(moves), "tested_decisions": decision_count, "trained_color": trained_color}


def candidate_parts(kind: str, occurrence: dict) -> tuple[dict, ...]:
    if kind == "shared_trunk":
        return (
            segment_preview("shared_trunk", occurrence["start_fen"], occurrence["cut_fen"],
                            occurrence["prefix_moves"], occurrence["decision_index"] + 1, occurrence["trained_color"]),
            segment_preview("branch", occurrence["cut_fen"], occurrence["ending_fen"],
                            occurrence["branch_moves"], occurrence["decision_count"] - occurrence["decision_index"] - 1,
                            occurrence["trained_color"]),
        )
    if kind != "transposition":
        raise ValueError("Unknown recommendation kind")
    continuation = segment_preview("shared_continuation", occurrence["decision_fen"], occurrence["ending_fen"],
                                   occurrence["suffix_moves"], occurrence["decision_count"] - occurrence["decision_index"],
                                   occurrence["trained_color"])
    if not occurrence["decision_index"]:
        return (continuation,)
    return (
        segment_preview("bridge", occurrence["start_fen"], occurrence["bridge_end_fen"],
                        occurrence["bridge_moves"], occurrence["decision_index"], occurrence["trained_color"]),
        continuation,
    )


def recommendation_estimate(kind: str, occurrences: tuple[dict, ...]) -> dict | None:
    """Small-fixture reference for the worker's indexed, paged aggregation."""
    presentations = {item["card_id"]: item for item in occurrences}
    if len(presentations) < 2:
        return None
    discriminator = "next_context" if kind == "shared_trunk" else "incoming_key"
    if len({stable_key(item[discriminator]) for item in occurrences}) < 2:
        return None
    parts = {part["id"]: part for item in occurrences for part in candidate_parts(kind, item)
             if part["tested_decisions"]}
    before = sum(item["decision_count"] for item in presentations.values())
    after = sum(part["tested_decisions"] for part in parts.values())
    additional_starts = max(0, len(parts) - len(presentations))
    savings = before - after
    if savings <= 0 or savings < additional_starts:
        return None
    return {"kind": kind, "decisions_before": before, "decisions_after": after,
            "decisions_avoided": savings, "additional_starts": additional_starts,
            "segments": sorted(parts.values(), key=lambda item: (item["role"], item["id"]))}
