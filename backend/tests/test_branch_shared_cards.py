from fastapi.testclient import TestClient
from app import database
from app.main import app


def test_branch_removal_preserves_shared_cards_and_other_repertoire_history(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        identifiers = []
        for name in ("first.pgn", "second.pgn"):
            response = client.post("/api/imports/pgn", files={"file": (name, b'[Event "Shared"]\n\n1. d4 Nf6 2. c4 e6 3. Nf3 *')}, data={"trained_color": "white", "initial_depth": 2})
            identifiers.append(response.json()["repertoire_id"])
        card = client.get("/api/queue/today").json()["cards"][0]
        assert client.post(f'/api/cards/{card["id"]}/review', json={"outcome": "correct", "queue_entry_id": card["queue_entry_id"]}).status_code == 200
        removed = client.post("/api/repertoire/branches/remove", json={"repertoire_id": identifiers[0], "starting_fen": card["start_fen"], "moves": ["d2d4", "g8f6"]})
        assert removed.status_code == 200
        assert removed.json()["deleted_card_count"] == 0
        with database.connection() as db:
            assert db.execute("SELECT repertoire_id FROM cards WHERE id=?", (card["id"],)).fetchone()[0] == identifiers[1]
            assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card["id"],)).fetchone()[0] == 1
