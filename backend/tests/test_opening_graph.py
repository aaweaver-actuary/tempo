from datetime import date, timedelta
import json
import time

import chess
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


def test_configured_black_route_materializes_as_one_cumulative_prefix():
    route = [
        "e2e4", "c7c6", "d2d4", "d7d5", "e4e5", "c6c5",
        "c2c3", "b8c6", "g1f3", "c5d4", "c3d4", "c8g4",
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
            "e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6",
            "b5a4", "g8f6", "e1g1",
        ],
        "white",
        2,
    )

    assert [segment.segment_kind for segment in segments] == [
        "prefix", "decision", "decision", "decision"
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
            "e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6",
            "b5a4", "g8f6", "e1g1",
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


def test_any_mature_incoming_path_unlocks_a_transposed_descendant():
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


def test_descendant_requires_a_mature_parent(tmp_path, monkeypatch):
    from app.main import seed_queue

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        with database.connection() as connection:
            _insert_repertoire_line(
                connection,
                "frontier-repertoire",
                "line-1",
                ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4"],
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
    assert len(graph) == 1


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

    assert len(artifacts.graph_steps) == 1_600
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
                ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4"],
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


def test_cumulative_graph_prefix_offers_splitting_but_decisions_do_not(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as connection:
            _insert_repertoire_line(
                connection,
                "split-repertoire",
                "line-1",
                ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4"],
            )
        _publish_graph("split-repertoire")
        with database.connection() as connection:
            cards = connection.execute(
                """SELECT card_id FROM opening_graph_steps
                   WHERE repertoire_id='split-repertoire' ORDER BY decision_index"""
            ).fetchall()
        assert client.get(f"/api/cards/{cards[0]['card_id']}/prefix-split").status_code == 200
        assert client.get(f"/api/cards/{cards[1]['card_id']}/prefix-split").status_code == 422


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
            assert current["total_sources"] == 2
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
            ).fetchone()[0] == 1


def test_exact_archived_prefix_restores_its_original_reviews_and_schedule(
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
            connection.execute(
                "UPDATE cards SET archived=1 WHERE id=?", (legacy_id,)
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
            ).fetchone()[0] == "queued"
            seeded = connection.execute(
                """SELECT card.stability,seed.baseline_successful_days,seed.verification_due
                   FROM opening_card_schedule_seeds seed
                   JOIN cards card ON card.id=seed.card_id"""
            ).fetchall()
            assert seeded == []
            restored = connection.execute(
                "SELECT archived,state,stability,due_date FROM cards WHERE id=?", (legacy_id,)
            ).fetchone()
            assert tuple(restored) == (0, "mature", 30, "2026-12-01")


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


def test_synthesized_prefix_evidence_caps_stability_and_copies_no_reviews(
    tmp_path, monkeypatch
):
    from app.services.cards import card_id

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        route = ["e2e4", "e7e5", "g1f3"]
        with database.connection() as connection:
            _insert_repertoire_line(
                connection, "seeded-prefix-repertoire", "line-1", route
            )
            board_after_first = chess.Board(STARTING_FEN)
            board_after_first.push_uci("e2e4")
            legacy_cards = (
                (card_id(STARTING_FEN, ["e2e4"]), STARTING_FEN, ["e2e4"]),
                (
                    card_id(board_after_first.fen(), ["e7e5", "g1f3"]),
                    board_after_first.fen(),
                    ["e7e5", "g1f3"],
                ),
            )
            for identifier, starting_fen, moves in legacy_cards:
                scheduled = schedule_review("correct")
                connection.execute(
                    """INSERT INTO cards(
                           id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                           interval_days,stability,fsrs_card_json,first_correct_at,
                           introduced_at,content_type,trained_color
                       ) VALUES(?,?,'response',?,?,'mature','2026-12-01',30,30,?,
                                '2026-08-01','2026-08-01','opening','white')""",
                    (
                        identifier,
                        "seeded-prefix-repertoire",
                        starting_fen,
                        json.dumps(moves),
                        scheduled.fsrs_card_json,
                    ),
                )
                connection.execute(
                    "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
                    ("seeded-prefix-repertoire", identifier),
                )
                for review_day in ("2026-08-01", "2026-08-03", "2026-08-05"):
                    connection.execute(
                        """INSERT INTO reviews(
                               card_id,rating,reviewed_at,previous_interval,next_interval
                           ) VALUES(?,'correct',?,1,7)""",
                        (identifier, review_day),
                    )

        _publish_graph("seeded-prefix-repertoire")

        expected_prefix_id = card_id(STARTING_FEN, route)
        with database.connection() as connection:
            prefix = connection.execute(
                "SELECT state,stability,due_date FROM cards WHERE id=?",
                (expected_prefix_id,),
            ).fetchone()
            assert prefix["state"] == "mature"
            assert prefix["stability"] == 14
            assert date.fromisoformat(prefix["due_date"]) <= date.today() + timedelta(days=7)
            assert connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id=?",
                (expected_prefix_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id IN (?,?)",
                tuple(identifier for identifier, _, _ in legacy_cards),
            ).fetchone()[0] == 6


def test_manual_prefix_split_survives_graph_rebuild(tmp_path, monkeypatch):
    from app.services.game_sync_coordinator import coordinator

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        client.portal.call(coordinator.stop)
        route = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
        with database.connection() as connection:
            _insert_repertoire_line(
                connection, "split-persistence-repertoire", "line-1", route
            )
        _publish_graph("split-persistence-repertoire")
        with database.connection() as connection:
            source = connection.execute(
                """SELECT card_id FROM opening_graph_steps
                   WHERE repertoire_id='split-persistence-repertoire'
                   ORDER BY decision_index LIMIT 1"""
            ).fetchone()[0]
            revision = connection.execute(
                "SELECT revision FROM cards WHERE id=?", (source,)
            ).fetchone()[0]

        response = client.post(
            f"/api/cards/{source}/prefix-split",
            json={"expected_revision": revision},
        )
        assert response.status_code == 200
        split = response.json()
        rebuild = claim_task("opening_graph_rebuild")
        assert rebuild is not None
        execute_opening_graph_rebuild(rebuild)
        assert complete_task(
            rebuild["id"], rebuild["generation"], rebuild["lease_token"]
        )

        with database.connection() as connection:
            published_steps = connection.execute(
                """SELECT step.card_id,step.segment_kind
                   FROM opening_graph_steps step
                   JOIN opening_graph_publications publication
                     ON publication.repertoire_id=step.repertoire_id
                    AND publication.generation=step.generation
                   WHERE step.repertoire_id='split-persistence-repertoire'
                   ORDER BY step.decision_index"""
            ).fetchall()
            assert [row["card_id"] for row in published_steps] == [
                split["parent"]["card_id"],
                split["continuation"]["card_id"],
            ]
            assert [row["segment_kind"] for row in published_steps] == [
                "prefix",
                "decision",
            ]


def test_cumulative_prefix_integrity_block_covers_any_crossed_decision(
    tmp_path, monkeypatch
):
    from app.services.game_sync_coordinator import coordinator

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        client.portal.call(coordinator.stop)
        route = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
        segment = decision_segments(STARTING_FEN, route, "white", 3)[0]
        with database.connection() as connection:
            _insert_repertoire_line(
                connection, "blocked-prefix-repertoire", "line-1", route
            )
            connection.execute(
                """INSERT INTO repertoire_integrity_issues(
                       id,repertoire_id,kind,fen_key,fen,trained_color,signature,
                       moves_json,sources_json,created_at,updated_at
                   ) VALUES('prefix-issue','blocked-prefix-repertoire',
                            'multiple_responses',?,?,'white','prefix-signature',
                            '[]','[]','2026-09-21','2026-09-21')""",
                (segment.decision_fen_keys[1], STARTING_FEN),
            )

        _publish_graph("blocked-prefix-repertoire")

        with database.connection() as connection:
            blocked = connection.execute(
                """SELECT card_id FROM repertoire_integrity_card_blocks
                   WHERE repertoire_id='blocked-prefix-repertoire'"""
            ).fetchall()
            assert [row["card_id"] for row in blocked] == [segment.card_id]
