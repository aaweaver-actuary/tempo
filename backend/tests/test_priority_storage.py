"""Storage regressions for derived introduction priorities."""

from datetime import datetime, timezone
import json
import threading

import chess

from app import database
from app.services import introduction_priorities as priorities
from app.services.activity_gate import activity_gate
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
    with database.connection() as database_connection:
        _seed_repertoire(database_connection)
        database_connection.executemany(
            """INSERT INTO repertoire_card_priority_generations(
                   repertoire_id,generation,card_id,scoring_version,
                   completed_line_ids_json,completion_mass,frontier_decisions_json,
                   frontier_reach,priority_score,evidence_json,updated_at)
               VALUES(?,1,?,2,?,?,?,?,?,?,?)""",
            [
                (
                    record.repertoire_id, record.card_id,
                    record.completed_line_ids_json, record.completion_mass,
                    record.frontier_decisions_json, record.frontier_reach,
                    record.priority_score, record.evidence_json,
                    datetime.now(timezone.utc).isoformat(),
                )
                for record in records
            ],
        )
        database_connection.execute(
            """INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at)
               VALUES('storage-rep',1,?)""",
            (datetime.now(timezone.utc).isoformat(),),
        )
        compact_status = priorities.priority_status(database_connection, "storage-rep")
        database_connection.execute(
            """UPDATE repertoire_card_priority_generations
               SET evidence_json=json_set(evidence_json,'$.edge_states',json('{}'))
               WHERE repertoire_id='storage-rep'"""
        )
        legacy_status = priorities.priority_status(database_connection, "storage-rep")
    assert compact_status == legacy_status


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


def test_priority_publication_atomically_enqueues_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as database_connection:
        _seed_repertoire(database_connection)
        _insert_generation(database_connection, 1)
        database_connection.execute(
            "INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at) VALUES('storage-rep',1,?)",
            (now,),
        )
        database_connection.execute(
            """INSERT INTO repertoire_priority_jobs(repertoire_id,generation,status,next_attempt_at,updated_at)
               VALUES('storage-rep',2,'running',?,?)""",
            (now, now),
        )
    records = [
        priorities.PriorityRecord(
            "storage-rep", card_id, "[]", 0, "[]", 0, 0,
            '{"personal_games":0,"explorer":"unknown","maia":"unknown"}',
        )
        for card_id in ("card-a", "card-b")
    ]
    assert priorities.publish_priority_records(
        {"repertoire_id": "storage-rep", "generation": 2}, records,
    )
    with database.connection() as database_connection:
        assert database_connection.execute(
            "SELECT generation FROM repertoire_priority_publications WHERE repertoire_id='storage-rep'"
        ).fetchone()[0] == 2
        assert database_connection.execute(
            """SELECT state FROM background_tasks
               WHERE kind='priority_retention' AND deduplication_key='storage-rep'"""
        ).fetchone()[0] == "queued"


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
    assert execute_priority_retention_slice(claimed) is True
    with database.read_connection() as database_connection:
        assert database_connection.execute(
            "SELECT COUNT(*) FROM repertoire_card_priority_generations WHERE generation=1"
        ).fetchone()[0] == 40 - 16
    interrupted = claim_task("priority_retention")
    assert interrupted is not None
    requeue_interrupted_tasks()
    replayed = claim_task("priority_retention")
    assert replayed is not None
    assert replayed["lease_token"] != interrupted["lease_token"]
    assert execute_priority_retention_slice(interrupted) is False
    finished = threading.Event()

    def cleanup_while_foreground_active():
        execute_priority_retention_slice(replayed)
        finished.set()

    with activity_gate.foreground():
        with database.read_connection() as foreground_database:
            assert foreground_database.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 42
        worker = threading.Thread(target=cleanup_while_foreground_active)
        worker.start()
        assert not finished.wait(timeout=0.05)
    worker.join(timeout=2)
    assert finished.is_set()
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
