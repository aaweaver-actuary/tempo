"""Validation guards retained for repertoire source edits and repair."""

import json

import chess
import pytest

from app.repertoire.changes.application import validate_source_rows, validate_repair_source_rows
from app.repertoire.changes.ports import ChangeError


def test_new_conflicting_import_is_held_and_invalid_source_json_is_not_clean():
    rows = tuple(
        dict(
            source_id=name,
            start_fen=chess.STARTING_FEN,
            moves_json=json.dumps([move]),
            trained_color="white",
        )
        for name, move in [("a", "e2e4"), ("b", "d2d4")]
    )
    with pytest.raises(ChangeError, match="pending_resolution"):
        validate_source_rows("rep", rows)
    with pytest.raises(ChangeError, match="invalid_source"):
        validate_source_rows("rep", (dict(rows[0], moves_json="broken-json"),))



def test_integrity_repair_allows_one_existing_conflict_to_clear_but_rejects_new_or_worsened():
    from app.repertoire.changes.application import validate_repair_source_rows

    other_fen = "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2"

    def row(name, fen, move):
        return dict(
            source_id=name,
            start_fen=fen,
            moves_json=json.dumps([move]),
            trained_color="white",
        )

    before = (
        row("a", chess.STARTING_FEN, "e2e4"),
        row("b", chess.STARTING_FEN, "d2d4"),
        row("c", other_fen, "e2e4"),
        row("d", other_fen, "d2d4"),
    )
    one_repaired = (
        before[0],
        row("b", chess.STARTING_FEN, "e2e4"),
        before[2],
        before[3],
    )
    validate_repair_source_rows("rep", before, one_repaired)
    with pytest.raises(ChangeError, match="pending_resolution"):
        validate_repair_source_rows("rep", before, before)
    with pytest.raises(ChangeError, match="pending_resolution"):
        validate_repair_source_rows(
            "rep", before, one_repaired + (row("e", other_fen, "c2c4"),)
        )
    new_fen = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    with pytest.raises(ChangeError, match="pending_resolution"):
        validate_repair_source_rows(
            "rep",
            before,
            one_repaired + (row("f", new_fen, "g1f3"), row("g", new_fen, "f1c4")),
        )
    with pytest.raises(ChangeError, match="invalid_source"):
        validate_repair_source_rows(
            "rep",
            before,
            one_repaired + (dict(before[0], source_id="bad", moves_json="broken"),),
        )
