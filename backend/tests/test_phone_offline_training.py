"""Phone queue replay keeps the desktop SQLite database authoritative."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app import database
from app.main import app
from helpers import wait_for_integrity


PGN = b'[Event "Phone queue"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. d4 *'


def _prepared_card(client):
    imported = client.post(
        "/api/imports/pgn", files={"file": ("phone.pgn", PGN)},
        data={"initial_depth": 2},
    ).json()
    wait_for_integrity(client, imported["repertoire_id"])
    prepared = client.get("/api/queue/prepared").json()
    assert prepared["projection"]["state"] == "ready"
    assert prepared["count"] == len(prepared["cards"])
    assert prepared["prepared_at"]
    return prepared["cards"][0]


def test_phone_prepared_queue_replays_repeats_once_at_the_original_study_time(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        recorded_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        request = {
            "outcome": "correct", "guided": False,
            "queue_entry_id": card["queue_entry_id"],
            "expected_review_id": card["latest_review_id"],
            "recorded_at": recorded_at,
        }
        first = client.post(f"/api/cards/{card['id']}/review", json=request)
        assert first.status_code == 200
        first_result = first.json()
        assert first_result["requeue_entry_id"] != card["queue_entry_id"]
        duplicate = client.post(f"/api/cards/{card['id']}/review", json=request)
        assert duplicate.status_code == 200 and duplicate.json()["idempotent"]
        repeat = client.post(f"/api/cards/{card['id']}/review", json={
            "outcome": "correct", "guided": False,
            "queue_entry_id": first_result["requeue_entry_id"],
            "expected_review_id": first_result["review_id"],
            "recorded_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        })
        assert repeat.status_code == 200
        with database.connection() as connection:
            reviews = connection.execute(
                "SELECT reviewed_at FROM reviews WHERE card_id=? ORDER BY id", (card["id"],),
            ).fetchall()
        assert len(reviews) == 2
        assert reviews[0]["reviewed_at"] == recorded_at


def test_phone_replay_uses_the_actual_local_study_date_for_scheduling(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        with database.connection() as connection:
            connection.execute("UPDATE settings SET timezone='America/New_York' WHERE id=1")
        recorded_at = (datetime.now(timezone.utc) - timedelta(days=2)).replace(
            hour=3, minute=0, second=0, microsecond=0,
        )
        expected_study_date = recorded_at.astimezone(ZoneInfo("America/New_York")).date()
        result = client.post(f"/api/cards/{card['id']}/review", json={
            "outcome": "correct", "queue_entry_id": card["queue_entry_id"],
            "expected_review_id": card["latest_review_id"],
            "recorded_at": recorded_at.isoformat(),
        })
        assert result.status_code == 200
        assert result.json()["next_due"] == expected_study_date.isoformat()


def test_phone_prepared_queue_rejects_competing_computer_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        path = f"/api/cards/{card['id']}/review"
        computer = client.post(path, json={
            "outcome": "again", "queue_entry_id": card["queue_entry_id"],
        })
        assert computer.status_code == 200
        phone = client.post(path, json={
            "outcome": "correct", "queue_entry_id": card["queue_entry_id"],
            "expected_review_id": card["latest_review_id"],
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        assert phone.status_code == 409
        with database.connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id=?", (card["id"],),
            ).fetchone()[0] == 1


def test_phone_prepared_queue_rejects_a_card_edited_after_preparation(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        with database.connection() as connection:
            connection.execute("UPDATE cards SET revision=revision+1 WHERE id=?", (card["id"],))
        response = client.post(f"/api/cards/{card['id']}/review", json={
            "outcome": "correct", "queue_entry_id": card["queue_entry_id"],
            "expected_review_id": card["latest_review_id"],
            "expected_revision": card["revision"],
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        assert response.status_code == 409
        assert "changed" in response.json()["detail"]


def test_phone_prepared_queue_contains_every_entry_beyond_the_live_window(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        with database.connection() as connection:
            for offset in range(25):
                connection.execute(
                    "INSERT INTO daily_queue(queue_date,card_id,cycle,position) VALUES(?,?,?,?)",
                    (date.today().isoformat(), card["id"], 100 + offset, 100 + offset),
                )
        live_window = client.get("/api/queue/window?limit=20").json()
        prepared = client.get("/api/queue/prepared").json()
        assert len(live_window["cards"]) == 20
        assert prepared["count"] == len(prepared["cards"])
        assert len(prepared["cards"]) > 20
