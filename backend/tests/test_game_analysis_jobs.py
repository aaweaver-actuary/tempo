from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient

from app import database
from app.main import app


def test_background_game_analysis_resumes_after_reload_and_submits_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "lichess:resume-game"
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(
                    id,provider,provider_game_id,username,played_at,speed,rated,color,result,start_fen,moves_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    game_id,
                    "lichess",
                    "resume-game",
                    "TempoPlayer",
                    datetime.now(timezone.utc).isoformat(),
                    "rapid",
                    1,
                    "white",
                    "1-0",
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    json.dumps(["e2e4", "e7e5"]),
                ),
            )
            db.execute(
                "INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
                (game_id, datetime.now(timezone.utc).isoformat()),
            )
        first_claim = client.post("/api/games/analysis/claim").json()["job"]
        assert first_claim["game_id"] == game_id
        with database.connection() as db:
            db.execute(
                "UPDATE game_analysis_jobs SET lease_expires_at=? WHERE game_id=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), game_id),
            )
        resumed_claim = client.post("/api/games/analysis/claim").json()["job"]
        assert resumed_claim["game_id"] == game_id
        assert resumed_claim["lease_id"] != first_claim["lease_id"]
        submission = {
            "lease_id": resumed_claim["lease_id"],
            "idempotency_key": f"{game_id}:analysis:1",
            "analysis_version": 1,
            "depth": 14,
            "engine_version": "Stockfish 19 WASM",
            "network_version": "test-network",
            "evaluations": [
                {
                    "ply": 0,
                    "before_cp": 20,
                    "after_cp": -120,
                    "depth": 14,
                    "best_move_uci": "d2d4",
                    "principal_variation": ["d2d4", "d7d5"],
                }
            ],
        }
        assert client.post(f"/api/games/{game_id}/analysis", json=submission).status_code == 200
        repeated = client.post(f"/api/games/{game_id}/analysis", json=submission)
        assert repeated.status_code == 200
        assert repeated.json()["idempotent"] is True
        assert client.post("/api/games/analysis/claim").json()["job"] is None
        with database.connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM game_move_analysis WHERE game_id=?", (game_id,)
            ).fetchone()[0] == 1
            stored = db.execute(
                "SELECT engine_version,network_version,best_move_uci FROM game_move_analysis WHERE game_id=?",
                (game_id,),
            ).fetchone()
        assert tuple(stored) == ("Stockfish 19 WASM", "test-network", "d2d4")


def test_paused_background_analysis_releases_its_lease_without_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "lichess:paused-game"
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(
                    id,provider,provider_game_id,username,played_at,speed,rated,color,result,start_fen,moves_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    game_id,
                    "lichess",
                    "paused-game",
                    "TempoPlayer",
                    datetime.now(timezone.utc).isoformat(),
                    "rapid",
                    1,
                    "white",
                    "1-0",
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    json.dumps(["e2e4"]),
                ),
            )
            db.execute(
                "INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
                (game_id, datetime.now(timezone.utc).isoformat()),
            )
        job = client.post("/api/games/analysis/claim").json()["job"]
        response = client.post(
            f"/api/games/analysis/{game_id}/release",
            json={"lease_id": job["lease_id"]},
        )
        assert response.json()["status"] == "queued"
        resumed = client.post("/api/games/analysis/claim").json()["job"]
        assert resumed["game_id"] == game_id
        assert resumed["lease_id"] != job["lease_id"]
        with database.connection() as db:
            assert db.execute(
                "SELECT last_error FROM game_analysis_jobs WHERE game_id=?", (game_id,)
            ).fetchone()[0] is None
