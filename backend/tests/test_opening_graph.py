from app.services.opening_graph import decision_segments


STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_lines_sharing_a_prefix_materialize_one_card_per_shared_decision():
    first = decision_segments(
        STARTING_FEN,
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
        "white",
        3,
    )
    second = decision_segments(
        STARTING_FEN,
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],
        "white",
        3,
    )

    assert [segment.card_id for segment in first[:2]] == [
        segment.card_id for segment in second[:2]
    ]
    assert first[2].card_id != second[2].card_id
    assert [segment.moves for segment in first] == [
        ("e2e4",),
        ("e7e5", "g1f3"),
        ("b8c6", "f1b5"),
    ]


def test_branch_divergence_creates_distinct_cards_only_after_the_divergence():
    king_side = decision_segments(
        STARTING_FEN,
        ["e2e4", "e7e5", "g1f3", "b8c6"],
        "black",
        2,
    )
    sicilian = decision_segments(
        STARTING_FEN,
        ["e2e4", "c7c5", "g1f3", "d7d6"],
        "black",
        2,
    )

    assert king_side[0].moves == ("e2e4", "e7e5")
    assert king_side[1].moves == ("g1f3", "b8c6")
    assert king_side[1].parent_card_id == king_side[0].card_id
    assert sicilian[0].card_id != king_side[0].card_id


def test_distinct_opponent_cues_to_the_same_position_remain_distinct_cards():
    direct = decision_segments(
        STARTING_FEN,
        ["g1f3", "g8f6", "g2g3"],
        "white",
        2,
    )
    alternate = decision_segments(
        STARTING_FEN,
        ["g2g3", "g8f6", "g1f3"],
        "white",
        2,
    )

    assert direct[1].card_id != alternate[1].card_id
    assert direct[1].moves != alternate[1].moves
