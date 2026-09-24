"""Issue 12 and 13 durable candidate, approval, and scheduling regressions."""

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import chess

from fastapi.testclient import TestClient

from app import database
from app.main import app, materialize_daily_queue
from app.services.threat_detection import find_defensive_knight_forks
from app.services.threat_models import GameSnapshot, SourceLine
from app.services.threat_pipeline import (
    _upsert_seed, claim_analysis_request, execute_threat_scan_slice, execute_threat_validation,
)
from app.services.background_activity import list_activity, set_control
from app.services import threat_pipeline
from app.services.threat_validation import AnalysisLine, AnalysisReport, EngineScore


FEN = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"


def seed_candidate(*, game_id: str = "lichess:defense-one", fen: str = FEN,
                   played_at: str | None = None):
    game = GameSnapshot(game_id, 1, fen, ("a2a3",), "white")
    seed = find_defensive_knight_forks(
        game, SourceLine("engine", 1, ("b4c2", "e1d2", "c2a1", "d2c1"))
    )[0]
    with database.connection() as db:
        db.execute("UPDATE settings SET defense_new_cards_per_day=0 WHERE id=1")
        db.execute(
            """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
                 result,start_fen,moves_json,analysis_state,analysis_version)
               VALUES(?,'lichess','learner',?,'rapid',1,'white','0-1',?,?,'ready',1)""",
            (game.game_id, played_at or datetime.now(timezone.utc).isoformat(), fen,
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


def seed_validated_control():
    fen = "4k3/8/8/8/1n2B3/8/P7/R3K3 w Q - 0 1"
    game = GameSnapshot("lichess:defense-control", 1, fen, ("a2a3",), "white")
    seed = find_defensive_knight_forks(
        game, SourceLine("engine", 1, ("b4c2", "e1d2", "c2a1", "d2c1")),
    )[0]
    with database.connection() as db:
        db.execute("UPDATE settings SET defense_new_cards_per_day=0 WHERE id=1")
        db.execute(
            """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
                 result,start_fen,moves_json,analysis_state,analysis_version)
               VALUES(?,'lichess','learner',?,'rapid',1,'white','1-0',?,?,'ready',1)""",
            (game.game_id, datetime.now(timezone.utc).isoformat(), fen,
             json.dumps(game.moves_uci)),
        )
        _upsert_seed(db, game, seed)
        candidate_id = db.execute(
            "SELECT id FROM threat_training_candidates WHERE game_id=?", (game.game_id,),
        ).fetchone()[0]
        requests = [dict(row) for row in db.execute(
            """SELECT relation.role,request.id,request.request_json
               FROM threat_candidate_requests relation
               JOIN threat_analysis_requests request ON request.id=relation.request_id
               WHERE relation.candidate_id=?""", (candidate_id,),
        )]
        for item in requests:
            request = threat_pipeline._request_from_json(json.loads(item["request_json"]))
            if item["role"] == "historical":
                lines = (AnalysisLine("a2a3", ("a2a3", "b4c2", "e1d2", "c2a1", "d2c1"),
                                      EngineScore(cp=-10), 14),)
            else:
                board = chess.Board(fen)
                generated = []
                for root in sorted(board.legal_moves, key=lambda move: move.uci())[:5]:
                    position = board.copy()
                    variation = [root.uci()]
                    position.push(root)
                    while len(variation) < 5:
                        reply = sorted(position.legal_moves, key=lambda move: move.uci())[0]
                        variation.append(reply.uci())
                        position.push(reply)
                    generated.append(AnalysisLine(root.uci(), tuple(variation), EngineScore(cp=0), 14))
                lines = tuple(generated)
            db.execute(
                "UPDATE threat_analysis_requests SET state='complete',report_json=? WHERE id=?",
                (json.dumps(asdict(AnalysisReport(request, lines, True))), item["id"]),
            )
    execute_threat_validation({"payload": {"candidate_id": candidate_id}})
    return candidate_id


def seed_reported_rc4_candidate():
    fen = "8/k1p2p2/1p2p3/1P2Pn2/PR6/8/3r1PKP/8 w - - 3 36"
    game = GameSnapshot("lichess:reported-rc4", 1, fen, ("b4c4",), "white")
    seed = find_defensive_knight_forks(
        game, SourceLine("engine", 1, ("f5e3", "g2g3", "e3c4", "f2f4")),
    )[0]
    with database.connection() as db:
        db.execute("UPDATE settings SET defense_new_cards_per_day=0 WHERE id=1")
        db.execute(
            """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
                 result,start_fen,moves_json,analysis_state,analysis_version)
               VALUES(?,'lichess','learner',?,'rapid',1,'white','0-1',?,?,'ready',1)""",
            (game.game_id, datetime.now(timezone.utc).isoformat(), fen,
             json.dumps(game.moves_uci)),
        )
        _upsert_seed(db, game, seed)
        candidate_id = db.execute(
            "SELECT id FROM threat_training_candidates WHERE game_id=?", (game.game_id,),
        ).fetchone()[0]
        for item in db.execute(
            """SELECT relation.role,request.id,request.request_json
               FROM threat_candidate_requests relation JOIN threat_analysis_requests request
                 ON request.id=relation.request_id WHERE relation.candidate_id=?""",
            (candidate_id,),
        ).fetchall():
            request = threat_pipeline._request_from_json(json.loads(item["request_json"]))
            line = (AnalysisLine("g2f3", ("g2f3", "a7b7"), EngineScore(cp=-405), 14)
                    if item["role"] == "best" else
                    AnalysisLine("b4c4", ("b4c4", "f5e3", "g2g3", "e3c4", "f2f4"),
                                 EngineScore(cp=-841), 14))
            db.execute(
                "UPDATE threat_analysis_requests SET state='complete',report_json=? WHERE id=?",
                (json.dumps(asdict(AnalysisReport(request, (line,), True))), item["id"]),
            )
    execute_threat_validation({"payload": {"candidate_id": candidate_id}})
    return candidate_id


def test_reported_rc4_preview_grades_c4_only_after_proposed_move(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_reported_rc4_candidate()
        card_id = client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve").json()["card_id"]
        with database.connection() as db:
            materialize_daily_queue(db, date.today().isoformat())
            entry_id = db.execute("SELECT id FROM daily_queue WHERE card_id=?", (card_id,)).fetchone()[0]
        exercise = client.get(f"/api/defense-exercises/{candidate_id}").json()
        assert exercise["proposed_move_san"] == "Rc4"
        assert chess.Board(exercise["preview_fen"]).piece_at(chess.C4) == chess.Piece(chess.ROOK, chess.WHITE)
        assert chess.Board(exercise["preview_fen"]).turn == chess.BLACK
        stale = client.post(f"/api/defense-exercises/{candidate_id}/recognition", json={
            "attempt_id": "stale-rc4", "exercise_revision": exercise["exercise_revision"],
            "rubric_version": 2, "queue_entry_id": entry_id,
            "dangerous_piece_square": "f5", "destination_square": "e3",
            "king_square": "g2", "major_square": "b4", "consequence": "none",
        })
        assert stale.status_code == 409
        answer = client.post(f"/api/defense-exercises/{candidate_id}/recognition", json={
            "attempt_id": "new-rc4", "exercise_revision": exercise["exercise_revision"],
            "rubric_version": 3, "queue_entry_id": entry_id,
            "dangerous_piece_square": "f5", "destination_square": "e3",
            "king_square": "g2", "major_square": "c4", "consequence": "checking_fork",
        })
        assert answer.status_code == 200, answer.text
        assert answer.json()["recognition_correct"] is True
        assert answer.json()["feedback"]["knight_route"] == [
            {"from_square": "f5", "to_square": "e3"},
        ]
        assert answer.json()["feedback"]["refutation_uci"][:4] == [
            "b4c4", "f5e3", "g2g3", "e3c4",
        ]


def test_reported_rc4_audit_preserves_attempts_and_removes_invalid_review_effect(tmp_path, monkeypatch, request):
    from app.services.durable_tasks import enqueue_task_in_transaction
    from app.services.database_executor import database_writer
    from app.models import DefenseRecognitionRequest
    from app.services.review_service import apply_scheduling_review
    from app.services.threat_training import (
        approve_defense_candidate, execute_defense_rubric_audit_slice,
        submit_defense_recognition,
    )
    import pytest

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    database_writer.start()
    request.addfinalizer(database_writer.stop)
    candidate_id = seed_reported_rc4_candidate()
    card_id = approve_defense_candidate(candidate_id)
    with database.connection() as db:
        materialize_daily_queue(db, date.today().isoformat())
        entry_id = db.execute("SELECT id FROM daily_queue WHERE card_id=?", (card_id,)).fetchone()[0]
        db.execute(
            """INSERT INTO defense_recognition_submissions(
                 attempt_id,candidate_id,queue_entry_id,exercise_revision,answer_json,
                 recognition_correct,created_at) VALUES(?,?,?,?,?,0,?)""",
            ("reported-raw-answer", candidate_id, entry_id, 1,
             json.dumps({"major_square": "b4", "consequence": "none"}),
             datetime.now(timezone.utc).isoformat()),
        )
        apply_scheduling_review(
            db, card_id, "again", guided=False, source_kind="study",
            source_ref="defense:old-rc4-review", light_first_interval_days=7,
            reviewed_at=datetime.now(timezone.utc), review_day=date.today(),
        )
        db.execute(
            """INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,queue_entry_id,
                 exercise_revision,move_uci,grade_json,reviewed_at)
               VALUES('old-rc4-review',?,?,?,?,?,?,?)""",
            (candidate_id, card_id, entry_id, 1, "g2f3", '{"status":"incorrect"}',
             datetime.now(timezone.utc).isoformat()),
        )
        db.execute("UPDATE daily_queue SET status='complete' WHERE id=?", (entry_id,))
        task = enqueue_task_in_transaction(
            db, "defensive_rubric_audit", "test", {"cursor": ""}, priority=145,
        )
        db.execute("UPDATE background_tasks SET state='leased',lease_token='audit-lease' WHERE id=?",
                   (task["id"],))
        generation = db.execute("SELECT generation FROM background_tasks WHERE id=?",
                                (task["id"],)).fetchone()[0]
    claimed = {"id": task["id"], "generation": generation, "lease_token": "audit-lease",
               "payload": {"cursor": ""}}
    assert execute_defense_rubric_audit_slice(claimed)
    with database.read_connection() as db:
        candidate = db.execute("SELECT card_id,exercise_revision,approved_at FROM threat_training_candidates WHERE id=?",
                               (candidate_id,)).fetchone()
        assert candidate["card_id"] == card_id and candidate["exercise_revision"] == 2
        assert candidate["approved_at"] is not None
        assert db.execute("SELECT COUNT(*) FROM defense_recognition_submissions WHERE candidate_id=?",
                          (candidate_id,)).fetchone()[0] == 1
        review = db.execute("SELECT invalidated_at FROM reviews WHERE source_ref='defense:old-rc4-review'").fetchone()
        assert review["invalidated_at"] is not None
        card = db.execute("SELECT interval_days,fsrs_card_json,revision FROM cards WHERE id=?", (card_id,)).fetchone()
        assert card["interval_days"] == 0 and card["fsrs_card_json"] is None and card["revision"] == 2
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND status='queued'",
                          (card_id,)).fetchone()[0] == 1
    with pytest.raises(ValueError, match="revision changed"):
        submit_defense_recognition(candidate_id, DefenseRecognitionRequest(
            attempt_id="stale-after-audit", exercise_revision=1, rubric_version=3,
            queue_entry_id=entry_id, no_concrete_threat=True, consequence="none",
        ))
    assert execute_defense_rubric_audit_slice(claimed)
    with database.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card_id,)).fetchone()[0] == 1


