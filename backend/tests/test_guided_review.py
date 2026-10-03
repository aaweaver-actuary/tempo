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
            f"/api/guided-reviews/{started['id']}/attempt", json={"move_uci": "e2e4", "finding_id": started["current"]["finding_id"]}
        )
        assert attempt.status_code == 200
        assert attempt.json()["correct"] is True
        resumed = client.post("/api/games/guided/guided-review").json()
        assert resumed["id"] == started["id"]
        assert resumed["current_index"] == 1
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM guided_review_attempts").fetchone()[0] == 1



def _seed_stale_review(database_connection, finding_ids, current_index=0):
    _seed_review_game(database_connection)
    database_connection.execute("UPDATE game_findings SET kind='repertoire lapse' WHERE id='finding-0'")
    database_connection.execute(
        "INSERT INTO guided_review_sessions(id,game_id,analysis_version,finding_ids_json,current_index,status,created_at,updated_at) VALUES('stale-review','guided',2,?,?,'active','2026-10-03','2026-10-03')",
        (json.dumps(finding_ids), current_index),
    )
    database_connection.execute("UPDATE repertoire_game_scope SET generation=generation+1 WHERE id=1")


def test_guided_review_hidden_current_get_and_submit_grade_same_finding_without_500(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_stale_review(db, ['finding-0', 'finding-1'])
        session = client.get('/api/guided-reviews/stale-review').json()
        assert session['current']['finding_id'] == 'finding-1'
        response = client.post('/api/guided-reviews/stale-review/attempt',
                               json={'move_uci': 'e2e4', 'finding_id': 'finding-1'})
        assert response.status_code == 200
        assert response.json()['revealed']['finding_id'] == 'finding-1'
        assert response.json()['session']['status'] == 'complete'
        with database.connection() as db:
            assert db.execute("SELECT finding_id FROM guided_review_attempts WHERE session_id='stale-review'").fetchone()[0] == 'finding-1'


def test_guided_review_hidden_completed_finding_remaps_index_and_preserves_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_stale_review(db, ['finding-0', 'finding-1', 'finding-2'], 2)
            db.execute("INSERT INTO guided_review_attempts VALUES('stale-review','finding-0','e2e4',1,'2026-10-03')")
        response = client.get('/api/guided-reviews/stale-review')
        assert response.status_code == 200
        assert response.json()['current_index'] == 1
        assert response.json()['current']['finding_id'] == 'finding-2'
        assert response.json()['attempts'][0]['finding_id'] == 'finding-0'
        with database.read_connection() as db:
            session = db.execute("SELECT * FROM guided_review_sessions WHERE id='stale-review'").fetchone()
            assert json.loads(session['finding_ids_json']) == ['finding-1', 'finding-2']
            assert session['current_index'] == 1


def test_guided_review_all_remaining_hidden_post_commits_completion_and_resume_is_not_stranded(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_stale_review(db, ['finding-1', 'finding-0'], 1)
        attempted = client.post('/api/guided-reviews/stale-review/attempt',
                                json={'move_uci': 'e2e4', 'finding_id': 'finding-0'})
        assert attempted.status_code == 404
        with database.read_connection() as db:
            session = db.execute("SELECT * FROM guided_review_sessions WHERE id='stale-review'").fetchone()
            assert session['status'] == 'complete'
            assert session['current_index'] == 1
            assert db.execute("SELECT COUNT(*) FROM guided_review_attempts").fetchone()[0] == 0
        assert client.get('/api/guided-reviews/stale-review').json()['current'] is None
        resumed = client.post('/api/games/guided/guided-review').json()
        assert resumed['id'] != 'stale-review'


def test_guided_review_scope_change_between_display_and_attempt_rejects_old_target(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_stale_review(db, ['finding-0', 'finding-1'])
        response = client.post('/api/guided-reviews/stale-review/attempt',
                               json={'move_uci': 'e2e4', 'finding_id': 'finding-0'})
        assert response.status_code == 409
        with database.read_connection() as db:
            assert json.loads(db.execute("SELECT finding_ids_json FROM guided_review_sessions WHERE id='stale-review'").fetchone()[0]) == ['finding-1']
            assert db.execute("SELECT COUNT(*) FROM guided_review_attempts").fetchone()[0] == 0
        session = client.post('/api/games/guided/guided-review').json()
        assert session['id'] == 'stale-review'
        assert session['current']['finding_id'] == 'finding-1'


def test_guided_review_all_findings_hidden_get_returns_complete_session(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_stale_review(db, ['finding-0'])
        session = client.get('/api/guided-reviews/stale-review').json()
        assert session['status'] == 'complete'
        assert session['current'] is None
        assert session['total'] == session['current_index'] == 0
