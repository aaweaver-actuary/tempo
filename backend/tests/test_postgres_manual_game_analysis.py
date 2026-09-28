"""Manual analysis saves use the versioned, restartable publication path."""

from contextlib import contextmanager
import json

from fastapi import HTTPException
import pytest

from app import command_dispatch, game_analysis_publication, main, postgres_store
from app.models import GameAnalysisRequest


START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Database:
    def __init__(self, *, job=None, game=None, published=None):
        self.job = job
        self.game = game
        self.published = published
        self.statements = []

    def execute_native(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        if statement.startswith("SELECT status,lease_id,analysis_version"):
            return Cursor(self.job)
        if statement.startswith("SELECT color,start_fen,moves_json,analysis_version"):
            return Cursor(self.game)
        if statement.startswith("SELECT result_json"):
            return Cursor(self.published)
        return Cursor()


def payload(lease_id=None, request_key=None):
    return {"game_id": "game-one", "prepared": {
        "game": {"color": "white", "start_fen": START_FEN,
                 "moves_json": json.dumps(["e2e4"])},
        "request": {"lease_id": lease_id, "idempotency_key": request_key,
                    "analysis_version": 1, "analysis_evidence_version": 3,
                    "depth": 14, "engine_version": "Stockfish 19 WASM",
                    "network_version": "test-network"},
        "evaluations": [],
    }, "result": {"major_mistake_ply": None, "missed_punishment_ply": None}}


def game(version=1):
    return {"color": "white", "start_fen": START_FEN,
            "moves_json": json.dumps(["e2e4"]), "analysis_version": version}


def test_postgres_manual_analysis_admits_one_higher_generation_and_sliced_publication(monkeypatch):
    queued = []
    monkeypatch.setattr(game_analysis_publication, "_queue_game_analysis_publication",
                        lambda _database, admitted, _now:
                        queued.append(admitted) or {"status": "preparing", "task_id": "task-one"})
    database = Database(job={"status": "leased", "lease_id": "lease-one",
                             "analysis_version": 2, "idempotency_key": None},
                        game=game(version=2))
    assert game_analysis_publication.admit_manual_game_analysis(
        database, payload("lease-one")) == {"status": "preparing", "task_id": "task-one"}
    assert queued[0]["analysis_version"] == 3
    assert queued[0]["analysis_evidence_version"] == 3
    assert "FOR UPDATE" in database.statements[0][0]
    assert "status='publishing'" in database.statements[2][0]


def test_postgres_manual_analysis_rejects_stale_lease_without_publishing(monkeypatch):
    monkeypatch.setattr(game_analysis_publication, "_queue_game_analysis_publication",
                        lambda *_args: pytest.fail("stale lease published"))
    database = Database(job={"status": "leased", "lease_id": "new-lease",
                             "analysis_version": 2, "idempotency_key": None}, game=game())
    with pytest.raises(HTTPException) as error:
        game_analysis_publication.admit_manual_game_analysis(database, payload("old-lease"))
    assert error.value.status_code == 409
    assert len(database.statements) == 2


def test_postgres_manual_analysis_replays_completed_request_key():
    database = Database(job={"status": "complete", "lease_id": None,
                             "analysis_version": 2, "idempotency_key": "save-one"},
                        game=game(version=2),
                        published={"result_json": json.dumps({"major_mistake_ply": 0})})
    assert game_analysis_publication.admit_manual_game_analysis(
        database, payload(request_key="save-one")) == {
        "major_mistake_ply": 0, "idempotent": True,
    }
    assert len(database.statements) == 3


def test_postgres_manual_analysis_route_retains_operation_id_and_returns_preparing(monkeypatch):
    calls = []

    class Reader:
        def execute(self, statement, _parameters=()):
            return Cursor({"color": "white", "start_fen": START_FEN,
                           "moves_json": json.dumps(["e2e4"])}
                          if "FROM imported_games" in statement else (100,))

    @contextmanager
    def reader():
        yield Reader()

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main, "read_connection", reader)
    monkeypatch.setattr(main, "classify_swings",
                        lambda *_args: {"major_mistake_ply": None,
                                        "missed_punishment_ply": None})
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, admitted, *, idempotency_key, background=False:
                        calls.append((name, admitted, idempotency_key, background))
                        or {"status": "preparing", "task_id": "task-one"})
    response = main.save_game_analysis("game-one", GameAnalysisRequest(evaluations=[]), "save-one")
    assert response.status_code == 202
    assert json.loads(response.body) == {"status": "preparing", "task_id": "task-one"}
    assert calls[0][0] == "games.analysis.manual.admit"
    assert calls[0][2:] == ("save-one", False)
