from datetime import date, datetime, timedelta, timezone
import time

from fastapi.testclient import TestClient
from app import database
from app.main import app, enqueue_daily_queue_refresh
from helpers import wait_for_daily_queue, wait_for_integrity

PGN = b'[Event "Durability"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 *'

def test_tactic_discovery_uses_local_day_after_utc_midnight(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    class LocalDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 16)
    class UtcClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 17, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "date", LocalDate)
    monkeypatch.setattr(main, "datetime", UtcClock)
    with TestClient(app) as client:
        result = client.post("/api/tactics/attempt", json={
            "attempt_id":"utc-lapse","puzzle_id":"utc-puzzle","deck_id":"fork-easy",
            "correct":False,"clean":False,
            "source_fen":"8/8/8/8/8/4k3/7p/6K1 b - - 0 1",
            "moves":["h2h1q","g1h1"],
        })
        assert result.status_code == 200
        assert result.json()["next_due"] == "2026-09-16"
        assert client.get("/api/queue/today").json()["count"] == 1

def test_default_scheduler_uses_local_day_not_utc_day(monkeypatch):
    import app.services.scheduler as scheduler
    class LocalDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 16)
    class UtcClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 17, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler, "date", LocalDate)
    monkeypatch.setattr(scheduler, "datetime", UtcClock)
    assert scheduler.schedule_review("again").due_date == date(2026, 9, 16)

def test_tomorrow_is_the_local_review_day_even_after_utc_midnight():
    from app.services.scheduler import schedule_review
    utc_now = datetime(2026, 9, 17, 1, tzinfo=timezone.utc)
    local_day = date(2026, 9, 16)
    first = schedule_review("correct", reviewed_at=utc_now, review_day=local_day)
    assert first.due_date == local_day and first.requeue_today
    second = schedule_review("correct", fsrs_card_json=first.fsrs_card_json, first_correct_at=first.first_correct_at, reinforcement_pending=True, reviewed_at=utc_now + timedelta(minutes=5), review_day=local_day)
    assert second.due_date == date(2026, 9, 17) and second.interval_days == 1

def install_clock(monkeypatch):
    import app.main as main
    clock = {"now": datetime(2026, 9, 16, 12, tzinfo=timezone.utc)}
    class ClockDate(date):
        @classmethod
        def today(cls): return clock["now"].date()
    class ClockDatetime(datetime):
        @classmethod
        def now(cls, tz=None): return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)
    monkeypatch.setattr(main, "date", ClockDate)
    monkeypatch.setattr(main, "datetime", ClockDatetime)
    return clock

def solve(client, card, outcome="correct"):
    response = client.post(f"/api/cards/{card['id']}/review", json={"outcome": outcome, "queue_entry_id": card["queue_entry_id"]})
    assert response.status_code == 200
    return response.json()

def test_study_reinforces_today_reviews_tomorrow_and_persists_unassisted_later_reviews(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    clock = install_clock(monkeypatch)
    with TestClient(app) as client:
        imported = client.post("/api/imports/pgn", files={"file": ("durability.pgn", PGN)}, data={"initial_depth": 2})
        wait_for_integrity(client, imported.json()["repertoire_id"])
        first = client.get("/api/queue/today").json()["cards"][0]
        for ply in (0, 2):
            assert client.post(f"/api/cards/{first['id']}/teaching", json={"revision": 1, "ply": ply}).status_code == 200
        result = solve(client, first)
        assert result["requeue_today"] and result["next_due"] == "2026-09-16"
        reinforcement = client.get("/api/queue/today").json()["cards"][0]
        assert reinforcement["attempt_state"] == "reinforcement" and reinforcement["first_correct_at"]
        assert reinforcement["queue_entry_id"] != first["queue_entry_id"]
        result = solve(client, reinforcement)
        assert result["next_due"] == "2026-09-17" and result["interval_days"] == 1
        assert client.get("/api/queue/today").json()["count"] == 0
        before = client.get("/api/migration/snapshot").json()
    with TestClient(app) as client:  # Re-open SQLite and run startup, not an in-memory store.
        assert client.get("/api/migration/snapshot").json()["checksum"] == before["checksum"]
        for _ in range(3):
            clock["now"] = datetime.fromisoformat(result["next_due"]).replace(hour=12, tzinfo=timezone.utc)
            enqueue_daily_queue_refresh()
            queue = []
            for _ in range(200):
                queue = client.get("/api/queue/today").json()["cards"]
                if queue:
                    break
                time.sleep(0.01)
            assert len(queue) == 1 and queue[0]["first_correct_at"]
            states = client.get(f"/api/cards/{first['id']}/teaching").json()["states"]
            assert {(state["revision"], state["ply"]) for state in states} == {(1, 0), (1, 2)}
            result = solve(client, queue[0])
            assert not result["requeue_today"] and 1 <= result["interval_days"] <= 365
            assert date.fromisoformat(result["next_due"]) > clock["now"].date()
        assert client.get("/api/migration/snapshot").json()["counts"]["reviews"] == 5

def test_tactical_failures_requeue_once_and_clean_reviews_survive_restart_and_return_when_due(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    clock = install_clock(monkeypatch)
    payload = {"attempt_id": "durable-discovery", "puzzle_id": "durable-tactic", "deck_id": "hangingPiece-easy", "correct": False, "clean": False,
               "source_fen": "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17", "moves": ["e8d7", "a2e6", "d7d8", "f7f8"]}
    with TestClient(app) as client:
        assert client.post("/api/tactics/attempt", json=payload).status_code == 200
        assert client.post("/api/tactics/attempt", json=payload).status_code == 200
        first = client.get("/api/queue/today").json()["cards"][0]
        solve(client, first, "again")
        queued = client.get("/api/queue/today").json()["cards"]
        assert len(queued) == 1 and queued[0]["queue_entry_id"] != first["queue_entry_id"]
        solve(client, queued[0])
        reinforced = client.get("/api/queue/today").json()["cards"][0]
        result = solve(client, reinforced)
        assert result["next_due"] == "2026-09-17"
        snapshot = client.get("/api/migration/snapshot").json()
    with TestClient(app) as client:
        assert client.get("/api/migration/snapshot").json()["checksum"] == snapshot["checksum"]
        assert client.get("/api/tactics/progress").json()["hangingPiece:easy"]["index"] == 1
        clock["now"] += timedelta(days=1)
        enqueue_daily_queue_refresh()
        returned = wait_for_daily_queue(client, 1)["cards"]
        assert len(returned) == 1 and returned[0]["id"] == first["id"] and returned[0]["content_type"] == "tactic"
        result = solve(client, returned[0])
        assert not result["requeue_today"] and result["next_due"] > "2026-09-17"
