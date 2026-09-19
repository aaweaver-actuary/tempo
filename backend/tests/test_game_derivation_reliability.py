from datetime import datetime, timezone
import json
import sqlite3

from fastapi.testclient import TestClient

from app import database
from app.main import app
import app.services.game_sync_coordinator as game_sync_coordinator


START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def seed_running_derivation(game_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as connection:
        connection.execute(
            """INSERT INTO imported_games(
                   id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json
               ) VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,?)""",
            (game_id, now, START_FEN, json.dumps(["e2e4"])),
        )
        connection.execute(
            """INSERT INTO game_derivation_jobs(game_id,status,attempts,updated_at)
               VALUES(?,'running',3,?)""",
            (game_id, now),
        )


def test_background_database_contention_retries_without_blocking_foreground_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        seed_running_derivation("busy-derivation")

        def raise_database_busy(_game_id: str) -> None:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(
            game_sync_coordinator, "_index_game_positions", raise_database_busy
        )
        game_sync_coordinator._execute_derivation("busy-derivation")
        with database.connection() as connection:
            job = connection.execute(
                "SELECT status,next_attempt_at,last_error FROM game_derivation_jobs WHERE game_id='busy-derivation'"
            ).fetchone()
        assert job["status"] == "queued"
        assert job["next_attempt_at"] is not None
        assert "locked" in job["last_error"]
        assert client.put(
            "/api/settings", json={**client.get("/api/settings").json(), "new_cards_per_day": 9}
        ).status_code == 200


def test_derivation_backlog_resumes_after_restart_without_duplicate_findings(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "restart-derivation"
    with TestClient(app):
        seed_running_derivation(game_id)
        with database.connection() as connection:
            connection.execute(
                """INSERT INTO game_move_analysis(
                       game_id,ply,eval_before_cp,eval_after_cp,loss_cp,label,depth,
                       best_move_uci,principal_variation_json
                   ) VALUES(?,0,25,-125,150,'major mistake',14,'d2d4',?)""",
                (game_id, json.dumps(["d2d4", "d7d5"])),
            )
    with TestClient(app):
        # Startup returns interrupted work to the durable queue. Deterministic finding IDs
        # make a repeated derivation an upsert rather than a duplicate.
        with database.connection() as connection:
            status = connection.execute(
                "SELECT status FROM game_derivation_jobs WHERE game_id=?", (game_id,)
            ).fetchone()[0]
        assert status in {"queued", "running", "complete"}
        game_sync_coordinator._execute_derivation(game_id)
        with database.connection() as connection:
            first_finding_count = connection.execute(
                "SELECT COUNT(*) FROM game_findings WHERE game_id=?", (game_id,)
            ).fetchone()[0]
        game_sync_coordinator._execute_derivation(game_id)
        with database.connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM game_findings WHERE game_id=?", (game_id,)
            ).fetchone()[0] == first_finding_count
            assert first_finding_count > 0
            assert connection.execute(
                "SELECT status FROM game_derivation_jobs WHERE game_id=?", (game_id,)
            ).fetchone()[0] == "complete"
