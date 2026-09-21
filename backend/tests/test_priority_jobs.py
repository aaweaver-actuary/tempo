from datetime import date, datetime, timezone
import json
import random
import threading
import time

import chess
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services import introduction_priorities as priorities
from app.services.game_sync_coordinator import (
    _requeue_interrupted_background_work,
    coordinator,
)


START_FEN = chess.STARTING_FEN


def _seed_repertoire(database_connection, repertoire_id: str = "priority-rep") -> int:
    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()
    database_connection.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
        (repertoire_id, "Priority", "priority.pgn", now),
    )
    moves = ["e2e4", "e7e5", "g1f3"]
    database_connection.execute(
        """INSERT INTO repertoire_lines(
               id,repertoire_id,name,trained_color,start_fen,moves_json,created_at
           ) VALUES('priority-line',?,'Priority','white',?,?,?)""",
        (repertoire_id, START_FEN, json.dumps(moves), now),
    )
    database_connection.execute(
        """INSERT INTO cards(
               id,repertoire_id,kind,start_fen,moves_json,state,due_date,
               introduced_at,trained_color,content_type
           ) VALUES('priority-card',?,'prefix',?,?,'learning',?,?, 'white','opening')""",
        (repertoire_id, START_FEN, json.dumps(moves), today, today),
    )
    database_connection.execute(
        "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,'priority-card')",
        (repertoire_id,),
    )
    database_connection.execute(
        "INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at) VALUES(?,'clean',?)",
        (repertoire_id, now),
    )
    return database_connection.execute(
        "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'priority-card',0)",
        (today,),
    ).lastrowid


