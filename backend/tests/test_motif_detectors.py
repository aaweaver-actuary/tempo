import json
from pathlib import Path

import chess
import pytest

from app.services.motif_detectors import (
    MOTIF_PRECEDENCE,
    classify_candidate_lines,
    select_primary_motif,
)


FIXTURE_PATH = Path(__file__).parents[2] / "tests/fixtures/motif-parity.json"


@pytest.mark.parametrize(
    "fixture",
    json.loads(FIXTURE_PATH.read_text()),
    ids=lambda fixture: fixture["id"],
)
def test_python_motif_parity_fixture(fixture):
    position = chess.Board(fixture["fen"])
    for candidate in fixture["candidates"]:
        replay = position.copy()
        for move_uci in candidate["pv"]:
            move = chess.Move.from_uci(move_uci)
            assert move in replay.legal_moves, fixture["id"]
            replay.push(move)

    evidence = classify_candidate_lines(
        position,
        fixture.get("played_move"),
        fixture["candidates"],
    )
    actual = [
        {
            "motif": item.motif,
            "pin_type": item.concrete_outcome.get("pin_type"),
            "existed_before": item.existed_before,
            "created_by_candidate_move": item.created_by_candidate_move,
            "outcome": item.concrete_outcome["type"],
        }
        for item in evidence
    ]
    assert actual == fixture["expected"]


def test_detector_contract_preserves_structured_pin_evidence():
    fixture = next(
        item
        for item in json.loads(FIXTURE_PATH.read_text())
        if item["id"] == "absolute-pin-created-by-candidate"
    )
    evidence = classify_candidate_lines(
        chess.Board(fixture["fen"]),
        fixture["played_move"],
        fixture["candidates"],
    )[0]

    assert evidence.motif == "pin"
    assert evidence.involved_squares == {
        "pinner": "e1",
        "pinned_piece": "e7",
        "protected_target": "e8",
    }
    assert {piece["role"] for piece in evidence.involved_pieces} == {
        "pinner",
        "pinned_piece",
        "protected_target",
    }
    assert evidence.proving_pv_segment == ("a1e1", "e8d8", "e1e7")
    assert evidence.concrete_outcome["material_gain_cp"] == 500


def test_primary_motif_precedence_is_stable_and_keeps_secondary_results():
    assert MOTIF_PRECEDENCE == (
        "matingTactic",
        "pin",
        "fork",
        "skewer",
        "discoveredAttack",
        "hangingPiece",
    )
    fixture = next(
        item
        for item in json.loads(FIXTURE_PATH.read_text())
        if item["id"] == "pin-and-fork-are-both-preserved"
    )
    evidence = classify_candidate_lines(
        chess.Board(fixture["fen"]),
        fixture["played_move"],
        fixture["candidates"],
    )
    assert [item.motif for item in evidence] == ["pin", "fork"]
    assert select_primary_motif(evidence).motif == "pin"
