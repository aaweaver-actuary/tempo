"""Authoritative identity under first insert, rollback and generation replacement."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
import psycopg, time
from tempo_learning_runtime.database import connection
from tempo_learning_runtime.durable_tasks import enqueue_task_in_transaction


def test_waiting_first_enqueue_uses_survivor_after_creator_rolls_back(case):
    created = Event()
    waiting = Event()
    release = Event()
    abandoned = []
    waiter_pid = []

    def creator():
        with case.database.scope():
            with pytest.raises(ValueError):
                with connection() as db:
                    task = enqueue_task_in_transaction(
                        db, "daily_queue", "rollback-race", {"queue_date": "2026-10-15"}
                    )
                    abandoned.append(task["id"])
                    created.set()
                    assert waiting.wait(3)
                    assert release.wait(3)
                    raise ValueError("creator rolled back")

    def survivor():
        assert created.wait(3)
        with case.database.scope(), connection() as db:
            db.execute_native("SET LOCAL lock_timeout='1500ms'")
            waiter_pid.append(
                db.execute_native("SELECT pg_backend_pid()").fetchone()[0]
            )
            waiting.set()
            return enqueue_task_in_transaction(
                db, "daily_queue", "rollback-race", {"queue_date": "2026-10-15"}
            )

    with ThreadPoolExecutor(2) as executor:
        first = executor.submit(creator)
        second = executor.submit(survivor)
        try:
            assert waiting.wait(3)
            with psycopg.connect(case.admin) as inspector:
                for _ in range(80):
                    inspector.execute("SELECT pg_stat_clear_snapshot()")
                    row = inspector.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                        (waiter_pid[0],),
                    ).fetchone()
                    if row and row[0] == "Lock":
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError(
                        "Competing insertion never waited on the speculative creator"
                    )
        finally:
            release.set()
        first.result()
        saved = second.result()
    assert saved["id"] != abandoned[0] and saved["generation"] == 1
    assert [
        tuple(row)
        for row in case.query(
            "SELECT task_id,generation FROM learning.background_task_events"
        )
    ] == [(saved["id"], 1)]


def test_existing_task_generation_replacement_keeps_database_identity_and_events(case):
    with case.database.scope():
        with connection() as db:
            first = enqueue_task_in_transaction(
                db, "daily_queue", "replace", {"queue_date": "2026-10-15"}
            )
        with connection() as db:
            db.execute_native(
                "UPDATE background_tasks SET state='leased',lease_token='old',lease_expires_at=NOW()+INTERVAL '1 hour' WHERE id=%s",
                (first["id"],),
            )
            newer = enqueue_task_in_transaction(
                db,
                "daily_queue",
                "replace",
                {"queue_date": "2026-10-16"},
                minimum_generation=20,
            )
        with connection() as db:
            last = enqueue_task_in_transaction(
                db, "daily_queue", "replace", {"queue_date": "2026-10-17"}
            )
    assert first["id"] == newer["id"] == last["id"]
    assert [first["generation"], newer["generation"], last["generation"]] == [1, 21, 22]
    assert newer["lease_token"] is None and newer["state"] == "queued"
    assert [
        tuple(row)
        for row in case.query(
            "SELECT task_id,generation FROM learning.background_task_events ORDER BY id"
        )
    ] == [(first["id"], generation) for generation in (1, 21, 22)]
