"""Offline recovery tool protection for authoritative Tempo records."""

from datetime import datetime, timezone
import json

import chess

from app import database
from scripts.reclaim_priority_storage import (
    compact, fingerprints, open_database, queue_order_checksum, reclaim,
)


def test_storage_reclaim_preserves_authoritative_fingerprints_and_compacts(tmp_path, monkeypatch):
    database_path = tmp_path / "tempo.db"
    monkeypatch.setattr(database, "DB_PATH", database_path)
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    repeated_edges = {f"edge-{index}": {"personal": "unknown"} for index in range(200)}
    evidence = json.dumps({
        "personal_games": 2,
        "explorer": "ready",
        "maia": "unknown",
        "edge_states": repeated_edges,
        "real_game_repertoire_miss": None,
    })
    with database.connection() as database_connection:
        database_connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('storage','Storage','source',?)",
            (now,),
        )
        database_connection.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date)
               VALUES('card','storage','prefix',?,'["e2e4"]','2026-01-01')""",
            (chess.STARTING_FEN,),
        )
        database_connection.execute(
            "INSERT INTO daily_queue(queue_date,card_id,position) VALUES('2026-01-01','card',0)"
        )
        database_connection.execute(
            "INSERT INTO repertoire_priority_publications(repertoire_id,generation,updated_at) VALUES('storage',3,?)",
            (now,),
        )
        database_connection.executemany(
            """INSERT INTO repertoire_card_priority_generations(
                   repertoire_id,generation,card_id,scoring_version,
                   completed_line_ids_json,frontier_decisions_json,evidence_json,updated_at)
               VALUES('storage',?,'card',2,'[]','[]',?,?)""",
            [(generation, evidence, now) for generation in (1, 2, 3)],
        )

    with open_database(database_path, read_only=False) as recovery_database:
        recovery_database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before_file_bytes = database_path.stat().st_size
        before = fingerprints(recovery_database)
        before_queue_order = queue_order_checksum(recovery_database)
        assert reclaim(recovery_database, batch_rows=1) == 2
        assert reclaim(recovery_database, batch_rows=1) == 0
        assert fingerprints(recovery_database) == before
        assert recovery_database.execute(
            "SELECT GROUP_CONCAT(generation) FROM repertoire_card_priority_generations"
        ).fetchone()[0] == "3"
        surviving_evidence = json.loads(recovery_database.execute(
            "SELECT evidence_json FROM repertoire_card_priority_generations"
        ).fetchone()[0])
        assert "edge_states" not in surviving_evidence
        assert surviving_evidence["personal_games"] == 2
        compact(recovery_database)
        assert fingerprints(recovery_database) == before
        assert queue_order_checksum(recovery_database) == before_queue_order
    assert database_path.stat().st_size < before_file_bytes