def test_defensive_preview_queue_blocks_unteachable_card_without_erasing_history(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        card_id = client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve").json()["card_id"]
        with database.connection() as db:
            materialize_daily_queue(db, date.today().isoformat())
            entry_id = db.execute("SELECT id FROM daily_queue WHERE card_id=? AND status='queued'",
                                  (card_id,)).fetchone()[0]
            db.execute("""UPDATE threat_training_candidates
                          SET validation_state='lesson_only',approved_at=NULL WHERE id=?""",
                       (candidate_id,))
            db.execute("UPDATE cards SET pending_validation=1 WHERE id=?", (card_id,))
            materialize_daily_queue(db, date.today().isoformat())
            assert db.execute("SELECT status FROM daily_queue WHERE id=?", (entry_id,)).fetchone()[0] == "blocked"
        queue = client.get("/api/queue/today").json()
        assert all(item.get("id") != card_id for item in queue.get("cards", []))
        assert client.get(f"/api/defense-exercises/{candidate_id}").status_code == 409


def test_defensive_rubric_audit_yields_to_foreground_and_replays_after_restart(tmp_path, monkeypatch, request):
    from app.services import threat_training
    from app.services.database_executor import database_writer
    from app.services.durable_tasks import enqueue_task_in_transaction

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    database_writer.start()
    request.addfinalizer(database_writer.stop)
    candidate_id = seed_reported_rc4_candidate()
    with database.connection() as db:
        task = enqueue_task_in_transaction(
            db, "defensive_rubric_audit", "restart-test", {"cursor": ""}, priority=145,
        )
        db.execute("UPDATE background_tasks SET state='leased',lease_token='first-lease' WHERE id=?",
                   (task["id"],))
        generation = db.execute("SELECT generation FROM background_tasks WHERE id=?",
                                (task["id"],)).fetchone()[0]
    claimed = {"id": task["id"], "generation": generation, "lease_token": "first-lease",
               "payload": {"cursor": ""}}
    began = threading.Event()
    release = threading.Event()
    original_validate = threat_training.validate_threat_anchor

    def paused_validation(*args):
        began.set()
        assert release.wait(5)
        return original_validate(*args)

    monkeypatch.setattr(threat_training, "validate_threat_anchor", paused_validation)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(threat_training.execute_defense_rubric_audit_slice, claimed)
        assert began.wait(2)
        started = time.perf_counter()
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM threat_training_candidates").fetchone()[0] == 1
        assert time.perf_counter() - started < 1
        release.set()
        assert future.result(timeout=5)
    with database.connection() as db:
        row = db.execute("SELECT generation,payload_json FROM background_tasks WHERE id=?",
                         (task["id"],)).fetchone()
        assert json.loads(row["payload_json"])["cursor"] == candidate_id
        db.execute("UPDATE background_tasks SET state='leased',lease_token='restarted-lease' WHERE id=?",
                   (task["id"],))
    resumed = {"id": task["id"], "generation": row["generation"],
               "lease_token": "restarted-lease", "payload": {"cursor": candidate_id}}
    assert threat_training.execute_defense_rubric_audit_slice(resumed) is False
    assert threat_training.execute_defense_rubric_audit_slice(claimed) is True
    with database.read_connection() as db:
        assert db.execute("SELECT exercise_revision FROM threat_training_candidates WHERE id=?",
                          (candidate_id,)).fetchone()[0] == 2


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
        assert exercise.json()["prompt"] == "What danger should your next move account for?"
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


def test_discovery_recognition_error_reinforces_even_with_sound_defense_once(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        card_id = client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve").json()["card_id"]
        with database.connection() as db:
            materialize_daily_queue(db, date.today().isoformat())
            entry_id = db.execute(
                "SELECT id FROM daily_queue WHERE card_id=? AND status='queued'", (card_id,),
            ).fetchone()[0]
        exercise = client.get(f"/api/defense-exercises/{candidate_id}").json()
        assert exercise["rubric_version"] == 3
        assert exercise["preview_fen"] != FEN
        assert exercise["proposed_move_san"] == "a3"
        assert "geometry" not in exercise
        recognition = client.post(f"/api/defense-exercises/{candidate_id}/recognition", json={
            "attempt_id": "recognition-one", "exercise_revision": 1, "rubric_version": 3,
            "queue_entry_id": entry_id, "no_concrete_threat": True,
            "consequence": "none",
        })
        assert recognition.status_code == 200
        assert recognition.json()["status"] == "ready_for_move"
        assert "correct" not in recognition.json()
        payload = {"attempt_id": "move-one", "exercise_revision": 1,
                   "queue_entry_id": entry_id, "move_uci": "e1f2",
                   "recognition_attempt_id": "recognition-one"}
        result = client.post(f"/api/defense-exercises/{candidate_id}/attempt", json=payload)
        assert result.status_code == 200, result.text
        assert result.json()["defense_status"] == "correct"
        assert result.json()["recognition_correct"] is False
        assert result.json()["status"] == "incorrect"
        assert client.post(f"/api/defense-exercises/{candidate_id}/attempt", json=payload).json()["idempotent"]
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card_id,)).fetchone()[0] == 1


