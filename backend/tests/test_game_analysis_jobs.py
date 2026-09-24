from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.activity_gate import activity_gate


def test_stockfish_timeout_resumes_at_unfinished_position_without_saving_partial_game(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "lichess:position-timeout"
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                   VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,?)""",
                (game_id, datetime.now(timezone.utc).isoformat(),
                 "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                 json.dumps(["e2e4", "e7e5"])),
            )
            db.execute("INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
                       (game_id, datetime.now(timezone.utc).isoformat()))
        first = client.post("/api/games/analysis/position/claim",
                            headers={"X-Tempo-Engine-Worker": "docker"}).json()["job"]
        assert first["position_index"] == 0
        report = {"request": first["request"], "complete": True, "lines": [{
            "root_move_uci": "e2e4", "pv_uci": ["e2e4"],
            "score": {"cp": 20, "mate": None}, "depth": 8,
        }]}
        assert client.post(f"/api/games/analysis/position/{first['id']}/report",
                           headers={"X-Tempo-Engine-Worker": "docker"},
                           json={"lease_id": first["lease_id"], "report": report}).status_code == 200
        second = client.post("/api/games/analysis/position/claim",
                             headers={"X-Tempo-Engine-Worker": "docker"}).json()["job"]
        assert second["position_index"] == 1
        assert client.post(f"/api/games/analysis/position/{second['id']}/release",
                           headers={"X-Tempo-Engine-Worker": "docker"},
                           json={"lease_id": second["lease_id"]}).status_code == 200
        resumed = client.post("/api/games/analysis/position/claim",
                              headers={"X-Tempo-Engine-Worker": "docker"}).json()["job"]
        assert resumed["position_index"] == 1
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM game_move_analysis WHERE game_id=?", (game_id,)).fetchone()[0] == 0


def test_docker_game_scan_finalizes_complete_positions_once_with_actual_network(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "lichess:docker-scan"
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                   VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,?)""",
                (game_id, datetime.now(timezone.utc).isoformat(),
                 "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                 json.dumps(["e2e4", "e7e5"])),
            )
            db.execute("INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
                       (game_id, datetime.now(timezone.utc).isoformat()))
        expected_roots = ["e2e4", "e7e5", "g1f3"]
        for expected_index, root in enumerate(expected_roots):
            job = client.post("/api/games/analysis/position/claim",
                              headers={"X-Tempo-Engine-Worker": "docker"}).json()["job"]
            assert job["position_index"] == expected_index
            report = {"request": job["request"], "complete": True, "lines": [{
                "root_move_uci": root, "pv_uci": [root],
                "score": {"cp": 0, "mate": None}, "depth": 8,
            }]}
            result = client.post(f"/api/games/analysis/position/{job['id']}/report",
                                 headers={"X-Tempo-Engine-Worker": "docker"},
                                 json={"lease_id": job["lease_id"], "report": report})
            assert result.status_code == 200, result.text
        final_job = client.post("/api/games/analysis/position/claim",
                                headers={"X-Tempo-Engine-Worker": "docker"}).json()["job"]
        assert final_job["kind"] == "finalize"
        finalized = client.post("/api/games/analysis/position/finalize",
                                headers={"X-Tempo-Engine-Worker": "docker"},
                                json={"lease_id": final_job["lease_id"]})
        assert finalized.status_code == 200, finalized.text
        with database.connection() as db:
            rows = db.execute("SELECT network_version FROM game_move_analysis WHERE game_id=? ORDER BY ply",
                              (game_id,)).fetchall()
            assert len(rows) == 2
            assert {row["network_version"] for row in rows} == {"nn-61e7af4bb97d.nnue"}
            assert db.execute("SELECT status FROM game_analysis_jobs WHERE game_id=?", (game_id,)).fetchone()[0] == "complete"


def test_docker_game_report_rejects_wrong_history_and_depth_without_advancing(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "lichess:wrong-report"
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                   VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,?)""",
                (game_id, datetime.now(timezone.utc).isoformat(),
                 "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", json.dumps(["e2e4"])),
            )
            db.execute("INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
                       (game_id, datetime.now(timezone.utc).isoformat()))
        job = client.post("/api/games/analysis/position/claim",
                          headers={"X-Tempo-Engine-Worker": "docker"}).json()["job"]
        report = {"request": {**job["request"], "position_prefix_uci": ["e2e4"]},
                  "complete": True, "lines": [{"root_move_uci": "e2e4", "pv_uci": ["e2e4"],
                                                "score": {"cp": 20, "mate": None}, "depth": 7}]}
        response = client.post(f"/api/games/analysis/position/{job['id']}/report",
                               headers={"X-Tempo-Engine-Worker": "docker"},
                               json={"lease_id": job["lease_id"], "report": report})
        assert response.status_code == 422
        with database.connection() as db:
            assert db.execute("SELECT state FROM game_analysis_position_reports WHERE id=?",
                              (job["id"],)).fetchone()[0] == "leased"


def test_browser_activity_preempts_docker_search_without_database_access(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        response = client.post("/api/system/browser-activity",
                               headers={"X-Tempo-Work-Class": "background"})
        assert response.status_code == 200
        assert client.get("/api/system/foreground-active",
                          headers={"X-Tempo-Work-Class": "background"}).json()["active"] is True
    assert activity_gate.foreground_waiting


def test_legacy_browser_network_is_requeued_one_game_at_a_time_without_erasing_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    game_id = "lichess:legacy-network"
    with TestClient(app) as client:
        with database.connection() as db:
            db.execute(
                """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,
                   analysis_state,analysis_version,analysis_evidence_version)
                   VALUES(?,'lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,'["e2e4"]','ready',1,2)""",
                (game_id, datetime.now(timezone.utc).isoformat(),
                 "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
            )
            db.execute("""INSERT INTO game_move_analysis(game_id,ply,eval_before_cp,eval_after_cp,loss_cp,depth,
                       network_version) VALUES(?,0,0,0,0,8,'nn-1c0000000000.nnue')""", (game_id,))
            db.execute("""INSERT INTO game_analysis_jobs(game_id,analysis_version,analysis_evidence_version,
                       status,updated_at) VALUES(?,1,2,'complete',?)""",
                       (game_id, datetime.now(timezone.utc).isoformat()))
        first = client.post("/api/games/analysis/repair-provenance",
                            headers={"X-Tempo-Engine-Worker": "docker"})
        assert first.status_code == 200 and first.json()["requeued"] is True
        second = client.post("/api/games/analysis/repair-provenance",
                             headers={"X-Tempo-Engine-Worker": "docker"})
        assert second.json()["requeued"] is False
        with database.connection() as db:
            job = db.execute("SELECT status,analysis_version,analysis_evidence_version FROM game_analysis_jobs WHERE game_id=?",
                             (game_id,)).fetchone()
            assert tuple(job) == ("queued", 2, 3)
            assert db.execute("SELECT COUNT(*) FROM game_move_analysis WHERE game_id=?",
                              (game_id,)).fetchone()[0] == 1


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
