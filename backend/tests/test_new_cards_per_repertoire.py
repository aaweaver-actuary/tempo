"""
Test suite for per-repertoire new cards per day setting.
"""

from fastapi.testclient import TestClient
import json
from app import database
from app.main import app
from helpers import wait_for_integrity

THREE_LINES_WHITE = b"""[Event "White King pawn"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 *

[Event "White Queen pawn"]
[Result "*"]

1. e4 c5 2. Nf3 d6 3. d4 *

[Event "White English"]
[Result "*"]

1. e4 e6 2. d4 d5 3. Nc3 *
"""

THREE_LINES_BLACK = b"""[Event "Black French"]
[Result "*"]

1. e4 e6 2. d4 d5 *

[Event "Black Caro-Kann"]
[Result "*"]

1. e4 e6 2. Nf3 d5 *

[Event "Black Scandinavian"]
[Result "*"]

1. e4 e6 2. c4 d5 *
"""


def test_new_cards_per_day_applies_separately_to_each_repertoire(tmp_path, monkeypatch):
    """
    Verify that new_cards_per_day setting applies per-repertoire, not globally.

    E.g., if new_cards_per_day=2:
    - Repertoire A should get 2 new cards
    - Repertoire B should get 2 new cards
    - Total = 4 cards in queue

    NOT:
    - Total limit of 2 cards across all repertoires
    """
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        # Set new_cards_per_day to 2
        settings = client.get("/api/settings").json()
        settings["new_cards_per_day"] = 2
        assert client.put("/api/settings", json=settings).status_code == 200

        # Import first repertoire with 3 white lines
        repertoire_1 = client.post(
            "/api/imports/pgn",
            files={"file": ("white.pgn", THREE_LINES_WHITE, "application/x-chess-pgn")},
            data={"trained_color": "white", "initial_depth": "2"},
        ).json()
        assert repertoire_1["cards_created"] == 4
        wait_for_integrity(client, repertoire_1["repertoire_id"])

        # Import second repertoire with 3 black lines
        repertoire_2 = client.post(
            "/api/imports/pgn",
            files={"file": ("black.pgn", THREE_LINES_BLACK, "application/x-chess-pgn")},
            data={"trained_color": "black", "initial_depth": "2"},
        ).json()
        assert repertoire_2["cards_created"] == 4
        wait_for_integrity(client, repertoire_2["repertoire_id"])

        # Get today's queue
        queue = client.get("/api/queue/today").json()

        # Each repertoire has one shared root; descendants remain locked.
        assert queue["count"] == 2, f"Expected 2 cards in queue, got {queue['count']}"

        # Count cards by repertoire
        cards = queue["cards"]
        rep1_cards = [
            c for c in cards if c["repertoire_id"] == repertoire_1["repertoire_id"]
        ]
        rep2_cards = [
            c for c in cards if c["repertoire_id"] == repertoire_2["repertoire_id"]
        ]

        assert len(rep1_cards) == 1, (
            f"Expected 1 frontier card from repertoire 1, got {len(rep1_cards)}"
        )
        assert len(rep2_cards) == 1, (
            f"Expected 1 frontier card from repertoire 2, got {len(rep2_cards)}"
        )

        # Verify the cards are the right color
        assert all(c["trained_color"] == "white" for c in rep1_cards)
        assert all(c["trained_color"] == "black" for c in rep2_cards)

        # Verify repertoire metadata
        repertoires = client.get("/api/repertoires").json()["repertoires"]
        rep1 = next(r for r in repertoires if r["id"] == repertoire_1["repertoire_id"])
        rep2 = next(r for r in repertoires if r["id"] == repertoire_2["repertoire_id"])

        assert rep1["card_count"] == 4
        assert rep1["due_count"] == 1
        assert rep2["card_count"] == 4
        assert rep2["due_count"] == 1