def test_priority_refresh_coalesces_repeated_game_and_coverage_triggers(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        _seed_repertoire(database_connection)
    first_generation = priorities.enqueue_priority_refresh(
        "priority-rep", quiet_seconds=0
    )
    second_generation = priorities.enqueue_priority_refresh(
        "priority-rep", quiet_seconds=0
    )
    with database.connection() as database_connection:
        jobs = database_connection.execute(
            "SELECT * FROM repertoire_priority_jobs"
        ).fetchall()
    assert first_generation == 1
    assert second_generation == 2
    assert len(jobs) == 1
    assert jobs[0]["generation"] == 2
    assert jobs[0]["status"] == "queued"


def test_interrupted_priority_refresh_replays_once_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        _seed_repertoire(database_connection)
    priorities.enqueue_priority_refresh("priority-rep", quiet_seconds=0)
    claimed = priorities.claim_priority_refresh()
    assert claimed is not None
    _requeue_interrupted_background_work()
    replayed = priorities.claim_priority_refresh()
    assert replayed is not None
    priorities.execute_priority_refresh(replayed)
    with database.connection() as database_connection:
        job = database_connection.execute(
            "SELECT status,attempts FROM repertoire_priority_jobs WHERE repertoire_id='priority-rep'"
        ).fetchone()
        record_count = database_connection.execute(
            "SELECT COUNT(*) FROM repertoire_card_introduction_priorities WHERE repertoire_id='priority-rep'"
        ).fetchone()[0]
    assert job["status"] == "complete"
    assert job["attempts"] == 2
    assert record_count == 1


def test_stale_priority_generation_cannot_overwrite_newer_inputs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        _seed_repertoire(database_connection)
    priorities.enqueue_priority_refresh("priority-rep", quiet_seconds=0)
    claimed = priorities.claim_priority_refresh()
    assert claimed is not None
    calculation_started = threading.Event()
    release_calculation = threading.Event()
    original_calculate = priorities.calculate_priority_records

    def paused_calculation(calculation_input):
        calculation_started.set()
        assert release_calculation.wait(timeout=3)
        return original_calculate(calculation_input)

    monkeypatch.setattr(priorities, "calculate_priority_records", paused_calculation)
    worker = threading.Thread(target=priorities.execute_priority_refresh, args=(claimed,))
    worker.start()
    assert calculation_started.wait(timeout=2)
    newer_generation = priorities.enqueue_priority_refresh(
        "priority-rep", quiet_seconds=0
    )
    release_calculation.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    with database.connection() as database_connection:
        job = database_connection.execute(
            "SELECT generation,status FROM repertoire_priority_jobs WHERE repertoire_id='priority-rep'"
        ).fetchone()
        record_count = database_connection.execute(
            "SELECT COUNT(*) FROM repertoire_card_introduction_priorities"
        ).fetchone()[0]
    assert newer_generation == 2
    assert dict(job) == {"generation": 2, "status": "queued"}
    assert record_count == 0


def test_foreground_review_and_workspace_reads_succeed_during_priority_computation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        queue_entry_id = _seed_repertoire(database_connection)
    priorities.enqueue_priority_refresh("priority-rep", quiet_seconds=0)
    claimed = priorities.claim_priority_refresh()
    assert claimed is not None
    calculation_started = threading.Event()
    release_calculation = threading.Event()

    def paused_calculation(_calculation_input):
        calculation_started.set()
        assert release_calculation.wait(timeout=3)
        return []

    async def disabled_coordinator_start():
        return None

    async def disabled_coordinator_stop():
        return None

    monkeypatch.setattr(priorities, "calculate_priority_records", paused_calculation)
    monkeypatch.setattr(coordinator, "start", disabled_coordinator_start)
    monkeypatch.setattr(coordinator, "stop", disabled_coordinator_stop)
    worker = threading.Thread(target=priorities.execute_priority_refresh, args=(claimed,))
    worker.start()
    assert calculation_started.wait(timeout=2)
    with TestClient(app) as client:
        assert client.get("/api/settings").status_code == 200
        assert client.get("/api/progress").status_code == 200
        review = client.post(
            "/api/cards/priority-card/review",
            json={"outcome": "correct", "queue_entry_id": queue_entry_id},
        )
        assert review.status_code == 200
    release_calculation.set()
    worker.join(timeout=3)
    assert not worker.is_alive()


def test_optimized_priority_scoring_matches_existing_parity_fixtures():
    move_lines = (
        ("d2d4", "d7d5", "c2c4", "e7e6", "b1c3"),
        ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5"),
    )
    lines = tuple(
        priorities.IntendedLine(
            f"line-{line_index}",
            START_FEN,
            "white",
            moves,
            priorities._route_signature(START_FEN, moves),
        )
        for line_index, moves in enumerate(move_lines)
    )
    cards = tuple(
        {
            "id": f"card-{card_index}",
            "start_fen": START_FEN,
            "moves_json": json.dumps(moves),
            "state": "new",
            "introduced_at": None,
        }
        for card_index, moves in enumerate((move_lines[0][:3], move_lines[1]))
    )
    calculation_input = priorities.PriorityCalculationInput(
        "parity",
        lines,
        cards,
        {},
        {},
        15,
        0.0005,
    )
    line_probabilities, edge_evidence, _aggregate = priorities._line_probabilities(
        list(lines), {}, {}, 15, 0.0005
    )
    naive_routes = {
        card["id"]: [
            (line, route)
            for line in lines
            if (route := priorities._line_card_route(card, line, edge_evidence))
        ]
        for card in cards
    }
    earliest_end_by_line = {
        line.identifier: min(
            route.end_ply
            for card in cards
            for candidate_line, route in naive_routes[card["id"]]
            if candidate_line.identifier == line.identifier
        )
        for line in lines
    }
    expected_completion_mass = {
        card["id"]: min(
            1.0,
            sum(
                line_probabilities[line.identifier]
                for line, route in naive_routes[card["id"]]
                if earliest_end_by_line[line.identifier] == route.end_ply
            ),
        )
        for card in cards
    }
    optimized = {
        record.card_id: record
        for record in priorities.calculate_priority_records(calculation_input)
    }
    assert {
        card_id: record.completion_mass for card_id, record in optimized.items()
    } == expected_completion_mass
    assert all(0 <= record.frontier_reach <= 1 for record in optimized.values())


def test_production_scale_priority_computation_holds_no_sqlite_connection():
    generator = random.Random(20260920)
    lines: list[priorities.IntendedLine] = []
    for line_index in range(1_600):
        board = chess.Board()
        moves: list[str] = []
        signature: list[tuple[str, str]] = []
        for _ in range(16):
            legal_moves = sorted(board.legal_moves, key=lambda move: move.uci())
            if not legal_moves:
                break
            move = legal_moves[generator.randrange(len(legal_moves))]
            signature.append((priorities.canonical_fen(board.fen()), move.uci()))
            moves.append(move.uci())
            board.push(move)
        lines.append(
            priorities.IntendedLine(
                f"line-{line_index}",
                START_FEN,
                "white",
                tuple(moves),
                tuple(signature),
            )
        )
    cards = tuple(
        {
            "id": f"card-{card_index}",
            "start_fen": START_FEN,
            "moves_json": json.dumps(
                lines[card_index].moves[: 1 + card_index % 5]
            ),
            "state": "new",
            "introduced_at": None,
        }
        for card_index in range(850)
    )
    starting_fen_key = priorities.canonical_fen(START_FEN)
    occurrence_rows = [
        {
            "fen_key": starting_fen_key,
            "move_uci": "e2e4",
            "played_at": "2026-09-20T00:00:00+00:00",
        }
        for _ in range(120_000)
    ]
    personal_evidence = priorities._personal_evidence_from_rows(occurrence_rows)
    calculation_input = priorities.PriorityCalculationInput(
        "production-scale",
        tuple(lines),
        cards,
        {},
        personal_evidence,
        15,
        0.0005,
    )
    started = time.monotonic()
    records = priorities.calculate_priority_records(calculation_input)
    assert len(records) == 850
    assert time.monotonic() - started < 20


def test_cpu_bound_task_does_not_starve_api_requests(tmp_path, monkeypatch):
    test_foreground_review_and_workspace_reads_succeed_during_priority_computation(
        tmp_path,
        monkeypatch,
    )
