from datetime import date
import json
import time

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.durable_tasks import claim_task, complete_task
from app.services.opening_graph import (
    GraphInput,
    GraphRebuildInput,
    build_graph,
    calculate_opening_graph_artifacts,
    decision_segments,
    enqueue_opening_graph_rebuild,
    execute_opening_graph_rebuild,
)
from app.services.scheduler import schedule_review


STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _publish_graph(repertoire_id: str) -> None:
    enqueue_opening_graph_rebuild(repertoire_id)
    task = claim_task("opening_graph_rebuild")
    if task is not None:
        execute_opening_graph_rebuild(task)
        complete_task(task["id"], task["generation"], task["lease_token"])
    for _ in range(200):
        with database.connection() as connection:
            if connection.execute(
                "SELECT 1 FROM opening_graph_publications WHERE repertoire_id=?",
                (repertoire_id,),
            ).fetchone():
                return
        time.sleep(0.01)
    raise AssertionError(f"opening graph did not publish for {repertoire_id}")


def test_daily_queue_graph_unlock_has_a_card_first_lookup_index(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as connection:
        indexed_columns = [
            row["name"]
            for row in connection.execute(
                "PRAGMA index_info(idx_opening_graph_steps_queue_card)"
            )
        ]
    assert indexed_columns == [
        "card_id",
        "repertoire_id",
        "generation",
        "parent_card_id",
    ]


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


def test_any_mature_incoming_path_unlocks_a_transposed_descendant():
    first_route = decision_segments(
        STARTING_FEN,
        ["g1f3", "d7d5", "g2g3", "g8f6", "f1g2"],
        "white",
        3,
    )
    second_route = decision_segments(
        STARTING_FEN,
        ["g2g3", "d7d5", "g1f3", "g8f6", "f1g2"],
        "white",
        3,
    )

    assert first_route[1].card_id != second_route[1].card_id
    assert first_route[2].card_id == second_route[2].card_id
    assert {
        first_route[2].parent_card_id,
        second_route[2].parent_card_id,
    } == {first_route[1].card_id, second_route[1].card_id}


def test_descendant_requires_a_mature_parent(tmp_path, monkeypatch):
    from app.main import seed_queue

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        with database.connection() as connection:
            _insert_repertoire_line(
                connection,
                "frontier-repertoire",
                "line-1",
                ["e2e4", "e7e5", "g1f3"],
            )
        _publish_graph("frontier-repertoire")

        with database.connection() as connection:
            steps = connection.execute(
                """SELECT card_id,parent_card_id FROM opening_graph_steps
                   WHERE repertoire_id='frontier-repertoire'
                   ORDER BY decision_index"""
            ).fetchall()
            assert connection.execute(
                "SELECT state FROM cards WHERE id=?", (steps[1]["card_id"],)
            ).fetchone()[0] == "locked"
            connection.execute(
                "UPDATE cards SET state='mature' WHERE id=?", (steps[0]["card_id"],)
            )
            seed_queue(connection, date.today().isoformat())
            assert connection.execute(
                "SELECT state FROM cards WHERE id=?", (steps[1]["card_id"],)
            ).fetchone()[0] == "learning"


def test_graph_rebuild_holds_no_sqlite_connection_during_chess_traversal(
    monkeypatch,
):
    def forbidden_connection(*_args, **_kwargs):
        raise AssertionError("graph computation opened SQLite")

    monkeypatch.setattr(database, "connection", forbidden_connection)
    graph_input = GraphInput(
        "offline-repertoire",
        (
            {
                "id": "line-1",
                "start_fen": STARTING_FEN,
                "moves_json": json.dumps(
                    ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
                ),
                "trained_color": "white",
                "learner_decision_count": 3,
            },
        ),
        3,
    )
    graph = calculate_opening_graph_artifacts(
        GraphRebuildInput(graph_input, (), {}, frozenset(), "2026-09-21")
    ).graph_steps
    assert len(graph) == 3


def test_production_scale_opening_graph_calculation_is_bounded():
    route_json = json.dumps(
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
        ]
    )
    graph_input = GraphInput(
        "production-scale-repertoire",
        tuple(
            {
                "id": f"line-{line_number}",
                "start_fen": STARTING_FEN,
                "moves_json": route_json,
                "trained_color": "white",
                "learner_decision_count": 5,
            }
            for line_number in range(1_600)
        ),
        5,
    )

    calculation_started = time.perf_counter()
    artifacts = calculate_opening_graph_artifacts(
        GraphRebuildInput(graph_input, (), {}, frozenset(), "2026-09-21")
    )

    assert len(artifacts.graph_steps) == 8_000
    assert time.perf_counter() - calculation_started < 8


