from fastapi.testclient import TestClient
import time
from app import database
from app.main import app
from helpers import wait_for_integrity


def test_branch_removal_unlinks_or_archives_only_unreferenced_decision_cards(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        identifiers = []
        for name in ("first.pgn", "second.pgn"):
            response = client.post("/api/imports/pgn", files={"file": (name, b'[Event "Shared"]\n\n1. d4 Nf6 2. c4 e6 3. Nf3 *')}, data={"trained_color": "white", "initial_depth": 2})
            identifiers.append(response.json()["repertoire_id"])
            wait_for_integrity(client, identifiers[-1])
        card = client.get("/api/queue/today").json()["cards"][0]
        assert client.post(f'/api/cards/{card["id"]}/review', json={"outcome": "correct", "queue_entry_id": card["queue_entry_id"]}).status_code == 200
        removed = client.post("/api/repertoire/branches/remove", json={"repertoire_id": identifiers[0], "starting_fen": card["start_fen"], "moves": ["d2d4", "g8f6"]})
        assert removed.status_code == 200
        assert removed.json()["deleted_card_count"] == 0
        owner = None
        for _ in range(200):
            with database.connection() as db:
                owner = db.execute(
                    "SELECT repertoire_id FROM cards WHERE id=?", (card["id"],)
                ).fetchone()[0]
                review_count = db.execute(
                    "SELECT COUNT(*) FROM reviews WHERE card_id=?", (card["id"],)
                ).fetchone()[0]
            if owner == identifiers[1]:
                break
            time.sleep(0.01)
        assert owner == identifiers[1]
        assert review_count == 1
