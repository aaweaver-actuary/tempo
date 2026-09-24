"""Issue 12 and 13 durable candidate, approval, and scheduling regressions."""

from dataclasses import asdict
from datetime import date, datetime, timezone
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from fastapi.testclient import TestClient

from app import database
from app.main import app, materialize_daily_queue
from app.services.threat_detection import find_defensive_knight_forks
from app.services.threat_models import GameSnapshot, SourceLine
from app.services.threat_pipeline import (
    _upsert_seed, execute_threat_scan_slice, execute_threat_validation,
)
from app.services import threat_pipeline
from app.services.threat_validation import AnalysisLine, AnalysisReport, EngineScore


FEN = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"


def seed_candidate():
    game = GameSnapshot("lichess:defense-one", 1, FEN, ("a2a3",), "white")
    seed = find_defensive_knight_forks(
        game, SourceLine("engine", 1, ("b4c2", "e1d2", "c2a1", "d2c1"))
    )[0]
    with database.connection() as db:
        db.execute(
            """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
                 result,start_fen,moves_json,analysis_state,analysis_version)
               VALUES(?,'lichess','learner',?,'rapid',1,'white','0-1',?,?,'ready',1)""",
            (game.game_id, datetime.now(timezone.utc).isoformat(), FEN,
             json.dumps(game.moves_uci)),
        )
        _upsert_seed(db, game, seed)
        candidate = db.execute(
            "SELECT * FROM threat_training_candidates WHERE game_id=?", (game.game_id,)
        ).fetchone()
        assert candidate is not None
        candidate_id = candidate["id"]
        requests = [dict(row) for row in db.execute(
            """SELECT relation.role,request.id,request.request_json
               FROM threat_candidate_requests relation
               JOIN threat_analysis_requests request ON request.id=relation.request_id
               WHERE relation.candidate_id=?""", (candidate_id,),
        )]
        for request in requests:
            raw_request = json.loads(request["request_json"])
            from app.services.threat_pipeline import _request_from_json
            typed_request = _request_from_json(raw_request)
            if request["role"] == "best":
                line = AnalysisLine("e1f2", ("e1f2", "e8e7"), EngineScore(cp=0), 14)
            else:
                line = AnalysisLine("a2a3", ("a2a3", "b4c2", "e1d2", "c2a1", "d2c1"),
                                    EngineScore(cp=-180), 14)
            report = AnalysisReport(typed_request, (line,), True)
            db.execute(
                "UPDATE threat_analysis_requests SET state='complete',report_json=? WHERE id=?",
                (json.dumps(asdict(report)), request["id"]),
            )
    execute_threat_validation({"payload": {"candidate_id": candidate_id}})
    return candidate_id


