import pytest

from app import database
from app.services import repertoire_coverage

from app.services.repertoire_coverage import (
    CoverageCandidate,
    blend_probabilities,
    required_reply_moves,
    discover_opponent_positions,
)


def test_coverage_threshold_uses_conditional_node_probability_rather_than_root_path_probability():
    candidates = [
        CoverageCandidate("e7e5", 0.98),
        CoverageCandidate("c7c5", 0.02),
    ]
    required = required_reply_moves(candidates, denominator=100, cumulative_target=0.95)
    assert required == {"e7e5", "c7c5"}
    assert 0.0001 * candidates[1].probability < 0.01  # reach probability is irrelevant


def test_coverage_requires_the_union_of_minimum_probability_and_cumulative_mass_replies():
    candidates = [
        CoverageCandidate("a7a6", 0.60),
        CoverageCandidate("b7b6", 0.25),
        CoverageCandidate("c7c6", 0.10),
        CoverageCandidate("d7d6", 0.03),
        CoverageCandidate("e7e6", 0.02),
    ]
    assert required_reply_moves(
        candidates, denominator=40, cumulative_target=0.90
    ) == {"a7a6", "b7b6", "c7c6", "d7d6"}


def test_coverage_uses_explorer_sample_weight_and_maia_smoothing():
    assert blend_probabilities(0.20, 0.50, explorer_games=200) == pytest.approx(0.35)
    assert blend_probabilities(0.20, None, explorer_games=10) == pytest.approx(0.20)
    assert blend_probabilities(None, 0.50, explorer_games=0) == pytest.approx(0.50)


def test_unknown_or_stale_coverage_data_never_reports_a_repertoire_complete():
    assert blend_probabilities(None, None, explorer_games=0) is None


def test_transposed_repertoire_positions_are_evaluated_once_and_credited_correctly():
    lines = [
        {
            "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "trained_color": "white",
            "moves_json": '["g1f3","g8f6","g2g3","g7g6","f1g2"]',
        },
        {
            "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "trained_color": "white",
            "moves_json": '["g2g3","g7g6","g1f3","g8f6","f1g2"]',
        },
    ]
    positions = discover_opponent_positions(lines, horizon_fullmoves=15)
    transposed = [position for position in positions if position["ply"] == 5]
    assert len(transposed) == 1
    assert len(transposed[0]["routes"]) == 2


def test_opponent_reply_without_a_trained_response_remains_a_coverage_gap():
    positions = discover_opponent_positions(
        [
            {
                "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "trained_color": "white",
                "moves_json": '["e2e4","c7c5"]',
            }
        ],
        horizon_fullmoves=15,
    )
    after_e4 = next(position for position in positions if position["ply"] == 1)
    assert "c7c5" not in after_e4["covered_replies"]


def test_coverage_refresh_persists_required_gaps_without_marking_unknown_complete(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        database_connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('r','R','r.pgn','2026-09-19T00:00:00+00:00')"
        )
        database_connection.execute(
            """INSERT INTO repertoire_lines(
                   id,repertoire_id,name,trained_color,start_fen,moves_json,created_at
               ) VALUES('l','r','line','white',?, ?, '2026-09-19T00:00:00+00:00')""",
            (
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                '["e2e4","e7e5","g1f3"]',
            ),
        )
    run_id = repertoire_coverage.enqueue_coverage_refresh("r")
    node = repertoire_coverage.claim_coverage_node()
    assert node and node["run_id"] == run_id
    monkeypatch.setattr(
        repertoire_coverage,
        "_fetch_explorer",
        lambda *_: {
            "moves": [
                {"uci": "e7e5", "white": 700, "draws": 100, "black": 100},
                {"uci": "c7c5", "white": 50, "draws": 25, "black": 25},
            ]
        },
    )
    repertoire_coverage.execute_coverage_node(node)
    summary = repertoire_coverage.coverage_summary("r")
    gaps = repertoire_coverage.coverage_gaps("r")
    assert summary["required_branches"] == 2
    assert summary["covered_branches"] == 1
    assert summary["is_complete"] is False
    assert [gap["move_uci"] for gap in gaps] == ["c7c5"]
