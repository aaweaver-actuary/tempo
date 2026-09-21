import json

from fastapi.testclient import TestClient

from app import database
from app.main import app
from helpers import wait_for_integrity


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _line(db, identifier: str, repertoire: str, color: str, moves: list[str]):
    db.execute(
        "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
        (repertoire, repertoire, "test", "2026-09-19T00:00:00+00:00"),
    )
    db.execute(
        "INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",
        (identifier, repertoire, identifier, color, START, json.dumps(moves), "2026-09-19T00:00:00+00:00"),
    )


def test_same_repertoire_trained_player_move_conflict_is_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "white-rep", "white", ["e2e4", "e7e5"])
            _line(db, "two", "white-rep", "white", ["d2d4", "d7d5"])
        conflicts = client.get("/api/repertoire/conflicts").json()["conflicts"]
        assert len(conflicts) == 1
        assert [move["uci"] for move in conflicts[0]["moves"]] == ["d2d4", "e2e4"]


def test_opponent_branches_and_cross_repertoire_moves_are_not_conflicts(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "white-rep", "white", ["e2e4", "e7e5", "g1f3"])
            _line(db, "two", "white-rep", "white", ["e2e4", "c7c5", "g1f3"])
            _line(db, "three", "other-rep", "white", ["d2d4", "d7d5"])
        assert client.get("/api/repertoire/conflicts").json()["conflicts"] == []


def test_new_conflicting_branch_enters_integrity_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "white-rep", "white", ["e2e4", "e7e5"])
        payload = {
            "repertoire_id": "white-rep",
            "starting_fen": START,
            "moves": ["d2d4", "d7d5"],
            "trained_color": "white",
        }
        response = client.post("/api/repertoire/branches", json=payload)
        assert response.status_code == 200
        wait_for_integrity(client, "white-rep")
        assert client.get("/api/repertoires/white-rep/integrity").json()["status"] == "needs_repair"
