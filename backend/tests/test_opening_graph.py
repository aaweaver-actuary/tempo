import json

import chess

from app.services.opening_graph import GraphInput, build_graph, decision_segments


STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_configured_black_route_materializes_as_one_cumulative_prefix():
    route = [
        "e2e4",
        "c7c6",
        "d2d4",
        "d7d5",
        "e4e5",
        "c6c5",
        "c2c3",
        "b8c6",
        "g1f3",
        "c5d4",
        "c3d4",
        "c8g4",
    ]

    segments = decision_segments(STARTING_FEN, route, "black", 6)

    assert len(segments) == 1
    assert segments[0].segment_kind == "prefix"
    assert segments[0].moves == tuple(route)
    assert segments[0].first_decision_index == 0
    assert segments[0].last_decision_index == 5
    assert len(segments[0].decision_fen_keys) == 6


def test_identical_complete_prefixes_share_one_global_schedule():
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

    assert len(first) == len(second) == 1
    assert first[0].card_id != second[0].card_id

    identical = decision_segments(
        STARTING_FEN,
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
        "white",
        3,
    )
    assert identical[0].card_id == first[0].card_id


def test_early_divergent_route_prefixes_may_repeat_shared_moves():
    king_side = decision_segments(
        STARTING_FEN,
        ["e2e4", "c7c6", "d2d4", "d7d5", "e4e5", "c6c5"],
        "black",
        3,
    )
    alternate = decision_segments(
        STARTING_FEN,
        ["e2e4", "c7c6", "d2d4", "d7d5", "g1f3", "c8g4"],
        "black",
        3,
    )

    assert king_side[0].moves[:4] == alternate[0].moves[:4]
    assert king_side[0].card_id != alternate[0].card_id


def test_post_prefix_cards_test_exactly_one_learner_decision():
    segments = decision_segments(
        STARTING_FEN,
        [
            "e2e4",
            "e7e5",
            "g1f3",
            "b8c6",
            "f1b5",
            "a7a6",
            "b5a4",
            "g8f6",
            "e1g1",
        ],
        "white",
        2,
    )

    assert [segment.segment_kind for segment in segments] == [
        "prefix",
        "decision",
        "decision",
        "decision",
    ]
    assert segments[0].moves == ("e2e4", "e7e5", "g1f3")
    assert all(
        segment.first_decision_index == segment.last_decision_index
        for segment in segments[1:]
    )


def test_full_line_descendants_are_materialized_beyond_prefix_depth():
    segments = decision_segments(
        STARTING_FEN,
        [
            "e2e4",
            "e7e5",
            "g1f3",
            "b8c6",
            "f1b5",
            "a7a6",
            "b5a4",
            "g8f6",
            "e1g1",
        ],
        "white",
        2,
    )
    assert len(segments) == 4
    assert segments[-1].moves == ("g8f6", "e1g1")


def test_distinct_opponent_cues_to_the_same_position_remain_distinct_cards():
    direct = decision_segments(
        STARTING_FEN,
        ["g1f3", "g8f6", "g2g3"],
        "white",
        1,
    )
    alternate = decision_segments(
        STARTING_FEN,
        ["g2g3", "g8f6", "g1f3"],
        "white",
        1,
    )

    assert direct[1].card_id != alternate[1].card_id
    assert direct[1].moves != alternate[1].moves


def test_transposed_routes_share_descendant_with_distinct_parents():
    first_route = decision_segments(
        STARTING_FEN,
        ["g1f3", "d7d5", "g2g3", "g8f6", "f1g2"],
        "white",
        2,
    )
    second_route = decision_segments(
        STARTING_FEN,
        ["g2g3", "d7d5", "g1f3", "g8f6", "f1g2"],
        "white",
        2,
    )

    assert first_route[0].card_id != second_route[0].card_id
    assert first_route[1].card_id == second_route[1].card_id
    assert {
        first_route[1].parent_card_id,
        second_route[1].parent_card_id,
    } == {first_route[0].card_id, second_route[0].card_id}


def test_graph_steps_reuse_exact_last_decision_fen_for_white_and_black_routes():
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6"]
    graph = build_graph(
        GraphInput(
            "fen-reuse",
            tuple(
                {
                    "id": f"{color}-line",
                    "start_fen": STARTING_FEN,
                    "moves_json": json.dumps(moves),
                    "trained_color": color,
                    "learner_decision_count": 2,
                }
                for color in ("white", "black")
            ),
            2,
        )
    )
    assert len(graph) > 2
    for step in graph:
        board = chess.Board(step.starting_fen)
        for setup_move in step.moves[:-1]:
            board.push_uci(setup_move)
        assert step.decision_fen_key == " ".join(board.fen().split()[:4])
        assert step.decision_fen_key == step.decision_fen_keys[-1]