def test_guided_recognition_reveals_route_after_assessment_and_sound_defense_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        card_id = client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve").json()["card_id"]
        with database.connection() as db:
            materialize_daily_queue(db, date.today().isoformat())
            entry_id = db.execute(
                "SELECT id FROM daily_queue WHERE card_id=? AND status='queued'", (card_id,),
            ).fetchone()[0]
        answer = client.post(f"/api/defense-exercises/{candidate_id}/recognition", json={
            "attempt_id": "recognition-correct", "exercise_revision": 1, "rubric_version": 3,
            "queue_entry_id": entry_id, "dangerous_piece_square": "b4",
            "destination_square": "c2", "king_square": "e1", "major_square": "a1",
            "consequence": "checking_fork",
        })
        assert answer.status_code == 200, answer.text
        assert answer.json()["recognition_correct"] is True
        assert answer.json()["feedback"]["fork_geometry"]["knight_to"] == "c2"
        repeated_assessment = client.post(f"/api/defense-exercises/{candidate_id}/recognition", json={
            "attempt_id": "recognition-correct", "exercise_revision": 1, "rubric_version": 3,
            "queue_entry_id": entry_id, "dangerous_piece_square": "b4",
            "destination_square": "c2", "king_square": "e1", "major_square": "a1",
            "consequence": "checking_fork",
        })
        assert repeated_assessment.json()["feedback"]["fork_geometry"]["knight_to"] == "c2"
        grade = client.post(f"/api/defense-exercises/{candidate_id}/attempt", json={
            "attempt_id": "move-correct", "exercise_revision": 1,
            "queue_entry_id": entry_id, "move_uci": "e1f2",
            "recognition_attempt_id": "recognition-correct",
        })
        assert grade.status_code == 200, grade.text
        assert grade.json()["status"] == "correct"
        assert grade.json()["recognition_correct"] is True


