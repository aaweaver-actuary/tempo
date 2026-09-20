from fastapi.testclient import TestClient
import hashlib
import json

from app import database
from app.main import app


PGN = b'''[Event "Persistent repertoire"]
[Opening "Italian Game"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d3 d6 6. O-O O-O 7. Re1 *
'''

THREE_LINES = b'''[Event "King pawn"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 *

[Event "Queen pawn"]
[Result "*"]

1. e4 c5 2. Nf3 d6 3. d4 *

[Event "English"]
[Result "*"]

1. e4 e6 2. d4 d5 3. Nc3 *
'''

THREE_LINES_BLACK = b'''[Event "King pawn"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 *

[Event "Queen pawn"]
[Result "*"]

1. e4 e5 2. c4 Nc6 *

[Event "English"]
[Result "*"]

1. e4 e5 2. d4 Nc6 *
'''

BLACK_PGN = b'''[Event "Persistent repertoire"]
[Opening "Italian Game"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d3 d6 6. O-O O-O 7. Re1 Be6 *
'''

INCOMPLETE_BLACK_LINE = b'''[Event "Incomplete Black"]
[Result "*"]

1. e4 *
'''


def test_incomplete_black_prefix_is_not_created_or_queued(tmp_path, monkeypatch):
    """Regression: an auto-played White move must not lock a Black card."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        imported = client.post(
            "/api/imports/pgn",
            files={"file": ("incomplete.pgn", INCOMPLETE_BLACK_LINE, "application/x-chess-pgn")},
            data={"trained_color": "black", "initial_depth": "6"},
        )
        assert imported.status_code == 200
        assert imported.json()["cards_created"] == 0
        assert client.get("/api/queue/today").json()["count"] == 0


def test_legacy_incomplete_black_prefix_is_quarantined_from_queue(tmp_path, monkeypatch):
    """Regression: malformed persisted cards are skipped instead of freezing Train."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        client.post(
            "/api/imports/pgn",
            files={"file": ("complete.pgn", BLACK_PGN, "application/x-chess-pgn")},
            data={"trained_color": "black", "initial_depth": "2"},
        )
        queued = client.get("/api/queue/today").json()["cards"][0]
        with database.connection() as db:
            db.execute(
                "UPDATE cards SET moves_json=? WHERE id=?",
                (json.dumps(["e2e4"]), queued["id"]),
            )
        response = client.get("/api/queue/today").json()
        assert response["count"] == 0
        assert response["diagnostics"][0]["card_id"] == queued["id"]
        with database.connection() as db:
            assert db.execute("SELECT state FROM cards WHERE id=?", (queued["id"],)).fetchone()[0] == "locked"
            assert db.execute("SELECT status FROM daily_queue WHERE id=?", (queued["queue_entry_id"],)).fetchone()[0] == "skipped"


def test_import_becomes_main_and_survives_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        first = client.post("/api/imports/pgn", files={"file": ("italian.pgn", PGN, "application/x-chess-pgn")}, data={"trained_color": "white", "initial_depth": "6"})
        assert first.status_code == 200
        repertoire_id = first.json()["repertoire_id"]
        queue = client.get("/api/queue/today").json()
        assert queue["count"] == 1
        assert queue["cards"][0]["is_main"] == 1
        assert queue["cards"][0]["trained_color"] == "white"
        assert queue["cards"][0]["moves"][-1] == "e1g1"

        card = queue["cards"][0]
        first_review = client.post(f"/api/cards/{card['id']}/review", json={"outcome": "correct", "queue_entry_id": card["queue_entry_id"]})
        assert first_review.json()["requeue_today"] is True
        reinforcement = client.get("/api/queue/today").json()
        assert reinforcement["count"] == 1
        assert reinforcement["cards"][0]["cycle"] == 1
        second = reinforcement["cards"][0]
        second_review = client.post(f"/api/cards/{second['id']}/review", json={"outcome": "correct", "queue_entry_id": second["queue_entry_id"]})
        assert second_review.json()["requeue_today"] is False
        assert client.get("/api/queue/today").json()["count"] == 0

        repeated = client.post("/api/imports/pgn", files={"file": ("italian.pgn", PGN, "application/x-chess-pgn")}, data={"trained_color": "white", "initial_depth": "6"})
        assert repeated.json()["repertoire_id"] == repertoire_id
        assert repeated.json()["cards_created"] == 0
        assert len(client.get("/api/repertoires").json()["repertoires"]) == 1


def test_tactic_and_endgame_are_admitted_to_scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        tactic = client.post("/api/tactics/attempt", json={
            "puzzle_id": "test-puzzle", "deck_id": "fork-easy", "correct": False, "clean": False,
            "source_fen": "8/8/8/8/8/4k3/7p/6K1 b - - 0 1", "moves": ["h2h1q", "g1h1"], "rating": 900,
        })
        assert tactic.status_code == 200
        assert tactic.json()["mode"] == "normal"

        endgame = client.post("/api/endgames/templates", json={
            "name": "Rook + king", "white_material": "KR", "black_material": "K", "trained_color": "white", "goal_mix": "both",
        })
        assert endgame.status_code == 200
        repeated = client.post("/api/endgames/templates", json={
            "name": "Rook + king", "white_material": "KR", "black_material": "K", "trained_color": "white", "goal_mix": "both",
        })
        assert repeated.status_code == 200
        assert repeated.json()["card_id"] == endgame.json()["card_id"]
        assert repeated.json()["already_exists"] is True

        queue = client.get("/api/queue/today").json()["cards"]
        assert {card["content_type"] for card in queue} == {"tactic", "endgame"}


