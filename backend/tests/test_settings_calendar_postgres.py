"""Native calendar follow-up and two-day lease interleaving on disposable PostgreSQL."""
from datetime import date, datetime, time, timedelta, timezone
import json
import sys
from pathlib import Path

import chess
from psycopg.conninfo import make_conninfo

from native_study_fixture import owned_postgres
from tempo_storage import postgres
from app.services import durable_tasks, postgres_queue_refresh as refresh, queue_application
from app import settings_commands
from app.contracts.transport.settings import Settings
from app.study.policy.clock import study_calendar_at

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database/tests"))
from queue_refresh_scale_fixture import seed_queue_refresh_case, protected_hash


def test_backward_zone_preserves_leased_generation_and_processes_selected_day(owned_postgres, monkeypatch):
    """Resume old delivery after settings changes; actual slices publish new day."""
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    url = make_conninfo(**owned_postgres.connection_options)
    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", url)
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", url)
    postgres.prepare_authoritative_background_pool()
    now = datetime.combine(date.today(), time(2), tzinfo=timezone.utc)
    later = now.date().isoformat()
    earlier = (now.date() - timedelta(days=1)).isoformat()
    try:
        with owned_postgres() as db:
            facts = seed_queue_refresh_case(db, queued_count=32, frozen_active=False)
            db.execute_native("DELETE FROM background_tasks WHERE id=%s", (facts.task_id,))
            db.execute_native("UPDATE cards SET due_date=%s WHERE id=%s", (earlier, "queue-refresh-scale-00003"))
            before = protected_hash(db.raw, facts.issued_ids)
            old = queue_application.request_queue_refresh_in_transaction(db, later)
        first = durable_tasks.claim_task("daily_queue")
        assert first["id"] == old["id"]
        assert refresh.execute_postgres_queue_refresh_slice(first)
        old_delivery = durable_tasks.claim_task("daily_queue")
        assert old_delivery["id"] == old["id"]
        with owned_postgres() as db:
            old_row = dict(db.execute_native("SELECT * FROM background_tasks WHERE id=%s", (old["id"],)).fetchone())
        monkeypatch.setattr(settings_commands, "capture_study_calendar", lambda db: study_calendar_at(now, "America/New_York"))
        with owned_postgres() as db:
            settings_commands.apply_settings(db, settings_commands.SettingsRequest(Settings(timezone="America/New_York"), frozenset({"timezone"})))
        with owned_postgres() as db:
            after = dict(db.execute_native("SELECT * FROM background_tasks WHERE id=%s", (old["id"],)).fetchone())
            new = dict(db.execute_native("SELECT * FROM background_tasks WHERE kind='daily_queue' AND deduplication_key=%s", ("current-day:" + earlier,)).fetchone())
            for key in ("id", "generation", "state", "lease_token", "payload_json"):
                assert after[key] == old_row[key]
            assert new["id"] != old["id"] and json.loads(new["payload_json"])["queue_date"] == earlier
        assert refresh.execute_postgres_queue_refresh_slice(old_delivery)
        assert not refresh.execute_postgres_queue_refresh_slice(old_delivery)
        for _ in range(300):
            with owned_postgres() as db:
                projection = db.execute_native("SELECT state,refresh_pending FROM queue_projections WHERE queue_date=%s", (earlier,)).fetchone()
                if projection and projection["state"] == "ready" and not projection["refresh_pending"]:
                    break
            task = durable_tasks.claim_task("daily_queue")
            assert task is not None
            refresh.execute_postgres_queue_refresh_slice(task)
        else:
            raise AssertionError("selected current day did not reach ready")
        with owned_postgres() as db:
            assert protected_hash(db.raw, facts.issued_ids) == before
            row = db.execute_native("SELECT c.start_fen,c.moves_json FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=%s AND q.status='queued' ORDER BY q.position LIMIT 1", (earlier,)).fetchone()
            assert row, "ready projection did not contain a playable queued card"
            board = chess.Board(row["start_fen"])
            assert board.parse_uci(json.loads(row["moves_json"])[0]) in board.legal_moves
    finally:
        postgres.close_pools()


def test_settings_receipt_replay_and_failed_followup_rollback(owned_postgres, monkeypatch):
    from fastapi import HTTPException
    from app import command_gateway as gateway
    from app.services import queue_refresh_wakeup, study_priority
    monkeypatch.setattr(gateway, "_writer_connection", lambda background: owned_postgres())
    monkeypatch.setattr(study_priority, "refresh_policy", lambda: None)
    monkeypatch.setattr(queue_refresh_wakeup, "wake_queue_refresh", lambda: None)
    now = datetime(2026, 11, 12, 2, tzinfo=timezone.utc)
    captures = []
    def calendar(db):
        captures.append(True)
        return study_calendar_at(now, "America/New_York")
    monkeypatch.setattr(settings_commands, "capture_study_calendar", calendar)
    payload = {"settings": Settings(timezone="America/New_York", new_cards_per_day=13).model_dump(), "supplied_fields":["timezone", "new_cards_per_day"]}
    first = gateway.execute_command("calendar-settings-success", "settings.update", payload)
    with owned_postgres() as db:
        row = db.execute_native("SELECT timezone,new_cards_per_day FROM settings WHERE id=1").fetchone()
        assert (row["timezone"], row["new_cards_per_day"]) == ("America/New_York", 13)
        projection = db.execute_native("SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s", ("2026-11-11",)).fetchone()
        epoch = projection[0]
        receipt = db.execute_native("SELECT state,response_json FROM operation_receipts WHERE operation_id=%s", ("calendar-settings-success",)).fetchone()
        assert receipt["state"] == "complete" and json.loads(receipt["response_json"]) == first
    assert gateway.execute_command("calendar-settings-success", "settings.update", payload) == first
    assert len(captures) == 1
    with owned_postgres() as db:
        assert db.execute_native("SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s", ("2026-11-11",)).fetchone()[0] == epoch
    actual_request = settings_commands.request_queue_refresh_in_transaction
    def fail_after_request(db, day):
        actual_request(db, day)
        raise HTTPException(503, "forced rollback")
    monkeypatch.setattr(settings_commands, "request_queue_refresh_in_transaction", fail_after_request)
    failed = {"settings": Settings(timezone="America/New_York", new_cards_per_day=14).model_dump(), "supplied_fields":["timezone", "new_cards_per_day"]}
    assert gateway.execute_command("calendar-settings-failure", "settings.update", failed) is None
    with owned_postgres() as db:
        assert db.execute_native("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[0] == 13
        assert db.execute_native("SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s", ("2026-11-11",)).fetchone()[0] == epoch
        assert db.execute_native("SELECT state FROM operation_receipts WHERE operation_id=%s", ("calendar-settings-failure",)).fetchone()[0] == "failed"
