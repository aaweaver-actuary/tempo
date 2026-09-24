"""Storage regressions for derived introduction priorities."""

from datetime import datetime, timezone
import json
import threading

import chess

from app import database
from app.services import introduction_priorities as priorities
from app.services.database_executor import database_writer
from app.services.durable_tasks import claim_task, requeue_interrupted_tasks


def _seed_repertoire(database_connection, repertoire_id: str = "storage-rep") -> None:
    now = datetime.now(timezone.utc).isoformat()
    database_connection.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
        (repertoire_id, "Storage", "storage.pgn", now),
    )
    for card_id in ("card-a", "card-b"):
        database_connection.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date)
               VALUES(?,?,'prefix',?,'["e2e4"]','2026-01-01')""",
            (card_id, repertoire_id, chess.STARTING_FEN),
        )


def _insert_generation(database_connection, generation: int, card_ids=("card-a", "card-b")) -> None:
    now = datetime.now(timezone.utc).isoformat()
    database_connection.executemany(
        """INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date)
           VALUES(?,'storage-rep','prefix',?,'["e2e4"]','2026-01-01')""",
        [(card_id, chess.STARTING_FEN) for card_id in card_ids],
    )
    database_connection.executemany(
        """INSERT INTO repertoire_card_priority_generations(
               repertoire_id,generation,card_id,scoring_version,
               completed_line_ids_json,frontier_decisions_json,evidence_json,updated_at)
           VALUES('storage-rep',?,?,2,'[]','[]','{}',?)""",
        [(generation, card_id, now) for card_id in card_ids],
    )


def test_priority_evidence_omits_repeated_edge_states_and_preserves_status(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    moves = ("e2e4", "e7e5")
    line = priorities.IntendedLine(
        "line", chess.STARTING_FEN, "white", moves,
        priorities._route_signature(chess.STARTING_FEN, moves),
    )
    cards = tuple(
        {"id": card_id, "start_fen": chess.STARTING_FEN,
         "moves_json": json.dumps(moves), "state": "new", "introduced_at": None}
        for card_id in ("card-a", "card-b")
    )
    records = priorities.calculate_priority_records(
        priorities.PriorityCalculationInput("storage-rep", (line,), cards, {}, {}, 15, 0.0005)
    )
    assert len(records) == 2
    for record in records:
        evidence = json.loads(record.evidence_json)
        assert "edge_states" not in evidence
        assert evidence["explorer"] == "unknown"
        assert evidence["maia"] == "unknown"
        assert "real_game_repertoire_miss" in evidence
        assert len(record.evidence_json) < 256


def test_priority_retention_keeps_only_published_and_active_generation(tmp_path, monkeypatch, request):
    from app.services.priority_retention import execute_priority_retention_slice

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        _seed_repertoire(database_connection)
        for generation in (1, 2, 3):
            _insert_generation(database_connection, generation)
        database_connection.execute(
            "INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at) VALUES('storage-rep',2,?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        database_connection.execute(
            """INSERT INTO repertoire_priority_jobs(repertoire_id,generation,status,next_attempt_at,updated_at)
               VALUES('storage-rep',3,'running',?,?)""",
            (datetime.now(timezone.utc).isoformat(),) * 2,
        )
    database_writer.start()
    request.addfinalizer(database_writer.stop)
    from app.services.durable_tasks import enqueue_task
    enqueue_task("priority_retention", "storage-rep", {"repertoire_id": "storage-rep"})
    claimed = claim_task("priority_retention")
    assert claimed is not None
    assert execute_priority_retention_slice(claimed) is False
    with database.connection() as database_connection:
        generations = [row[0] for row in database_connection.execute(
            "SELECT DISTINCT generation FROM repertoire_card_priority_generations ORDER BY generation"
        )]
    assert generations == [2, 3]


def test_priority_retention_restarts_and_foreground_reads_continue(tmp_path, monkeypatch, request):
    from app.services.priority_retention import execute_priority_retention_slice

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        _seed_repertoire(database_connection)
        _insert_generation(database_connection, 1, tuple(f"old-{index}" for index in range(40)))
        _insert_generation(database_connection, 2)
        database_connection.execute(
            "INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at) VALUES('storage-rep',2,?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
    database_writer.start()
    request.addfinalizer(database_writer.stop)
    from app.services.durable_tasks import enqueue_task
    enqueue_task("priority_retention", "storage-rep", {"repertoire_id": "storage-rep"})
    claimed = claim_task("priority_retention")
    assert claimed is not None
    foreground_completed = threading.Event()

    def read_foreground():
        with database.read_connection() as foreground_database:
            assert foreground_database.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 42
        foreground_completed.set()

    foreground = threading.Thread(target=read_foreground)
    foreground.start()
    assert execute_priority_retention_slice(claimed) is True
    foreground.join(timeout=2)
    assert foreground_completed.is_set()
    requeue_interrupted_tasks()
    while next_claimed := claim_task("priority_retention"):
        if not execute_priority_retention_slice(next_claimed):
            break
    assert execute_priority_retention_slice(claimed) is False
    with database.connection() as database_connection:
        assert database_connection.execute(
            "SELECT COUNT(*) FROM repertoire_card_priority_generations WHERE generation=1"
        ).fetchone()[0] == 0
        assert database_connection.execute(
            "SELECT COUNT(*) FROM repertoire_card_priority_generations WHERE generation=2"
        ).fetchone()[0] == 2
