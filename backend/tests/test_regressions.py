import json

import pytest


PGN = b'[Event "Rated blitz game"]\n[White "andy"]\n[Black "opponent"]\n[UTCDate "2026.09.16"]\n[UTCTime "12:30:00"]\n[Site "https://lichess.org/game1"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. d4 *'
BRANCH_PGN = b'[Event "QGD"]\n[White "andy"]\n[Black "opponent"]\n[Result "*"]\n\n1. d4 d5 2. c4 e6 *\n\n[Event "Nimzo"]\n[White "andy"]\n[Black "opponent"]\n[Result "*"]\n\n1. d4 Nf6 2. c4 e6 3. Nc3 Bb4 *'


def test_prefixes_match_rust_including_incomplete_trained_turns():
    from pathlib import Path
    from app.chess.pgn import prefix_through_user_moves

    fixtures = json.loads(
        (Path(__file__).parents[2] / "tests/fixtures/core-parity.json").read_text()
    )
    for fixture in fixtures:
        if fixture.get("error") == "invalid_fen":
            with pytest.raises(ValueError):
                prefix_through_user_moves(
                    fixture["fen"], fixture["moves"], fixture["color"], fixture["depth"]
                )
            continue
        assert (
            prefix_through_user_moves(
                fixture["fen"], fixture["moves"], fixture["color"], fixture["depth"]
            )
            == fixture["prefix"]
        )


def test_python_card_ids_match_shared_rust_parity_fixture():
    from pathlib import Path
    from app.chess.cards import card_id

    fixtures = json.loads(
        (Path(__file__).parents[2] / "tests/fixtures/card-id-parity.json").read_text()
    )
    for fixture in fixtures:
        assert card_id(fixture["fen"], fixture["moves"]) == fixture["cardId"], fixture[
            "name"
        ]


def test_game_mistakes_respect_arbitrary_fen_side_to_move():
    from app.chess.evaluation import classify_swings

    result = classify_swings(
        [{"ply": 0, "before_cp": 0, "after_cp": 150}], "black", starting_color="black"
    )
    assert result["major_mistake_ply"] == 0
