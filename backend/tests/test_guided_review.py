import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import database
from app.main import app


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _seed_review_game(db):
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO imported_games(
               id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,analysis_version
           ) VALUES('guided','lichess','TempoPlayer',?,'rapid',1,'white','1-0',?,'[\"e2e4\"]',2)""",
        (now, START),
    )
    for index in range(7):
        ply = index if index < 6 else 0
        kind = "tactical miss" if index % 2 else "major mistake"
        evidence = {
            "fen": START,
            "loss_cp": 100 + index * 25,
            "best_move_uci": "e2e4",
            "actual_move_uci": "d2d4",
            "principal_variation": ["e2e4", "e7e5"],
        }
        db.execute(
            """INSERT INTO game_findings(
                   id,game_id,analysis_version,ply,kind,confidence,evidence_json,motif,created_at,updated_at
               ) VALUES(?,'guided',2,?,?,0.95,?,'fork',?,?)""",
            (f"finding-{index}", ply, kind, json.dumps(evidence), now, now),
        )


def test_guided_review_ranks_and_deduplicates_top_five_actionable_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_review_game(db)
        session = client.post("/api/games/guided/guided-review").json()
        assert session["total"] == 5
        with database.connection() as db:
            finding_ids = json.loads(db.execute(
                "SELECT finding_ids_json FROM guided_review_sessions WHERE id=?", (session["id"],)
            ).fetchone()[0])
            plies = [db.execute("SELECT ply FROM game_findings WHERE id=?", (finding_id,)).fetchone()[0] for finding_id in finding_ids]
            assert len(plies) == len(set(plies)) == 5
            assert 5 in plies


def test_guided_review_resumes_and_correction_does_not_change_fsrs(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_review_game(db)
        started = client.post("/api/games/guided/guided-review").json()
        attempt = client.post(
            f"/api/guided-reviews/{started['id']}/attempt", json={"move_uci": "e2e4"}
        )
        assert attempt.status_code == 200
        assert attempt.json()["correct"] is True
        resumed = client.post("/api/games/guided/guided-review").json()
        assert resumed["id"] == started["id"]
        assert resumed["current_index"] == 1
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM guided_review_attempts").fetchone()[0] == 1

