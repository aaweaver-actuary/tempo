"""Native calendar follow-up and two-day lease interleaving on disposable PostgreSQL."""

import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import chess
from psycopg.conninfo import make_conninfo
from tempo_storage import postgres

from app import settings_commands
from app.contracts.transport.settings import Settings
from app.services import (
    durable_tasks,
    queue_application,
)
from app.services import (
    postgres_queue_refresh as refresh,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database/tests"))
from queue_refresh_scale_fixture import protected_hash, seed_queue_refresh_case


def test_backward_zone_preserves_leased_generation_and_processes_selected_day(
    owned_postgres, monkeypatch
):
    """Resume old delivery after settings changes; actual slices publish new day."""
    from app.product_composition.database_infrastructure import (
        bind_database_infrastructure,
    )

    bind_database_infrastructure()
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    url = make_conninfo(**owned_postgres.connection_options)
    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", url)
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", url)
    postgres.prepare_authoritative_background_pool()
    now = datetime.combine(date.today(), time(2), tzinfo=UTC)
    later = now.date().isoformat()
    earlier = (now.date() - timedelta(days=1)).isoformat()
    clock = [now]

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0] if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(durable_tasks, "_now", lambda: clock[0])
    from app.services import study_calendar

    monkeypatch.setattr(study_calendar, "datetime", FixedClock)
    monkeypatch.setattr(refresh, "datetime", FixedClock)
    try:
        with owned_postgres() as db:
            db.execute_native("UPDATE settings SET timezone='UTC' WHERE id=1")
            facts = seed_queue_refresh_case(db, queued_count=32, frozen_active=False)
            db.execute_native(
                "DELETE FROM background_tasks WHERE id=%s", (facts.task_id,)
            )
            from tempo_chess_core import card_id

            candidate = card_id(chess.STARTING_FEN, ["e2e4"])
            db.execute_native(
                "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,content_type) VALUES(%s,'queue-refresh-scale','prefix',%s,'[\"e2e4\"]','white','new',%s,'opening')",
                (candidate, chess.STARTING_FEN, earlier),
            )
            db.execute_native(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('queue-refresh-scale',%s)",
                (candidate,),
            )
            db.execute_native(
                "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('calendar-opening','queue-refresh-scale','Calendar','white',%s,'[\"e2e4\"]',%s)",
                (chess.STARTING_FEN, earlier),
            )
            db.execute_native(
                "INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES('queue-refresh-scale',1,'calendar-opening',0,%s,%s,%s,'[\"e2e4\"]','white')",
                (candidate, chess.STARTING_FEN, chess.STARTING_FEN),
            )
            before = protected_hash(db.raw, facts.issued_ids)
            old = queue_application.request_queue_refresh_in_transaction(db, later)
        # Reach the real checkpoint after a bounded page has selected the
        # shared opening, then keep its lease across the timezone change.
        for _ in range(300):
            old_delivery = durable_tasks.claim_task("daily_queue")
            assert old_delivery is not None and old_delivery["id"] == old["id"]
            if old_delivery["payload"].get("_queue_phase") == "study_opening_item":
                assert (
                    old_delivery["payload"]["study_opening_items"][0]["card_id"]
                    == candidate
                )
                break
            assert refresh.execute_postgres_queue_refresh_slice(old_delivery)
        else:
            raise AssertionError("future task did not reach opening admission")
        with owned_postgres() as db:
            old_row = dict(
                db.execute_native(
                    "SELECT * FROM background_tasks WHERE id=%s", (old["id"],)
                ).fetchone()
            )
        with owned_postgres() as db:
            settings_commands.apply_settings(
                db,
                settings_commands.SettingsRequest(
                    Settings(timezone="America/New_York"), frozenset({"timezone"})
                ),
            )
        with owned_postgres() as db:
            after = dict(
                db.execute_native(
                    "SELECT * FROM background_tasks WHERE id=%s", (old["id"],)
                ).fetchone()
            )
            new = dict(
                db.execute_native(
                    "SELECT * FROM background_tasks WHERE kind='daily_queue' AND deduplication_key=%s",
                    ("current-day:" + earlier,),
                ).fetchone()
            )
            for key in ("id", "generation", "state", "lease_token", "payload_json"):
                assert after[key] == old_row[key]
            assert (
                new["id"] != old["id"]
                and json.loads(new["payload_json"])["queue_date"] == earlier
            )
        assert not refresh.execute_postgres_queue_refresh_slice(old_delivery)
        assert not refresh.execute_postgres_queue_refresh_slice(old_delivery)
        with owned_postgres() as db:
            held = db.execute_native(
                "SELECT state,generation,payload_json FROM background_tasks WHERE id=%s",
                (old["id"],),
            ).fetchone()
            assert (
                held["state"] == "retrying"
                and held["generation"] == old_row["generation"]
            )
            assert held["payload_json"] == old_row["payload_json"]
            candidate_before = db.execute_native(
                "SELECT state,introduced_at FROM cards WHERE id=%s", (candidate,)
            ).fetchone()
            assert (candidate_before["state"], candidate_before["introduced_at"]) == (
                "new",
                None,
            )
        for _ in range(300):
            with owned_postgres() as db:
                projection = db.execute_native(
                    "SELECT state,refresh_pending FROM queue_projections WHERE queue_date=%s",
                    (earlier,),
                ).fetchone()
                if (
                    projection
                    and projection["state"] == "ready"
                    and not projection["refresh_pending"]
                ):
                    break
            task = durable_tasks.claim_task("daily_queue")
            assert task is not None
            refresh.execute_postgres_queue_refresh_slice(task)
        else:
            raise AssertionError("selected current day did not reach ready")
        with owned_postgres() as db:
            assert protected_hash(db.raw, facts.issued_ids) == before
            candidate_after = db.execute_native(
                "SELECT state,introduced_at FROM cards WHERE id=%s", (candidate,)
            ).fetchone()
            assert candidate_after["introduced_at"] == earlier
            assert db.execute_native(
                "SELECT 1 FROM daily_queue WHERE queue_date=%s AND card_id=%s AND status='queued'",
                (earlier, candidate),
            ).fetchone()
            row = db.execute_native(
                "SELECT c.start_fen,c.moves_json FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=%s AND q.status='queued' ORDER BY q.position LIMIT 1",
                (earlier,),
            ).fetchone()
            assert row, "ready projection did not contain a playable queued card"
            board = chess.Board(row["start_fen"])
            assert (
                board.parse_uci(json.loads(row["moves_json"])[0]) in board.legal_moves
            )
        # The preserved future generation becomes eligible after the Study day
        # catches up; periodic polling uses the same injected task clock.
        clock[0] += timedelta(days=1)
        for _ in range(300):
            with owned_postgres() as db:
                state = db.execute_native(
                    "SELECT state FROM background_tasks WHERE id=%s", (old["id"],)
                ).fetchone()[0]
            if state == "complete":
                break
            delivery = durable_tasks.claim_task("daily_queue")
            assert delivery is not None and delivery["id"] == old["id"]
            refresh.execute_postgres_queue_refresh_slice(delivery)
        else:
            raise AssertionError("original future-day task did not resume")
    finally:
        postgres.close_pools()


