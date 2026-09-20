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


def test_game_analysis_persists_bounded_candidate_lines_and_evidence_versions(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "lichess:candidate-lines"
    start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(
                    id,provider,provider_game_id,username,played_at,speed,rated,color,result,start_fen,moves_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    game_id,
                    "lichess",
                    "candidate-lines",
                    "TempoPlayer",
                    datetime.now(timezone.utc).isoformat(),
                    "rapid",
                    1,
                    "white",
                    "1-0",
                    start_fen,
                    json.dumps(["e2e4"]),
                ),
            )
            db.execute(
                "INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
                (game_id, datetime.now(timezone.utc).isoformat()),
            )
        job = client.post("/api/games/analysis/claim").json()["job"]
        response = client.post(
            f"/api/games/{game_id}/analysis",
            json={
                "lease_id": job["lease_id"],
                "idempotency_key": f"{game_id}:analysis:1:evidence:2",
                "analysis_version": 1,
                "analysis_evidence_version": 2,
                "depth": 14,
                "engine_version": "Stockfish 19 WASM",
                "network_version": "test-network",
                "evaluations": [
                    {
                        "ply": 0,
                        "before_cp": 20,
                        "after_cp": 30,
                        "best_move_uci": "e2e4",
                        "principal_variation": ["e2e4", "e7e5"],
                        "candidate_lines": [
                            {"uci": "e2e4", "cp": 30, "pv": ["e2e4", "e7e5"]},
                            {"uci": "d2d4", "cp": 25, "pv": ["d2d4", "d7d5"]},
                            {"uci": "g1f3", "cp": 20, "pv": ["g1f3", "d7d5"]},
                        ],
                    }
                ],
            },
        )
        assert response.status_code == 200
        with database.connection() as db:
            parent = db.execute(
                """SELECT position_fen,engine_version,network_version
                   FROM game_move_analysis WHERE game_id=? AND ply=0""",
                (game_id,),
            ).fetchone()
            candidates = db.execute(
                """SELECT rank,candidate_uci,score_cp,principal_variation_json,depth,
                          position_fen,engine_version,network_version
                   FROM game_move_analysis_candidates WHERE game_id=? ORDER BY rank""",
                (game_id,),
            ).fetchall()
            versions = db.execute(
                """SELECT g.analysis_evidence_version,j.analysis_evidence_version
                   FROM imported_games g JOIN game_analysis_jobs j ON j.game_id=g.id
                   WHERE g.id=?""",
                (game_id,),
            ).fetchone()
        assert parent["position_fen"] == start_fen
        assert parent["engine_version"] == "Stockfish 19 WASM"
        assert parent["network_version"] == "test-network"
        assert [row["candidate_uci"] for row in candidates] == ["e2e4", "d2d4", "g1f3"]
        assert candidates[1]["score_cp"] == 25
        assert json.loads(candidates[1]["principal_variation_json"]) == ["d2d4", "d7d5"]
        assert candidates[0]["depth"] == 14
        assert candidates[0]["position_fen"] == start_fen
        assert candidates[0]["network_version"] == "test-network"
        assert tuple(versions) == (2, 2)


def test_game_analysis_rejects_illegal_candidate_pv_before_persistence(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "lichess:invalid-candidate"
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(
                    id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json
                ) VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,?)""",
                (
                    game_id,
                    datetime.now(timezone.utc).isoformat(),
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
            f"/api/games/{game_id}/analysis",
            json={
                "lease_id": job["lease_id"],
                "idempotency_key": f"{game_id}:analysis:1:evidence:2",
                "evaluations": [
                    {
                        "ply": 0,
                        "before_cp": 0,
                        "after_cp": 0,
                        "best_move_uci": "e2e4",
                        "principal_variation": ["e2e4"],
                        "candidate_lines": [
                            {"uci": "e2e4", "cp": 10, "pv": ["e2e4", "e2e5"]}
                        ],
                    }
                ],
            },
        )
        assert response.status_code == 422
        assert "candidate 1 at ply 0 move 2 is illegal" in response.json()["detail"]
        with database.connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM game_move_analysis WHERE game_id=?", (game_id,)
            ).fetchone()[0] == 0
            assert db.execute(
                "SELECT COUNT(*) FROM game_move_analysis_candidates WHERE game_id=?", (game_id,)
            ).fetchone()[0] == 0