def test_issue12_reprocessing_preserves_identity_dismissal_and_no_fsrs(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        with database.connection() as db:
            candidate = db.execute(
                "SELECT * FROM threat_training_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            assert candidate["validation_state"] == "engine_supported"
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM tactical_opportunities").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM game_findings WHERE kind='defensive tactical threat'").fetchone()[0] == 1
        assert client.post(f"/api/defensive-threats/candidates/{candidate_id}/dismiss").status_code == 200
        game = GameSnapshot("lichess:defense-one", 1, FEN, ("a2a3",), "white")
        seed = find_defensive_knight_forks(
            game, SourceLine("engine", 1, ("b4c2", "e1d2", "c2a1", "d2c1"))
        )[0]
        with database.connection() as db:
            _upsert_seed(db, game, seed)
            same = db.execute("SELECT * FROM threat_training_candidates WHERE id=?", (candidate_id,)).fetchone()
            assert same["dismissed_at"] is not None
            assert same["exercise_revision"] == 1
            assert db.execute("SELECT COUNT(*) FROM threat_training_candidates").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_issue12_played_fork_evidence_resurfaces_dismissed_engine_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        assert client.post(f"/api/defensive-threats/candidates/{candidate_id}/dismiss").status_code == 200
        played_game = GameSnapshot("lichess:defense-one", 1, FEN,
                                   ("a2a3", "b4c2"), "white")
        played_seed = find_defensive_knight_forks(
            played_game, SourceLine("played", 1, ("b4c2",))
        )[0]
        with database.connection() as db:
            _upsert_seed(db, played_game, played_seed)
            changed = db.execute(
                "SELECT * FROM threat_training_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            assert changed["dismissed_at"] is None
            assert changed["exercise_revision"] == 2
            assert changed["validation_state"] == "needs_analysis"
            assert db.execute("SELECT COUNT(*) FROM threat_training_candidates").fetchone()[0] == 1


def test_issue13_approved_rubric_grades_unlisted_move_and_schedules_once(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        approved = client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve")
        assert approved.status_code == 200, approved.text
        card_id = approved.json()["card_id"]
        with database.connection() as db:
            materialize_daily_queue(db, date.today().isoformat())
            queue_entry = db.execute(
                "SELECT id FROM daily_queue WHERE card_id=? AND status='queued'", (card_id,)
            ).fetchone()
            assert queue_entry is not None
            entry_id = queue_entry["id"]
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        exercise = client.get(f"/api/defense-exercises/{candidate_id}")
        assert exercise.status_code == 200
        assert exercise.json()["prompt"] == "Choose your move."
        illegal = client.post(f"/api/defense-exercises/{candidate_id}/attempt", json={
            "attempt_id": "illegal-attempt", "exercise_revision": 1,
            "queue_entry_id": entry_id, "move_uci": "a2a5",
        })
        assert illegal.status_code == 200 and illegal.json()["status"] == "illegal"
        payload = {"attempt_id": "attempt-one", "exercise_revision": 1,
                   "queue_entry_id": entry_id, "move_uci": "a2a4"}
        pending = client.post(f"/api/defense-exercises/{candidate_id}/attempt", json=payload)
        assert pending.status_code == 200, pending.text
        assert pending.json()["status"] == "needs_analysis"
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
            request_row = db.execute(
                """SELECT request.id,request.request_json FROM threat_candidate_requests relation
                   JOIN threat_analysis_requests request ON request.id=relation.request_id
                   WHERE relation.candidate_id=? AND relation.role='attempt'""",
                (candidate_id,),
            ).fetchone()
            from app.services.threat_pipeline import _request_from_json
            request = _request_from_json(json.loads(request_row["request_json"]))
            ambiguous_report = AnalysisReport(request, (
                AnalysisLine("a2a4", ("a2a4", "e8e7"), EngineScore(cp=-60), 14),
            ), True)
            db.execute(
                "UPDATE threat_analysis_requests SET state='complete',report_json=? WHERE id=?",
                (json.dumps(asdict(ambiguous_report)), request_row["id"]),
            )
        ambiguous = client.post(f"/api/defense-exercises/{candidate_id}/attempt", json=payload)
        assert ambiguous.status_code == 200 and ambiguous.json()["status"] == "ambiguous"
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        payload = {**payload, "attempt_id": "attempt-two", "move_uci": "e1f2"}
        first = client.post(f"/api/defense-exercises/{candidate_id}/attempt", json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "correct"
        replay = client.post(f"/api/defense-exercises/{candidate_id}/attempt", json=payload)
        assert replay.status_code == 200 and replay.json()["idempotent"]
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card_id,)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM defense_attempts WHERE attempt_id='attempt-two'").fetchone()[0] == 1
        stale = client.post(f"/api/defense-exercises/{candidate_id}/attempt", json={
            **payload, "attempt_id": "attempt-three", "exercise_revision": 2,
        })
        assert stale.status_code == 409


def test_issue12_foreground_review_completes_during_threat_scan_and_restart_replay_is_idempotent(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    game_id = "lichess:concurrent-threat"
    with database.connection() as db:
        db.execute(
            """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
                 result,start_fen,moves_json,analysis_state,analysis_version)
               VALUES(?,'lichess','learner',?,'rapid',1,'white','0-1',?,?,'ready',1)""",
            (game_id, now, FEN, json.dumps(["a2a3", "b4c2"])),
        )
        db.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('tactic-test','Tactic','test',?)",
            (now,),
        )
        db.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                 content_type,trained_color)
               VALUES('foreground-card','tactic-test','checkpoint',?,'["a2a3"]','learning',?,
                      'tactic','white')""",
            (FEN, date.today().isoformat()),
        )
        db.execute(
            "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'foreground-card',0)",
            (date.today().isoformat(),),
        )
        entry_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        payload = {"game_id": game_id, "analysis_version": 1, "cursor": 1}
        db.execute(
            """INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,
                 state,phase,payload_json,next_attempt_at,lease_token,created_at,updated_at)
               VALUES('threat-task','defensive_threat_scan',?,1,145,'leased','claimed',?,?,?, ?,?)""",
            (game_id, json.dumps(payload), now, "lease-one", now, now),
        )
    task = {"id": "threat-task", "generation": 1, "lease_token": "lease-one", "payload": payload}
    started = threading.Event()
    release = threading.Event()
    real_detector = threat_pipeline.find_defensive_knight_forks

    def paused_detector(game, source_line):
        started.set()
        assert release.wait(5)
        return real_detector(game, source_line)

    monkeypatch.setattr(threat_pipeline, "find_defensive_knight_forks", paused_detector)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute_threat_scan_slice, task)
        assert started.wait(2)
        client = TestClient(app)
        began = time.perf_counter()
        response = client.post(
            "/api/cards/foreground-card/review",
            json={"outcome": "correct", "queue_entry_id": entry_id},
        )
        elapsed = time.perf_counter() - began
        assert response.status_code == 200, response.text
        assert elapsed < 1.0
        release.set()
        assert future.result(timeout=5)
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM threat_training_candidates").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='foreground-card'").fetchone()[0] == 1
        db.execute(
            """UPDATE background_tasks SET generation=3,state='leased',lease_token='lease-two',
                 payload_json=? WHERE id='threat-task'""",
            (json.dumps(payload),),
        )
    database.initialize()  # Simulate reopening the persisted schema after a restart.
    assert execute_threat_scan_slice({
        "id": "threat-task", "generation": 3, "lease_token": "lease-two", "payload": payload,
    })
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM threat_training_candidates").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM game_findings WHERE kind='defensive tactical threat'").fetchone()[0] == 1


def test_issue12_new_analysis_supersedes_approved_defense_and_blocks_its_card(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        approved = client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve")
        assert approved.status_code == 200
        card_id = approved.json()["card_id"]
        replacement = client.post("/api/games/lichess:defense-one/analysis", json={
            "analysis_version": 2, "depth": 14,
            "engine_version": "Stockfish 19 WASM", "network_version": "test-network",
            "evaluations": [],
        })
        assert replacement.status_code == 200, replacement.text
        assert client.get(f"/api/defense-exercises/{candidate_id}").status_code == 409
        assert client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve").status_code == 409
        assert client.get(
            "/api/defensive-threats/candidates", params={"game_id": "lichess:defense-one"}
        ).json()["candidates"] == []
        with database.connection() as db:
            assert db.execute(
                "SELECT pending_validation FROM cards WHERE id=?", (card_id,)
            ).fetchone()[0] == 1
            materialize_daily_queue(db, date.today().isoformat())
            assert db.execute(
                "SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND status='queued'", (card_id,)
            ).fetchone()[0] == 0


def test_issue11_analysis_report_requires_matching_lease_and_request(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        with database.connection() as db:
            request_row = db.execute(
                """SELECT request.id,request.request_json FROM threat_candidate_requests relation
                   JOIN threat_analysis_requests request ON request.id=relation.request_id
                   WHERE relation.candidate_id=? AND relation.role='best'""",
                (candidate_id,),
            ).fetchone()
            db.execute(
                "UPDATE threat_analysis_requests SET state='queued',report_json=NULL WHERE id=?",
                (request_row["id"],),
            )
            db.execute(
                "UPDATE threat_training_candidates SET validation_state='needs_analysis' WHERE id=?",
                (candidate_id,),
            )
        claim = client.post("/api/defensive-threats/analysis/claim")
        assert claim.status_code == 200
        job = claim.json()["job"]
        assert job["id"] == request_row["id"]
        from app.services.threat_pipeline import _request_from_json
        request = _request_from_json(job["request"])
        report = asdict(AnalysisReport(request, (
            AnalysisLine("e1f2", ("e1f2", "e8e7"), EngineScore(cp=0), 14),
        ), True))
        endpoint = f"/api/defensive-threats/analysis/{job['id']}/report"
        assert client.post(endpoint, json={"lease_id": "wrong", "report": report}).status_code == 409
        changed_report = {**report, "request": {**report["request"], "depth": 16}}
        assert client.post(endpoint, json={
            "lease_id": job["lease_id"], "report": changed_report,
        }).status_code == 409
        saved = client.post(endpoint, json={"lease_id": job["lease_id"], "report": report})
        assert saved.status_code == 200, saved.text
        assert client.post(endpoint, json={"lease_id": job["lease_id"], "report": report}).status_code == 200
        with database.connection() as db:
            assert db.execute(
                "SELECT state FROM threat_analysis_requests WHERE id=?", (job["id"],)
            ).fetchone()[0] == "complete"