def test_failed_seed_verification_does_not_revoke_introduced_descendants(
    tmp_path, monkeypatch
):
    from datetime import datetime, timezone

    from app.services.review_service import apply_scheduling_review

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        now = datetime.now(timezone.utc)
        with database.connection() as connection:
            _insert_repertoire_line(
                connection,
                "verification-repertoire",
                "line-1",
                ["e2e4", "e7e5", "g1f3"],
            )
        _publish_graph("verification-repertoire")
        with database.connection() as connection:
            steps = connection.execute(
                """SELECT card_id FROM opening_graph_steps
                   WHERE repertoire_id='verification-repertoire'
                   ORDER BY decision_index"""
            ).fetchall()
            parent_id, child_id = steps[0]["card_id"], steps[1]["card_id"]
            connection.execute(
                "UPDATE cards SET state='mature',stability=14 WHERE id=?",
                (parent_id,),
            )
            connection.execute(
                "UPDATE cards SET state='learning',introduced_at=? WHERE id=?",
                (date.today().isoformat(), child_id),
            )
            apply_scheduling_review(
                connection,
                parent_id,
                "again",
                guided=False,
                source_kind="study",
                source_ref=None,
                light_first_interval_days=7,
                reviewed_at=now,
                review_day=date.today(),
            )
            assert connection.execute(
                "SELECT state FROM cards WHERE id=?", (parent_id,)
            ).fetchone()[0] == "learning"
            assert connection.execute(
                "SELECT state FROM cards WHERE id=?", (child_id,)
            ).fetchone()[0] == "learning"


def test_canonical_decision_cards_do_not_offer_prefix_splitting(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as connection:
            _insert_repertoire_line(
                connection,
                "split-repertoire",
                "line-1",
                ["e2e4", "e7e5", "g1f3"],
            )
        _publish_graph("split-repertoire")
        with database.connection() as connection:
            card_identifier = connection.execute(
                """SELECT card_id FROM opening_graph_steps
                   WHERE repertoire_id='split-repertoire' ORDER BY decision_index LIMIT 1"""
            ).fetchone()[0]
        assert client.get(f"/api/cards/{card_identifier}/prefix-split").status_code == 422


def test_graph_publication_restarts_integrity_scan_with_current_sources(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        with database.connection() as connection:
            _insert_repertoire_line(
                connection,
                "scan-repertoire",
                "line-1",
                ["e2e4", "e7e5", "g1f3"],
            )
        from app.services.repertoire_integrity import enqueue_integrity_scans

        first_run_id = enqueue_integrity_scans("scan-repertoire")[0]
        _publish_graph("scan-repertoire")
        with database.connection() as connection:
            current = connection.execute(
                """SELECT run_id,total_sources,last_error
                   FROM repertoire_integrity_jobs WHERE repertoire_id='scan-repertoire'"""
            ).fetchone()
            assert current["run_id"] != first_run_id
            assert current["total_sources"] == 3
            assert current["last_error"] is None


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
    from app.services.game_sync_coordinator import coordinator

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        client.portal.call(coordinator.stop)
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
        if current is not None:
            execute_opening_graph_rebuild(current)
            assert complete_task(
                current["id"], current["generation"], current["lease_token"]
            ) is True
            expected_generation = current["generation"]
        else:
            import time

            expected_generation = first["generation"] + 1
            for _ in range(200):
                with database.connection() as connection:
                    publication = connection.execute(
                        """SELECT generation FROM opening_graph_publications
                           WHERE repertoire_id='graph-repertoire'"""
                    ).fetchone()
                if publication:
                    break
                time.sleep(0.01)
        with database.connection() as connection:
            publication = connection.execute(
                "SELECT generation FROM opening_graph_publications WHERE repertoire_id='graph-repertoire'"
            ).fetchone()
            assert publication["generation"] == expected_generation
            assert connection.execute(
                """SELECT COUNT(DISTINCT card_id) FROM opening_graph_steps
                   WHERE repertoire_id='graph-repertoire' AND generation=?""",
                (expected_generation,),
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

        _publish_graph("migration-repertoire")

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


def test_graph_publication_never_rewrites_completed_queue_attempts(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        route = ["e2e4", "e7e5", "g1f3"]
        with database.connection() as connection:
            _insert_repertoire_line(
                connection, "completed-attempt-repertoire", "line-1", route
            )
            from app.services.cards import card_id

            legacy_card_id = card_id(STARTING_FEN, route)
            connection.execute(
                """INSERT INTO cards(
                       id,repertoire_id,kind,start_fen,moves_json,due_date,
                       content_type,trained_color
                   ) VALUES(?,?,'prefix',?,?,'2026-09-21','opening','white')""",
                (
                    legacy_card_id,
                    "completed-attempt-repertoire",
                    STARTING_FEN,
                    __import__("json").dumps(route),
                ),
            )
            connection.execute(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
                ("completed-attempt-repertoire", legacy_card_id),
            )
            connection.execute(
                """INSERT INTO daily_queue(
                       queue_date,card_id,cycle,position,status,attempt_state
                   ) VALUES('2026-09-21',?,0,7,'complete','failed')""",
                (legacy_card_id,),
            )

        _publish_graph("completed-attempt-repertoire")

        with database.connection() as connection:
            completed_attempt = connection.execute(
                """SELECT card_id,position,status,attempt_state FROM daily_queue
                   WHERE queue_date='2026-09-21' AND cycle=0"""
            ).fetchone()
            assert dict(completed_attempt) == {
                "card_id": legacy_card_id,
                "position": 7,
                "status": "complete",
                "attempt_state": "failed",
            }
