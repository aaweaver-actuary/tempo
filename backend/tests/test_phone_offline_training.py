"""Phone queue replay keeps the desktop SQLite database authoritative."""

from datetime import date, datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services.scheduler import schedule_review
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


def test_phone_review_after_computer_review_is_credited_once_in_completion_order(tmp_path, monkeypatch):
    """A phone review must survive a completed desktop queue attempt and retries."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        path = f"/api/cards/{card['id']}/review"
        with database.connection() as connection:
            initial = dict(connection.execute(
                "SELECT interval_days,fsrs_card_json,first_correct_at,reinforcement_pending,"
                "scheduling_mode,hard_correct_streak,recent_attempts_json FROM cards WHERE id=?",
                (card["id"],),
            ).fetchone())
            light_first_interval_days = connection.execute(
                "SELECT light_first_interval_days FROM settings WHERE id=1",
            ).fetchone()[0]
        phone_time = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
        computer_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        computer = client.post(path, json={
            "outcome": "again", "queue_entry_id": card["queue_entry_id"],
            "recorded_at": computer_time, "attempt_id": "computer-1",
        })
        assert computer.status_code == 200
        phone_request = {
            "outcome": "correct", "queue_entry_id": card["queue_entry_id"],
            "recorded_at": phone_time, "attempt_id": "phone-1",
            "expected_review_id": card["latest_review_id"],
            "expected_revision": card["revision"],
        }
        phone = client.post(path, json=phone_request)
        assert phone.status_code == 200
        assert phone.json()["reconciliation"] == "chronological"
        assert phone.json()["warning"] is None
        assert client.post(path, json=phone_request).json() == phone.json()
        with database.connection() as connection:
            reviews = connection.execute(
                "SELECT rating,reviewed_at FROM reviews WHERE card_id=? ORDER BY reviewed_at,id",
                (card["id"],),
            ).fetchall()
            assert [(row["rating"], row["reviewed_at"]) for row in reviews] == [
                ("correct", phone_time), ("again", computer_time),
            ]
            assert connection.execute(
                "SELECT COUNT(*) FROM review_attempt_receipts WHERE card_id=?", (card["id"],),
            ).fetchone()[0] == 2
            expected_phone = schedule_review(
                "correct", interval_days=initial["interval_days"],
                fsrs_card_json=initial["fsrs_card_json"],
                first_correct_at=initial["first_correct_at"],
                reinforcement_pending=bool(initial["reinforcement_pending"]),
                scheduling_mode=initial["scheduling_mode"],
                hard_correct_streak=initial["hard_correct_streak"],
                recent_attempts=json.loads(initial["recent_attempts_json"]),
                light_first_interval_days=light_first_interval_days,
                reviewed_at=datetime.fromisoformat(phone_time),
                review_day=date.today(),
            )
            expected_computer = schedule_review(
                "again", interval_days=expected_phone.interval_days,
                fsrs_card_json=expected_phone.fsrs_card_json,
                first_correct_at=expected_phone.first_correct_at,
                reinforcement_pending=expected_phone.reinforcement_pending,
                scheduling_mode=expected_phone.scheduling_mode,
                hard_correct_streak=expected_phone.hard_correct_streak,
                recent_attempts=list(expected_phone.recent_attempts),
                light_first_interval_days=light_first_interval_days,
                reviewed_at=datetime.fromisoformat(computer_time),
                review_day=date.today(),
            )
            final = connection.execute(
                "SELECT due_date,fsrs_card_json FROM cards WHERE id=?", (card["id"],),
            ).fetchone()
            assert final["due_date"] == expected_computer.due_date.isoformat()
            actual_fsrs = json.loads(final["fsrs_card_json"])
            expected_fsrs = json.loads(expected_computer.fsrs_card_json)
            actual_fsrs.pop("card_id")
            expected_fsrs.pop("card_id")
            assert actual_fsrs == expected_fsrs


def test_older_phone_conflict_keeps_computer_schedule_and_warns_when_snapshot_missing(tmp_path, monkeypatch):
    """Legacy phone results are credited even when historical state cannot be replayed."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        path = f"/api/cards/{card['id']}/review"
        computer = client.post(path, json={
            "outcome": "again", "queue_entry_id": card["queue_entry_id"],
        })
        assert computer.status_code == 200
        with database.connection() as connection:
            connection.execute("DELETE FROM review_schedule_snapshots")
        phone = client.post(path, json={
            "outcome": "correct", "queue_entry_id": card["queue_entry_id"],
            "recorded_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
            "attempt_id": "legacy-phone-1", "expected_revision": card["revision"],
        })
        assert phone.status_code == 200
        assert phone.json()["reconciliation"] == "computer_fallback"
        assert "computer schedule" in phone.json()["warning"]
        with database.connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id=?", (card["id"],),
            ).fetchone()[0] == 2


def test_legacy_phone_retry_after_uncertain_save_does_not_count_twice(tmp_path, monkeypatch):
    """An old phone journal can adopt its already-saved result after upgrading."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        path = f"/api/cards/{card['id']}/review"
        completed_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        original = {
            "outcome": "correct", "queue_entry_id": card["queue_entry_id"],
            "recorded_at": completed_at,
        }
        assert client.post(path, json=original).status_code == 200
        replayed = client.post(path, json={**original, "attempt_id": "old-phone-retry"})
        assert replayed.status_code == 200
        assert replayed.json()["idempotent"]
        with database.connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id=?", (card["id"],),
            ).fetchone()[0] == 1


def test_phone_repeat_is_credited_when_computer_did_not_schedule_that_repeat(tmp_path, monkeypatch):
    """A locally completed repeat remains a distinct review of the original attempt."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        path = f"/api/cards/{card['id']}/review"
        first_time = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
        repeat_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        with database.connection() as connection:
            connection.execute("UPDATE cards SET first_correct_at=? WHERE id=?", (first_time, card["id"]))
        assert client.post(path, json={
            "outcome": "correct", "queue_entry_id": card["queue_entry_id"],
            "recorded_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "attempt_id": "computer-no-repeat",
        }).status_code == 200
        for attempt_id, completed_at in (("phone-first", first_time), ("phone-repeat", repeat_time)):
            response = client.post(path, json={
                "outcome": "correct", "queue_entry_id": card["queue_entry_id"],
                "recorded_at": completed_at, "attempt_id": attempt_id,
                "expected_revision": card["revision"],
            })
            assert response.status_code == 200
            assert response.json()["reconciliation"] == "chronological"
        with database.connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM reviews WHERE card_id=?", (card["id"],),
            ).fetchone()[0] == 3


def test_phone_review_of_changed_card_is_preserved_with_actionable_warning(tmp_path, monkeypatch):
    """A changed card keeps the phone evidence without applying an obsolete grade."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        card = _prepared_card(client)
        path = f"/api/cards/{card['id']}/review"
        assert client.post(path, json={
            "outcome": "again", "queue_entry_id": card["queue_entry_id"],
            "attempt_id": "computer-edited",
        }).status_code == 200
        with database.connection() as connection:
            connection.execute("UPDATE cards SET revision=revision+1 WHERE id=?", (card["id"],))
        phone = client.post(path, json={
            "outcome": "correct", "queue_entry_id": card["queue_entry_id"],
            "attempt_id": "phone-before-edit", "expected_revision": card["revision"],
            "recorded_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
        })
        assert phone.status_code == 200
        assert phone.json()["reconciliation"] == "history_only"
        assert "changed" in phone.json()["warning"]
        with database.connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM review_attempt_receipts WHERE card_id=?", (card["id"],),
            ).fetchone()[0] == 2


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