def test_discovery_paused_defense_can_train_now_beyond_automatic_daily_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        assert client.post(f"/api/defensive-threats/candidates/{candidate_id}/pause").status_code == 200
        with database.connection() as db:
            db.execute("UPDATE settings SET defense_new_cards_per_day=0 WHERE id=1")
        trained = client.post(f"/api/defensive-threats/candidates/{candidate_id}/train-now")
        assert trained.status_code == 200, trained.text
        card_id = trained.json()["card_id"]
        with database.connection() as db:
            row = db.execute(
                "SELECT admission_kind,admission_source FROM daily_queue WHERE card_id=? AND status='queued'",
                (card_id,),
            ).fetchone()
            assert row["admission_kind"] == "explicit"
            assert row["admission_source"] == f"defense:{candidate_id}"
            assert db.execute(
                "SELECT admission_mode,paused_at FROM threat_training_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()["admission_mode"] == "explicit"


def test_discovery_engine_activity_states_and_pause_preempts_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app):
        candidate_id = seed_candidate()
    with database.connection() as db:
        request_id = db.execute(
            "SELECT request_id FROM threat_candidate_requests WHERE candidate_id=? AND role='historical'",
            (candidate_id,),
        ).fetchone()[0]
        db.execute("UPDATE threat_training_candidates SET validation_state='needs_analysis' WHERE id=?",
                   (candidate_id,))
        db.execute("UPDATE threat_analysis_requests SET state='queued',report_json=NULL WHERE id=?",
                   (request_id,))
    assert any(item["id"] == request_id and item["state"] == "queued"
               for item in list_activity()["items"])
    assert set_control("threat_analysis", request_id, "pause")
    assert claim_analysis_request() is None
    assert set_control("threat_analysis", request_id, "resume")
    claimed = claim_analysis_request()
    assert claimed and claimed["id"] == request_id
    assert any(item["id"] == request_id and item["state"] == "running"
               for item in list_activity()["items"])


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
        assert client.post("/api/defensive-threats/analysis/claim").status_code == 403
        claim = client.post("/api/defensive-threats/analysis/claim",
                            headers={"X-Tempo-Engine-Worker": "docker"})
        assert claim.status_code == 200
        job = claim.json()["job"]
        assert job["id"] == request_row["id"]
        from app.services.threat_pipeline import _request_from_json
        request = _request_from_json(job["request"])
        report = asdict(AnalysisReport(request, (
            AnalysisLine("e1f2", ("e1f2",), EngineScore(cp=0), 14),
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


def test_discoveries_restricted_engine_report_rejects_wrong_root_and_short_depth(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        with database.connection() as db:
            request_id = db.execute(
                """SELECT request.id FROM threat_candidate_requests relation
                   JOIN threat_analysis_requests request ON request.id=relation.request_id
                   WHERE relation.candidate_id=? AND relation.role='historical'""",
                (candidate_id,),
            ).fetchone()[0]
            db.execute(
                "UPDATE threat_analysis_requests SET state='queued',report_json=NULL WHERE id=?",
                (request_id,),
            )
            db.execute(
                "UPDATE threat_training_candidates SET validation_state='needs_analysis' WHERE id=?",
                (candidate_id,),
            )
        job = client.post("/api/defensive-threats/analysis/claim",
                          headers={"X-Tempo-Engine-Worker": "docker"}).json()["job"]
        assert job["id"] == request_id
        root = job["request"]["root_move_uci"]
        assert root == "a2a3"
        endpoint = f"/api/defensive-threats/analysis/{request_id}/report"
        report = {"request": job["request"], "complete": True, "lines": [
            {"root_move_uci": "e1f2", "pv_uci": ["e1f2"],
             "score": {"cp": 0, "mate": None}, "depth": 14},
        ]}
        assert client.post(endpoint, json={"lease_id": job["lease_id"], "report": report}).status_code == 409
        report["lines"][0].update(root_move_uci=root, pv_uci=[root], depth=13)
        assert client.post(endpoint, json={"lease_id": job["lease_id"], "report": report}).status_code == 409
        report["lines"][0]["depth"] = 14
        assert client.post(endpoint, json={"lease_id": job["lease_id"], "report": report}).status_code == 200


def test_discoveries_report_repair_yields_to_foreground_and_replays_once(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        with database.connection() as db:
            request = db.execute(
                """SELECT request.id,request.report_json FROM threat_candidate_requests relation
                   JOIN threat_analysis_requests request ON request.id=relation.request_id
                   WHERE relation.candidate_id=? ORDER BY request.id LIMIT 1""",
                (candidate_id,),
            ).fetchone()
            malformed = json.loads(request["report_json"])
            malformed["lines"][0]["root_move_uci"] = "h7h8"
            db.execute(
                "UPDATE threat_analysis_requests SET report_json=? WHERE id=?",
                (json.dumps(malformed), request["id"]),
            )
        now = datetime.now(timezone.utc).isoformat()
        with database.connection() as db:
            db.execute(
                """INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,
                     state,phase,payload_json,next_attempt_at,lease_token,created_at,updated_at)
                   VALUES('audit-task','defensive_threat_report_audit','repair',1,135,
                          'leased','claimed','{"cursor":""}',?,'audit-lease',?,?)""",
                (now, now, now),
            )
        task = {"id": "audit-task", "generation": 1, "lease_token": "audit-lease",
                "payload": {"cursor": ""}}
        began = threading.Event()
        release = threading.Event()
        original_validate = threat_pipeline.validate_analysis_report

        def paused_validate(*args):
            began.set()
            assert release.wait(5)
            return original_validate(*args)

        monkeypatch.setattr(threat_pipeline, "validate_analysis_report", paused_validate)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(threat_pipeline.execute_threat_report_audit, task)
            assert began.wait(2)
            started = time.perf_counter()
            assert client.get("/api/health").status_code == 200
            assert time.perf_counter() - started < 1
            release.set()
            assert future.result(timeout=5)
        assert threat_pipeline.execute_threat_report_audit(task)
    database.initialize()
    with database.read_connection() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM threat_analysis_report_history WHERE request_id=?",
            (request["id"],),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT state FROM threat_analysis_requests WHERE id=?", (request["id"],)
        ).fetchone()[0] == "queued"
        assert db.execute(
            "SELECT validation_state FROM threat_training_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0] == "needs_analysis"


def test_discoveries_verified_defense_auto_admits_once_with_daily_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    from app.services.database_executor import database_writer
    database_writer.start()
    try:
        candidate_id = seed_candidate()
        from app.services.durable_tasks import claim_task
        from app.services.threat_training import (
            enqueue_defense_admission, execute_defense_admission_slice,
        )
        with database.connection() as db:
            db.execute("UPDATE settings SET defense_new_cards_per_day=1 WHERE id=1")
        enqueue_defense_admission(background=False)
        task = claim_task("defensive_admission")
        assert task is not None
        assert execute_defense_admission_slice(task)
        assert not execute_defense_admission_slice(task)
        with database.connection() as db:
            materialize_daily_queue(db, date.today().isoformat())
            candidate = db.execute(
                "SELECT card_id,approved_at FROM threat_training_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            assert candidate["approved_at"] and candidate["card_id"]
            assert db.execute(
                "SELECT COUNT(*) FROM cards WHERE content_type='defense' AND introduced_at=?",
                (date.today().isoformat(),),
            ).fetchone()[0] == 1
            assert db.execute(
                "SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND status='queued'",
                (candidate["card_id"],),
            ).fetchone()[0] == 1
    finally:
        database_writer.stop()


def test_defensive_auto_admission_holds_legacy_fork_without_immediate_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    from app.services.database_executor import database_writer
    from app.services.durable_tasks import claim_task
    from app.services.threat_training import enqueue_defense_admission, execute_defense_admission_slice
    database_writer.start()
    try:
        candidate_id = seed_candidate()
        with database.connection() as db:
            db.execute("UPDATE settings SET defense_new_cards_per_day=1 WHERE id=1")
            report_row = db.execute(
                """SELECT request.id,request.report_json FROM threat_candidate_requests relation
                   JOIN threat_analysis_requests request ON request.id=relation.request_id
                   WHERE relation.candidate_id=? AND relation.role='historical'""",
                (candidate_id,),
            ).fetchone()
            delayed_report = json.loads(report_row["report_json"])
            delayed_report["lines"][0]["pv_uci"] = ["a2a3", "e8e7"]
            db.execute(
                "UPDATE threat_analysis_requests SET report_json=? WHERE id=?",
                (json.dumps(delayed_report), report_row["id"]),
            )
        enqueue_defense_admission(background=False)
        task = claim_task("defensive_admission")
        assert task and execute_defense_admission_slice(task)
        with database.read_connection() as db:
            candidate = db.execute(
                "SELECT card_id,approved_at FROM threat_training_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            assert candidate["card_id"] is None and candidate["approved_at"] is None
    finally:
        database_writer.stop()


def test_discoveries_backfill_restarts_after_one_game_without_duplicate_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    from app.services.database_executor import database_writer
    from app.services.durable_tasks import claim_task
    database_writer.start()
    try:
        seed_candidate()
        task = threat_pipeline.enqueue_threat_backfill()
        claimed = claim_task("defensive_threat_backfill")
        assert claimed and claimed["id"] == task["id"]
        reached_publication = threading.Event()
        release_publication = threading.Event()
        original_wait = threat_pipeline.activity_gate.wait_for_foreground
        wait_calls = 0

        def pause_before_publication():
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 2:
                reached_publication.set()
                assert release_publication.wait(5)
            return original_wait()

        monkeypatch.setattr(threat_pipeline.activity_gate, "wait_for_foreground", pause_before_publication)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(threat_pipeline.execute_threat_backfill_slice, claimed)
            assert reached_publication.wait(2)
            started = time.perf_counter()
            with database.connection() as db:
                assert db.execute("SELECT COUNT(*) FROM imported_games").fetchone()[0] == 1
            assert time.perf_counter() - started < 1
            release_publication.set()
            assert future.result(timeout=5)
        assert threat_pipeline.execute_threat_backfill_slice(claimed)
        with database.read_connection() as db:
            queued_scan = db.execute(
                "SELECT payload_json FROM background_tasks WHERE kind='defensive_threat_scan'"
            ).fetchall()
            assert len(queued_scan) == 1
            assert json.loads(queued_scan[0][0])["game_id"] == "lichess:defense-one"
        database.initialize()
        resumed = claim_task("defensive_threat_backfill")
        assert resumed and resumed["payload"] == {"phase": "games", "cursor": "lichess:defense-one"}
        assert threat_pipeline.execute_threat_backfill_slice(resumed)
        with database.read_connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM background_tasks WHERE kind='defensive_threat_scan'"
            ).fetchone()[0] == 1
    finally:
        database_writer.stop()


def test_discoveries_validated_false_alarm_finishes_after_recognition_with_one_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_validated_control()
        with database.connection() as db:
            assert db.execute(
                "SELECT validation_state FROM threat_training_candidates WHERE id=?", (candidate_id,),
            ).fetchone()[0] == "validated_control"
        approved = client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve")
        assert approved.status_code == 200, approved.text
        with database.connection() as db:
            materialize_daily_queue(db, date.today().isoformat())
            queue_entry_id = db.execute(
                """SELECT queue.id FROM daily_queue queue
                   JOIN threat_training_candidates candidate ON candidate.card_id=queue.card_id
                   WHERE candidate.id=? AND queue.status='queued'""",
                (candidate_id,),
            ).fetchone()[0]
        exercise = client.get(f"/api/defense-exercises/{candidate_id}").json()
        assert "refutation" not in json.dumps(exercise)
        answer = {"attempt_id": "control-answer", "exercise_revision": exercise["exercise_revision"],
                  "rubric_version": 3,
                  "queue_entry_id": queue_entry_id, "no_concrete_threat": True,
                  "consequence": "none"}
        first = client.post(f"/api/defense-exercises/{candidate_id}/recognition", json=answer)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "correct"
        assert first.json()["feedback"]["refutation_uci"][-1] == "e4c2"
        repeated = client.post(f"/api/defense-exercises/{candidate_id}/recognition", json=answer)
        assert repeated.status_code == 200 and repeated.json()["idempotent"]
        with database.read_connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
            assert db.execute("SELECT status FROM daily_queue WHERE id=?", (queue_entry_id,)).fetchone()[0] == "complete"


def test_discoveries_auto_admission_includes_one_validated_control_under_daily_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    from app.services.database_executor import database_writer
    from app.services.durable_tasks import claim_task
    from app.services.threat_training import enqueue_defense_admission, execute_defense_admission_slice
    database_writer.start()
    try:
        seed_candidate()
        seed_validated_control()
        with database.connection() as db:
            db.execute("UPDATE settings SET defense_new_cards_per_day=2 WHERE id=1")
        enqueue_defense_admission(background=False)
        for _ in range(3):
            task = claim_task("defensive_admission")
            if task is None or not execute_defense_admission_slice(task):
                break
        with database.read_connection() as db:
            admissions = db.execute(
                """SELECT validation_state FROM threat_training_candidates
                   WHERE admission_mode='automatic' ORDER BY validation_state""",
            ).fetchall()
            assert [row[0] for row in admissions] == ["engine_supported", "validated_control"]
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        from app.main import materialize_daily_queue
        with database.connection() as db:
            materialize_daily_queue(db, date.today().isoformat())
        with TestClient(app) as client:
            response = client.get("/api/queue/today")
            assert response.status_code == 200
            defense_cards = [card for card in response.json()["cards"]
                             if card["content_type"] == "defense"]
            assert len(defense_cards) == 2
            assert all(card["admission_source"].startswith("defense:")
                       for card in defense_cards)
    finally:
        database_writer.stop()


def test_discoveries_reanalysis_keeps_defense_card_and_genuine_review_history(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        candidate_id = seed_candidate()
        approved = client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve")
        assert approved.status_code == 200
        card_id = approved.json()["card_id"]
        with database.connection() as db:
            materialize_daily_queue(db, date.today().isoformat())
            entry_id = db.execute(
                "SELECT id FROM daily_queue WHERE card_id=? AND status='queued'", (card_id,),
            ).fetchone()[0]
        attempted = client.post(f"/api/defense-exercises/{candidate_id}/attempt", json={
            "attempt_id": "original-review", "exercise_revision": 1,
            "queue_entry_id": entry_id, "move_uci": "e1f2",
        })
        assert attempted.status_code == 200 and attempted.json()["status"] == "correct"
        game = GameSnapshot("lichess:defense-one", 2, FEN, ("a2a3",), "white")
        seed = find_defensive_knight_forks(
            game, SourceLine("engine", 1, ("b4c2", "e1d2", "c2a1", "d2c1")),
        )[0]
        with database.connection() as db:
            db.execute("UPDATE imported_games SET analysis_version=2 WHERE id=?", (game.game_id,))
            _upsert_seed(db, game, seed)
            current = db.execute(
                "SELECT id,card_id,analysis_version,validation_state FROM threat_training_candidates WHERE game_id=?",
                (game.game_id,),
            ).fetchall()
            assert len(current) == 1
            assert current[0]["id"] == candidate_id
            assert current[0]["card_id"] == card_id
            assert current[0]["analysis_version"] == 2
            assert current[0]["validation_state"] == "needs_analysis"
            assert db.execute("SELECT pending_validation FROM cards WHERE id=?", (card_id,)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card_id,)).fetchone()[0] == 1
        execute_threat_validation({"payload": {"candidate_id": candidate_id}})
        reapproved = client.post(f"/api/defensive-threats/candidates/{candidate_id}/approve")
        assert reapproved.status_code == 200 and reapproved.json()["card_id"] == card_id
        with database.read_connection() as db:
            assert db.execute("SELECT pending_validation FROM cards WHERE id=?", (card_id,)).fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card_id,)).fetchone()[0] == 1


def test_discoveries_auto_admission_prioritizes_recurring_position_before_recent_isolated_one(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    from app.services.database_executor import database_writer
    from app.services.durable_tasks import claim_task
    from app.services.threat_training import enqueue_defense_admission, execute_defense_admission_slice
    database_writer.start()
    try:
        isolated_id = seed_candidate()
        recurring_fen = "4k3/8/8/8/1n6/8/P6P/R3K3 w Q - 0 1"
        recurring_id = seed_candidate(
            game_id="lichess:defense-recurring", fen=recurring_fen,
            played_at=(datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
        )
        with database.connection() as db:
            db.execute("UPDATE settings SET defense_new_cards_per_day=1 WHERE id=1")
            for game_id, position_fen in (("lichess:defense-one", FEN),
                                          ("lichess:defense-recurring", recurring_fen)):
                db.execute(
                    "INSERT INTO game_position_occurrences(game_id,ply,fen_key) VALUES(?,0,?)",
                    (game_id, " ".join(position_fen.split()[:4])),
                )
            for index in range(3):
                game_id = f"lichess:recurring-extra-{index}"
                db.execute(
                    """INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
                         result,start_fen,moves_json,analysis_state,analysis_version)
                       VALUES(?,'lichess','learner',?,'rapid',1,'white','1-0',?,'[]','ready',1)""",
                    (game_id, datetime.now(timezone.utc).isoformat(), recurring_fen),
                )
                db.execute(
                    "INSERT INTO game_position_occurrences(game_id,ply,fen_key) VALUES(?,0,?)",
                    (game_id, " ".join(recurring_fen.split()[:4])),
                )
        enqueue_defense_admission(background=False)
        task = claim_task("defensive_admission")
        assert task and execute_defense_admission_slice(task)
        with database.read_connection() as db:
            assert db.execute(
                "SELECT admission_mode FROM threat_training_candidates WHERE id=?", (recurring_id,),
            ).fetchone()[0] == "automatic"
            assert db.execute(
                "SELECT admission_mode FROM threat_training_candidates WHERE id=?", (isolated_id,),
            ).fetchone()[0] is None
    finally:
        database_writer.stop()
