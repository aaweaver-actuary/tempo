import json

from fastapi.testclient import TestClient

from app import database
from app.main import app


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _line(db, identifier: str, repertoire: str, moves: list[str]):
    db.execute(
        "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
        (repertoire, repertoire, "integrity.pgn", "2026-09-20T00:00:00+00:00"),
    )
    db.execute(
        "INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",
        (identifier, repertoire, identifier, "white", START, json.dumps(moves), "2026-09-20T00:00:00+00:00"),
    )


def test_repertoire_integrity_sweep_pauses_conflicting_transpositions_after_import(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5", "g1f3", "b8c6", "d2d4"])
            _line(db, "two", "rep", ["d2d4", "d7d5", "c2c4", "c7c6", "g1f3"])
        integrity = client.get("/api/repertoires/rep/integrity").json()
        assert integrity["status"] == "needs_repair"
        assert any(issue["kind"] == "multiple_responses" for issue in integrity["issues"])
        assert client.get("/api/queue/today").json()["cards"] == []


def test_repertoire_integrity_requires_one_response_at_player_turn_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _line(db, "one", "rep", ["e2e4", "e7e5"])
    with TestClient(app) as client:
        issues = client.get("/api/repertoires/rep/integrity").json()["issues"]
        assert [issue["kind"] for issue in issues] == ["missing_response"]


def test_integrity_resolution_rewrites_routes_and_truncates_losing_continuations(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5", "g1f3", "b8c6", "d2d4"])
            _line(db, "two", "rep", ["d2d4", "d7d5", "c2c4", "c7c6", "g1f3"])
        issue = next(
            item
            for item in client.get("/api/repertoires/rep/integrity").json()["issues"]
            if item["kind"] == "multiple_responses"
        )
        result = client.post(
            f"/api/repertoires/rep/integrity/issues/{issue['id']}/resolve",
            json={"signature": issue["signature"], "selected_move_uci": "d2d4"},
        )
        assert result.status_code == 200
        assert result.json()["summary"]["status"] == "clean"
        lines = client.get("/api/repertoire/lines").json()["lines"]
        assert any(line["moves"] == ["d2d4"] for line in lines)


def test_stale_or_illegal_integrity_resolution_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5"])
            _line(db, "two", "rep", ["d2d4", "d7d5"])
        issue = next(
            item
            for item in client.get("/api/repertoires/rep/integrity").json()["issues"]
            if item["kind"] == "multiple_responses"
        )
        response = client.post(
            f"/api/repertoires/rep/integrity/issues/{issue['id']}/resolve",
            json={"signature": "stale", "selected_move_uci": "d2d4"},
        )
        assert response.status_code == 409
        assert client.get("/api/repertoires/rep/integrity").json()["status"] == "needs_repair"


def test_legacy_integrity_issues_are_swept_at_startup_and_excluded_from_training(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _line(db, "legacy", "rep", ["e2e4", "e7e5"])
    with TestClient(app) as client:
        summary = client.get("/api/repertoires/rep/integrity").json()
        assert summary["status"] == "needs_repair"
        assert client.get("/api/queue/today").json()["count"] == 0


def test_shared_cards_remain_trainable_only_through_clean_repertoires(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "clean-line", "clean-rep", ["e2e4"])
            _line(db, "paused-line", "paused-rep", ["e2e4", "e7e5"])
            db.execute(
                "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES(?,?,?,?,?,?,?)",
                ("shared", "clean-rep", "prefix", START, '["e2e4"]', "2026-09-20", "opening"),
            )
            db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", ("clean-rep", "shared"))
            db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", ("paused-rep", "shared"))
        client.get("/api/queue/today")
        with database.connection() as db:
            db.execute("UPDATE cards SET due_date=? WHERE id=?", ("2026-09-20", "shared"))
        queued = client.get("/api/queue/today").json()["cards"]
        assert any(card["id"] == "shared" for card in queued)


def test_invalid_sources_materialize_as_distinct_blocking_issues(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES(?,?,?,?,?)", ("rep", "rep", "bad.pgn", "2026-09-20", 0))
        for identifier in ("bad-one", "bad-two"):
            db.execute(
                "INSERT INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",
                (identifier, "rep", identifier, "white", START, '["not-a-uci"]', "2026-09-20"),
            )
    with TestClient(app) as client:
        issues = client.get("/api/repertoires/rep/integrity").json()["issues"]
        assert [issue["kind"] for issue in issues] == ["invalid_source", "invalid_source"]


def test_missing_endpoint_resolution_accepts_an_unsaved_legal_move(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _line(db, "one", "rep", ["e2e4", "e7e5"])
    with TestClient(app) as client:
        issue = client.get("/api/repertoires/rep/integrity").json()["issues"][0]
        response = client.post(
            f"/api/repertoires/rep/integrity/issues/{issue['id']}/resolve",
            json={"signature": issue["signature"], "selected_move_uci": "g1f3"},
        )
        assert response.status_code == 200
        assert response.json()["summary"]["status"] == "clean"


def test_integrity_repair_archives_losing_card_history_without_transferring_mastery(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _line(db, "one", "rep", ["e2e4", "e7e5", "g1f3"])
            _line(db, "two", "rep", ["d2d4", "d7d5", "c2c4"])
            db.execute(
                "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES(?,?,?,?,?,?,?)",
                ("losing-card", "rep", "prefix", START, '["e2e4", "e7e5", "g1f3"]', "2026-09-20", "opening"),
            )
            db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep','losing-card')")
            db.execute(
                "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('losing-card','correct','2026-09-20',1,7)"
            )
        issue = next(item for item in client.get("/api/repertoires/rep/integrity").json()["issues"] if item["kind"] == "multiple_responses")
        response = client.post(
            f"/api/repertoires/rep/integrity/issues/{issue['id']}/resolve",
            json={"signature": issue["signature"], "selected_move_uci": "d2d4"},
        )
        assert response.status_code == 200
        with database.connection() as db:
            card = db.execute("SELECT archived,state FROM cards WHERE id='losing-card'").fetchone()
            assert card["archived"] == 1
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='losing-card'").fetchone()[0] == 1
