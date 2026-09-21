from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.durable_tasks import claim_task, complete_task
from app.services.opening_graph import (
    decision_segments,
    enqueue_opening_graph_rebuild,
    execute_opening_graph_rebuild,
)
from app.services.scheduler import schedule_review


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


def test_cumulative_migration_preserves_reviews_and_caps_seeded_stability(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        route = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
        with database.connection() as connection:
            _insert_repertoire_line(connection, "migration-repertoire", "line-1", route)
            from app.services.cards import card_id

            legacy_id = card_id(STARTING_FEN, route)
            scheduled = schedule_review("correct")
            connection.execute(
                """INSERT INTO cards(
                       id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                       interval_days,stability,fsrs_card_json,first_correct_at,
                       introduced_at,content_type,trained_color
                   ) VALUES(?,?,'prefix',?,?,'mature','2026-12-01',30,30,?,?,
                            '2026-08-01','opening','white')""",
                (
                    legacy_id,
                    "migration-repertoire",
                    STARTING_FEN,
                    __import__("json").dumps(route),
                    scheduled.fsrs_card_json,
                    scheduled.first_correct_at,
                ),
            )
            connection.execute(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
                ("migration-repertoire", legacy_id),
            )
            for day in ("2026-08-01", "2026-08-03", "2026-08-05"):
                connection.execute(
                    """INSERT INTO reviews(
                           card_id,rating,reviewed_at,previous_interval,next_interval
                       ) VALUES(?,'correct',?,1,7)""",
                    (legacy_id, day),
                )
            connection.execute(
                "INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES('2026-09-21',?,0,'complete')",
                (legacy_id,),
            )
            connection.execute(
                "INSERT INTO daily_queue(queue_date,card_id,cycle,position,status) VALUES('2026-09-21',?,1,1,'queued')",
                (legacy_id,),
            )

        enqueue_opening_graph_rebuild("migration-repertoire")
        task = claim_task("opening_graph_rebuild")
        assert task is not None
        execute_opening_graph_rebuild(task)
        complete_task(task["id"], task["generation"], task["lease_token"])

        with database.connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id=?", (legacy_id,)
            ).fetchone()[0] == 3
            assert connection.execute(
                "SELECT status FROM daily_queue WHERE card_id=? AND cycle=0", (legacy_id,)
            ).fetchone()[0] == "complete"
            assert connection.execute(
                "SELECT status FROM daily_queue WHERE card_id=? AND cycle=1", (legacy_id,)
            ).fetchone()[0] == "superseded"
            seeded = connection.execute(
                """SELECT card.stability,seed.baseline_successful_days,seed.verification_due
                   FROM opening_card_schedule_seeds seed
                   JOIN cards card ON card.id=seed.card_id"""
            ).fetchall()
            assert len(seeded) == 3
            assert all(row["stability"] <= 14 for row in seeded)
            assert all(row["baseline_successful_days"] == 3 for row in seeded)
            assert connection.execute(
                "SELECT archived FROM cards WHERE id=?", (legacy_id,)
            ).fetchone()[0] == 1