def test_new_cards_per_day_respects_lower_limit(tmp_path, monkeypatch):
    """
    Verify that if new_cards_per_day is set to 1, only 1 card per repertoire is queued.
    """
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        # Set new_cards_per_day to 1
        settings = client.get("/api/settings").json()
        settings["new_cards_per_day"] = 1
        assert client.put("/api/settings", json=settings).status_code == 200

        # Import two repertoires with 2 cards each
        repertoire_1 = client.post(
            "/api/imports/pgn",
            files={"file": ("white.pgn", THREE_LINES_WHITE, "application/x-chess-pgn")},
            data={"trained_color": "white", "initial_depth": "2"},
        ).json()
        wait_for_integrity(client, repertoire_1["repertoire_id"])

        repertoire_2 = client.post(
            "/api/imports/pgn",
            files={"file": ("black.pgn", THREE_LINES_BLACK, "application/x-chess-pgn")},
            data={"trained_color": "black", "initial_depth": "2"},
        ).json()
        wait_for_integrity(client, repertoire_2["repertoire_id"])

        # Get today's queue
        queue = client.get("/api/queue/today").json()

        # Should have 2 cards total: 1 from each repertoire
        assert queue["count"] == 2
        cards = queue["cards"]
        rep1_cards = [
            c for c in cards if c["repertoire_id"] == repertoire_1["repertoire_id"]
        ]
        rep2_cards = [
            c for c in cards if c["repertoire_id"] == repertoire_2["repertoire_id"]
        ]

        assert len(rep1_cards) == 1
        assert len(rep2_cards) == 1


def test_new_cards_per_day_handles_uneven_repertoire_sizes(tmp_path, monkeypatch):
    """
    Verify per-repertoire limit when repertoires have different numbers of cards.

    E.g., with new_cards_per_day=2:
    - Rep1 has 5 cards -> 2 queued
    - Rep2 has 1 card  -> 1 queued
    - Total = 3 cards
    """
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        # Set new_cards_per_day to 2
        settings = client.get("/api/settings").json()
        settings["new_cards_per_day"] = 2
        assert client.put("/api/settings", json=settings).status_code == 200

        # Import first repertoire with 5 cards
        repertoire_1 = client.post(
            "/api/imports/pgn",
            files={"file": ("white.pgn", THREE_LINES_WHITE, "application/x-chess-pgn")},
            data={"trained_color": "white", "initial_depth": "2"},
        ).json()
        wait_for_integrity(client, repertoire_1["repertoire_id"])
        # Add two more lines to make it 5 total
        with database.connection() as db:
            rep_id = repertoire_1["repertoire_id"]
            # Create 2 more cards in this repertoire
            db.execute(
                "INSERT INTO cards(id, repertoire_id, kind, start_fen, moves_json, due_date, content_type) "
                "VALUES(?, ?, 'prefix', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', ?, ?, 'opening')",
                (
                    f"{rep_id}_extra1",
                    rep_id,
                    json.dumps(["e2e4", "c7c6", "d2d4"]),
                    "2026-09-18",
                ),
            )
            db.execute(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
                (rep_id, f"{rep_id}_extra1"),
            )
            db.execute(
                "INSERT INTO cards(id, repertoire_id, kind, start_fen, moves_json, due_date, content_type) "
                "VALUES(?, ?, 'prefix', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', ?, ?, 'opening')",
                (
                    f"{rep_id}_extra2",
                    rep_id,
                    json.dumps(["d2d4", "d7d5", "c2c4"]),
                    "2026-09-18",
                ),
            )
            db.execute(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
                (rep_id, f"{rep_id}_extra2"),
            )

        # Import second repertoire with 1 card
        ONE_LINE = b"""[Event "Single line"]
[Result "*"]

1. e4 e5 *
"""
        repertoire_2 = client.post(
            "/api/imports/pgn",
            files={"file": ("single.pgn", ONE_LINE, "application/x-chess-pgn")},
            data={"trained_color": "black", "initial_depth": "2"},
        ).json()
        assert repertoire_2["cards_created"] == 1
        wait_for_integrity(client, repertoire_2["repertoire_id"])

        # Get today's queue
        queue = client.get("/api/queue/today").json()

        # Should have 3 cards: 2 from rep1, 1 from rep2
        assert queue["count"] == 3
        cards = queue["cards"]
        rep1_cards = [
            c for c in cards if c["repertoire_id"] == repertoire_1["repertoire_id"]
        ]
        rep2_cards = [
            c for c in cards if c["repertoire_id"] == repertoire_2["repertoire_id"]
        ]

        assert len(rep1_cards) == 2
        assert len(rep2_cards) == 1