def test_settings_receipt_replay_and_failed_followup_rollback(
    owned_postgres, monkeypatch
):
    from fastapi import HTTPException

    from app import command_gateway as gateway
    from app.services import queue_refresh_wakeup, study_priority

    monkeypatch.setattr(
        gateway, "_writer_connection", lambda background: owned_postgres()
    )
    monkeypatch.setattr(study_priority, "refresh_policy", lambda: None)
    monkeypatch.setattr(queue_refresh_wakeup, "wake_queue_refresh", lambda: None)
    now = datetime(2026, 11, 12, 2, tzinfo=UTC)
    captures = []
    from app.services import study_calendar

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            captures.append(True)
            return now if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(study_calendar, "datetime", FixedClock)
    payload = {
        "settings": Settings(
            timezone="America/New_York", new_cards_per_day=13
        ).model_dump(),
        "supplied_fields": ["timezone", "new_cards_per_day"],
    }
    first = gateway.execute_command(
        "calendar-settings-success", "settings.update", payload
    )
    with owned_postgres() as db:
        row = db.execute_native(
            "SELECT timezone,new_cards_per_day FROM settings WHERE id=1"
        ).fetchone()
        assert (row["timezone"], row["new_cards_per_day"]) == ("America/New_York", 13)
        projection = db.execute_native(
            "SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s",
            ("2026-11-11",),
        ).fetchone()
        epoch = projection[0]
        receipt = db.execute_native(
            "SELECT state,response_json FROM operation_receipts WHERE operation_id=%s",
            ("calendar-settings-success",),
        ).fetchone()
        assert (
            receipt["state"] == "complete"
            and json.loads(receipt["response_json"]) == first
        )
    assert (
        gateway.execute_command("calendar-settings-success", "settings.update", payload)
        == first
    )
    assert len(captures) == 1
    with owned_postgres() as db:
        assert (
            db.execute_native(
                "SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s",
                ("2026-11-11",),
            ).fetchone()[0]
            == epoch
        )
    actual_request = settings_commands.request_queue_refresh_in_transaction

    def fail_after_request(db, day):
        actual_request(db, day)
        raise HTTPException(503, "forced rollback")

    monkeypatch.setattr(
        settings_commands, "request_queue_refresh_in_transaction", fail_after_request
    )
    failed = {
        "settings": Settings(
            timezone="America/New_York", new_cards_per_day=14
        ).model_dump(),
        "supplied_fields": ["timezone", "new_cards_per_day"],
    }
    assert (
        gateway.execute_command("calendar-settings-failure", "settings.update", failed)
        is None
    )
    with owned_postgres() as db:
        assert (
            db.execute_native(
                "SELECT new_cards_per_day FROM settings WHERE id=1"
            ).fetchone()[0]
            == 13
        )
        assert (
            db.execute_native(
                "SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s",
                ("2026-11-11",),
            ).fetchone()[0]
            == epoch
        )
        assert (
            db.execute_native(
                "SELECT state FROM operation_receipts WHERE operation_id=%s",
                ("calendar-settings-failure",),
            ).fetchone()[0]
            == "failed"
        )


