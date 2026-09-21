from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.durable_tasks import claim_task, complete_task
from app.services.opening_graph import (
    decision_segments,
    enqueue_opening_graph_rebuild,
    execute_opening_graph_rebuild,
)


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


def _insert_repertoire_line(connection, repertoire_id: str, line_id: str, moves):
    connection.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,'2026-09-21')",
        (repertoire_id, repertoire_id, f"{repertoire_id}.pgn"),
    )
    connection.execute(
        """INSERT INTO repertoire_lines(
               id,repertoire_id,name,trained_color,start_fen,moves_json,created_at
           ) VALUES(?,?,?,'white',?,?, '2026-09-21')""",
        (line_id, repertoire_id, f"{repertoire_id}.pgn", STARTING_FEN, __import__("json").dumps(moves)),
    )
    connection.execute(
        "INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) VALUES(?,3)",
        (line_id,),
    )


def test_repeated_graph_rebuilds_are_generation_guarded_and_idempotent(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        with database.connection() as connection:
            _insert_repertoire_line(
                connection,
                "graph-repertoire",
                "line-1",
                ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
            )
        first = enqueue_opening_graph_rebuild("graph-repertoire")
        claimed = claim_task("opening_graph_rebuild")
        assert claimed and claimed["id"] == first["id"]
        enqueue_opening_graph_rebuild("graph-repertoire")
        execute_opening_graph_rebuild(claimed)
        assert complete_task(
            claimed["id"], claimed["generation"], claimed["lease_token"]
        ) is False
        with database.connection() as connection:
            assert connection.execute(
                "SELECT 1 FROM opening_graph_publications WHERE repertoire_id='graph-repertoire'"
            ).fetchone() is None

        current = claim_task("opening_graph_rebuild")
        assert current is not None
        execute_opening_graph_rebuild(current)
        assert complete_task(
            current["id"], current["generation"], current["lease_token"]
        ) is True
        with database.connection() as connection:
            publication = connection.execute(
                "SELECT generation FROM opening_graph_publications WHERE repertoire_id='graph-repertoire'"
            ).fetchone()
            assert publication["generation"] == current["generation"]
            assert connection.execute(
                """SELECT COUNT(DISTINCT card_id) FROM opening_graph_steps
                   WHERE repertoire_id='graph-repertoire' AND generation=?""",
                (current["generation"],),
            ).fetchone()[0] == 3
