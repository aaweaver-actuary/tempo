import json
import time
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.game_sync_coordinator import coordinator, enqueue_game_derivation


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_review_position_filters_games_and_summarizes_played_moves(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            for identifier, moves, result in (
                ("one", ["e2e4", "e7e5"], "won"),
                ("two", ["d2d4", "d7d5"], "lost"),
            ):
                db.execute(
                    """INSERT INTO imported_games(id,provider,provider_game_id,username,played_at,speed,rated,color,result,start_fen,moves_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        identifier,
                        "lichess",
                        identifier,
                        "TempoPlayer",
                        datetime.now(timezone.utc).isoformat(),
                        "rapid",
                        1,
                        "white",
                        result,
                        START,
                        json.dumps(moves),
                    ),
                )
        for identifier in ("one", "two"):
            enqueue_game_derivation(identifier)
        coordinator.wake()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with database.connection() as db:
                complete = db.execute(
                    "SELECT COUNT(*) FROM game_derivation_jobs WHERE status='complete'"
                ).fetchone()[0]
            if complete == 2:
                break
            time.sleep(0.01)
        summary = client.get("/api/games/position-summary", params={"fen": START}).json()
        assert summary["encounters"] == 2
        assert {move["move_uci"] for move in summary["moves"]} == {"e2e4", "d2d4"}
        filtered = client.get("/api/games/summary", params={"fen": START}).json()
        assert filtered["total"] == 2

        after_e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        assert client.get("/api/games/summary", params={"fen": after_e4}).json()["total"] == 1
