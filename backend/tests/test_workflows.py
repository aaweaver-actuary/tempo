from fastapi.testclient import TestClient

from app import database
from app.main import app


PGN = b'''[Event "Persistent repertoire"]
[Opening "Italian Game"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d3 d6 6. O-O O-O *
'''


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