def test_new_card_limit_due_counts_and_repertoire_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        settings = client.get("/api/settings").json()
        settings["new_cards_per_day"] = 2
        assert client.put("/api/settings", json=settings).status_code == 200

        imported = client.post("/api/imports/pgn", files={"file": ("three.pgn", THREE_LINES_BLACK, "application/x-chess-pgn")}, data={"trained_color": "black", "initial_depth": "2"})
        assert imported.status_code == 200
        assert imported.json()["cards_created"] == 3
        repertoire_id = imported.json()["repertoire_id"]

        queue = client.get("/api/queue/today").json()["cards"]
        assert len(queue) == 2
        assert all(card["trained_color"] == "black" for card in queue)
        assert all(len(card["moves"]) == 4 for card in queue)
        repertoire = client.get("/api/repertoires").json()["repertoires"][0]
        assert repertoire["card_count"] == 3
        assert repertoire["due_count"] == 2

        assert client.delete(f"/api/repertoires/{repertoire_id}").json()["deleted"] is True
        assert client.get("/api/repertoires").json()["repertoires"] == []
        assert client.get("/api/queue/today").json()["count"] == 0


def test_legacy_eager_queue_is_reconciled_without_reviews(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        settings = client.get("/api/settings").json()
        settings["new_cards_per_day"] = 1
        client.put("/api/settings", json=settings)
        client.post("/api/imports/pgn", files={"file": ("legacy.pgn", THREE_LINES, "application/x-chess-pgn")}, data={"trained_color": "white", "initial_depth": "2"})
        with database.connection() as db:
            day = db.execute("SELECT queue_date FROM daily_queue LIMIT 1").fetchone()[0]
            maximum = db.execute("SELECT MAX(position) FROM daily_queue WHERE queue_date=?", (day,)).fetchone()[0]
            unseen = db.execute("SELECT id FROM cards WHERE state='new' ORDER BY id").fetchall()
            for offset, card in enumerate(unseen, 1):
                db.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)", (day, card[0], maximum + offset))
        queue = client.get("/api/queue/today").json()
        assert queue["count"] == 1
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_overlapping_repertoires_share_card_history_when_one_is_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        first = client.post("/api/imports/pgn", files={"file": ("first.pgn", PGN, "application/x-chess-pgn")}, data={"trained_color": "white", "initial_depth": "2"}).json()
        card_id_value = client.get("/api/queue/today").json()["cards"][0]["id"]
        client.post(f"/api/cards/{card_id_value}/review", json={"outcome": "correct", "guided": False})

        second = client.post("/api/imports/pgn", files={"file": ("second.pgn", PGN, "application/x-chess-pgn")}, data={"trained_color": "white", "initial_depth": "2"}).json()
        assert second["cards_created"] == 0
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM repertoire_cards WHERE card_id=?", (card_id_value,)).fetchone()[0] == 2

        client.delete(f"/api/repertoires/{first['repertoire_id']}")
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM cards WHERE id=?", (card_id_value,)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card_id_value,)).fetchone()[0] == 1
            assert db.execute("SELECT repertoire_id FROM cards WHERE id=?", (card_id_value,)).fetchone()[0] == second["repertoire_id"]


def test_position_annotations_are_scoped_and_round_trip_through_pgn(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        imported = client.post("/api/imports/pgn", files={"file": ("notes.pgn", PGN, "application/x-chess-pgn")}, data={"trained_color": "white"}).json()
        repertoire_id = imported["repertoire_id"]
        fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        saved = client.put(f"/api/repertoires/{repertoire_id}/annotations", json={
            "fen": fen,
            "comment": "Watch the loose diagonal.",
            "arrows": [{"from": "c1", "to": "g5", "color": "yellow"}],
            "squares": [{"square": "d4", "color": "red"}],
        })
        assert saved.status_code == 200
        listed = client.get(f"/api/repertoires/{repertoire_id}/annotations", params={"fen": fen}).json()["annotations"]
        assert listed[0]["comment"] == "Watch the loose diagonal."
        exported = client.get(f"/api/repertoires/{repertoire_id}/export.pgn").text
        assert "Watch the loose diagonal." in exported
        assert "%cal Yc1g5" in exported
        assert "%csl Rd4" in exported


def test_teaching_state_and_verified_migration_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        imported = client.post(
            "/api/imports/pgn",
            files={"file": ("teaching.pgn", PGN, "application/x-chess-pgn")},
            data={"trained_color": "white", "initial_depth": "2"},
        )
        assert imported.status_code == 200
        card_id = client.get("/api/queue/today").json()["cards"][0]["id"]
        taught = client.post(f"/api/cards/{card_id}/teaching", json={"revision": 1, "ply": 0})
        assert taught.status_code == 200
        assert client.post(f"/api/cards/{card_id}/teaching", json={"revision": 1, "ply": 0}).status_code == 200
        states = client.get(f"/api/cards/{card_id}/teaching").json()["states"]
        assert len(states) == 1

        snapshot = client.get("/api/migration/snapshot").json()
        assert snapshot["source"] == "tempo-sqlite"
        assert snapshot["counts"]["teaching_states"] == 1
        assert all(snapshot["counts"][name] == len(rows) for name, rows in snapshot["tables"].items())
        canonical = json.dumps(
            {"schemaVersion": snapshot["schemaVersion"], "tables": snapshot["tables"]},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        assert hashlib.sha256(canonical.encode()).hexdigest() == snapshot["checksum"]