# This case needs a fresh invocation-owned database, independent of the B02
# case's current singleton and without borrowing installed state.
import pytest
from native_study_fixture import owned_postgres as _owned_postgres_factory


@pytest.fixture
def missing_day_postgres(tmp_path_factory):
    yield from _owned_postgres_factory.__wrapped__(tmp_path_factory)


@pytest.mark.parametrize("has_epoch", [False, True])
def test_missing_accepted_day_stays_held_while_known_current_queue_progresses(
    missing_day_postgres, monkeypatch, has_epoch
):
    from app.product_composition.database_infrastructure import (
        bind_database_infrastructure,
    )

    bind_database_infrastructure()
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    url = make_conninfo(**missing_day_postgres.connection_options)
    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", url)
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", url)
    postgres.prepare_authoritative_background_pool()
    payload = {"_queue_phase": "admit_due", "preserve_through_entry_id": 1}
    if has_epoch:
        payload["_refresh_cycle_epoch"] = 42
    payload_json = json.dumps(payload, separators=(",", ":"))
    try:
        with missing_day_postgres() as db:
            db.execute_native("UPDATE settings SET timezone='UTC' WHERE id=1")
            facts = seed_queue_refresh_case(db, queued_count=32, frozen_active=False)
            db.execute_native(
                "DELETE FROM background_tasks WHERE id=%s", (facts.task_id,)
            )
            observed = datetime.combine(
                date.fromisoformat(facts.queue_date), time(12), tzinfo=UTC
            )
            now = observed.isoformat()
            db.execute_native(
                "INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,payload_json,next_attempt_at,created_at,updated_at) VALUES('unknown-day','daily_queue','current',7,10,'queued','admit_due',%s,%s,%s,%s)",
                (payload_json, now, now, now),
            )
            protected_before = protected_hash(db.raw, facts.issued_ids)
            protected_rows_before = [
                dict(row)
                for row in db.execute_native(
                    "SELECT q.id, to_jsonb(q) AS queue, to_jsonb(o) AS origin FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id WHERE q.id=ANY(%s) ORDER BY q.id,o.card_id,o.revision",
                    (list(facts.issued_ids),),
                ).fetchall()
            ]

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return observed if tz else observed.replace(tzinfo=None)

        monkeypatch.setattr(refresh, "datetime", FixedClock)
        monkeypatch.setattr(durable_tasks, "_now", lambda: observed)
        old = durable_tasks.claim_task("daily_queue")
        assert old["id"] == "unknown-day" and old["generation"] == 7
        with monkeypatch.context() as patch:

            def interrupt(*args, **kwargs):
                raise RuntimeError("synthetic hold interruption")

            patch.setattr(durable_tasks, "_record_event", interrupt)
            with pytest.raises(RuntimeError, match="synthetic hold interruption"):
                refresh.execute_postgres_queue_refresh_slice(old)
        with missing_day_postgres() as db:
            interrupted = db.execute_native(
                "SELECT state,generation,lease_token,payload_json,last_error FROM background_tasks WHERE id='unknown-day'"
            ).fetchone()
            assert interrupted["state"] == "leased" and interrupted["generation"] == 7
            assert (
                interrupted["lease_token"] == old["lease_token"]
                and interrupted["payload_json"] == payload_json
            )
            assert interrupted["last_error"] is None
        assert not refresh.execute_postgres_queue_refresh_slice(old)
        assert not refresh.execute_postgres_queue_refresh_slice(old), (
            "stale lease replay must not alter the hold"
        )
        durable_tasks.requeue_interrupted_tasks()
        with missing_day_postgres() as db:
            held = dict(
                db.execute_native(
                    "SELECT * FROM background_tasks WHERE id='unknown-day'"
                ).fetchone()
            )
            assert held["generation"] == 7 and held["state"] == "retrying"
            assert held["payload_json"] == payload_json and held["phase"] == "admit_due"
            assert held["last_error"] == "accepted_queue_day_unknown"
            assert held["next_attempt_at"] == durable_tasks._UNKNOWN_QUEUE_DAY_UNTIL
            assert held["lease_token"] is None
            db.execute_native(
                "UPDATE settings SET timezone='America/New_York' WHERE id=1"
            )
            today = facts.queue_date
            first = queue_application.request_queue_refresh_in_transaction(db, today)
            second = queue_application.request_queue_refresh_in_transaction(db, today)
            assert first["id"] == second["id"] != old["id"]
        for _ in range(300):
            with missing_day_postgres() as db:
                row = db.execute_native(
                    "SELECT state,refresh_pending FROM queue_projections WHERE queue_date=%s",
                    (today,),
                ).fetchone()
                if row and row["state"] == "ready" and not row["refresh_pending"]:
                    break
            task = durable_tasks.claim_task("daily_queue")
            assert task and task["id"] == first["id"]
            refresh.execute_postgres_queue_refresh_slice(task)
        else:
            raise AssertionError("known current-day queue did not progress")
        with missing_day_postgres() as db:
            after = dict(
                db.execute_native(
                    "SELECT * FROM background_tasks WHERE id='unknown-day'"
                ).fetchone()
            )
            for key in (
                "id",
                "generation",
                "state",
                "phase",
                "payload_json",
                "last_error",
                "next_attempt_at",
            ):
                assert after[key] == held[key]
            protected_rows_after = [
                dict(row)
                for row in db.execute_native(
                    "SELECT q.id, to_jsonb(q) AS queue, to_jsonb(o) AS origin FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id WHERE q.id=ANY(%s) ORDER BY q.id,o.card_id,o.revision",
                    (list(facts.issued_ids),),
                ).fetchall()
            ]
            assert protected_rows_after == protected_rows_before
            assert protected_hash(db.raw, facts.issued_ids) == protected_before
            card = db.execute_native(
                "SELECT c.start_fen,c.moves_json FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=%s AND q.status='queued' ORDER BY q.position LIMIT 1",
                (today,),
            ).fetchone()
            assert card and chess.Board(card["start_fen"]).parse_uci(
                json.loads(card["moves_json"])[0]
            )
    finally:
        postgres.close_pools()
