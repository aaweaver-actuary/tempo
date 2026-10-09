"""Regular durability: bounded monitor, foreground safety and persistent episodes."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
import uuid
import concurrent.futures
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import postgres_store
from app.services import activity_health, background_diagnostics
from app.services.durable_tasks import (
    enqueue_task,
    claim_task,
    advance_task_slice_in_transaction,
)
from app.services.activity_gate import activity_gate


def proof_activity_health(database_url):
    import check_postgres_graph_retention as fixtures

    started = time.monotonic()
    with (
        patch.object(fixtures, "DATABASE_URL", database_url),
        fixtures.owned_fixture_database(),
    ):
        url = os.environ["TEMPO_DATABASE_WRITE_URL"]
        now = datetime.now(timezone.utc)
        task = enqueue_task("daily_queue", "health-proof", {})
        claim = claim_task("daily_queue")
        execution = activity_health.begin_execution(claim, now=now)
        for index in range(5):
            activity_health.record_execution_error(
                claim, "transaction_timeout", execution_id=str(index)
            )
        with psycopg.connect(url) as db:
            db.execute(
                "UPDATE activity_pipeline_health SET bootstrap_ready=1,checked_at=%s WHERE kind!='daily_queue'",
                ((now + timedelta(hours=1)).isoformat(),),
            )
        with activity_gate.foreground():
            section = time.monotonic()
            assert activity_health.monitor_one_pipeline(
                now=now,
                evidence={
                    "available": True,
                    "foreground": True,
                    "idle_sampler": lambda _a, _b: {},
                },
            )
            assert time.monotonic() - section < 0.250
            with psycopg.connect(url) as db:
                assert (
                    db.execute(
                        "SELECT count(*) FROM activity_notification_incidents"
                    ).fetchone()[0]
                    == 0
                )
                foreground_started = time.monotonic()
                assert db.execute("SELECT 1").fetchone()[0] == 1
                foreground_seconds = time.monotonic() - foreground_started
        assert activity_health.monitor_one_pipeline(
            now=now + timedelta(minutes=1),
            evidence={
                "available": True,
                "foreground": False,
                "idle_sampler": lambda _a, _b: {},
            },
        )
        assert len(activity_health.notification_changes()["items"]) == 1
        postgres_store.close_pools()
        assert activity_health.monitor_one_pipeline(
            now=now + timedelta(minutes=2),
            evidence={
                "available": True,
                "foreground": False,
                "idle_sampler": lambda _a, _b: {},
            },
        )
        assert len(activity_health.notification_changes()["items"]) == 1
        with postgres_store.connection() as db:
            assert advance_task_slice_in_transaction(
                db, claim, next_phase="unlock", next_payload={"cursor": "accepted"}
            )
        activity_health.finish_execution(
            claim, execution, now=datetime.now(timezone.utc)
        )
        assert activity_health.monitor_one_pipeline(
            now=now + timedelta(minutes=3),
            evidence={
                "available": True,
                "foreground": False,
                "idle_sampler": lambda _a, _b: {},
            },
        )
        changes = activity_health.notification_changes()
        assert [change["event"] for change in changes["items"]] == [
            "opened",
            "resolved",
        ]
        assert (
            activity_health.notification_changes(after=1, limit=1)["items"][0]["event"]
            == "resolved"
        )
        # Real foreground grading and a lock-contended monitor use independent
        # connections. The monitor's 25ms lock budget must not strand reviews.
        from app import review_commands
        from app.command_gateway import execute_command
        from app.services.background_activity import list_activity

        foreground_card = "health-foreground-card"
        fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        with postgres_store.connection() as db:
            db.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('health-foreground-repertoire','Health fixture','synthetic',?)",
                (now.isoformat(),),
            )
            db.execute(
                "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES(?,'health-foreground-repertoire','prefix',?,'[]',?,'endgame')",
                (foreground_card, fen, now.date().isoformat()),
            )
            queue_id = db.execute(
                "INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES(?,?,0,'queued') RETURNING id",
                (now.date().isoformat(), foreground_card),
            ).fetchone()[0]
        payload = {
            "card_id": foreground_card,
            "review": {
                "outcome": "correct",
                "queue_entry_id": queue_id,
                "attempt_id": "health-foreground-attempt",
            },
        }
        # Register/import before measuring execution so module load is excluded.
        import app.main

        with (
            psycopg.connect(url) as blocker,
            concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor,
        ):
            blocker.execute(
                "SELECT work_id FROM activity_work_progress WHERE source='durable' AND work_id=%s FOR UPDATE",
                (task["id"],),
            )
            future = executor.submit(
                activity_health.monitor_one_pipeline,
                now=now + timedelta(minutes=4),
                evidence={"available": True, "foreground": False},
            )
            review_started = time.monotonic()
            with activity_gate.foreground():
                result = execute_command(
                    "health-foreground-review", "cards.review", payload
                )
                foreground_review_seconds = time.monotonic() - review_started
                assert foreground_review_seconds < 0.250, foreground_review_seconds
            try:
                future.result(timeout=1)
            except psycopg.errors.LockNotAvailable:
                pass
            else:
                raise AssertionError("The controlled row lock must fail fast")
            blocker.rollback()
        assert (
            execute_command("health-foreground-review", "cards.review", payload)
            == result
        )
        with postgres_store.connection(read_only=True) as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM reviews WHERE card_id=?", (foreground_card,)
                ).fetchone()[0]
                == 1
            )
        assert list_activity(work_source="durable", work_id=task["id"])["total"] == 1

        with postgres_store.connection(read_only=True) as db:
            queued_before_fixture = db.execute(
                "SELECT COUNT(*) FROM background_tasks WHERE state='queued'"
            ).fetchone()[0]
        # Real native engine restart retains the expired admitted interval; its
        # replacement and heartbeat are neither progress nor duplicate execution.
        with psycopg.connect(url) as db:
            db.execute(
                "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('health-engine-game','lichess','test',%s,'rapid',1,'white','win',%s,'[]')",
                (now.isoformat(), fen),
            )
            db.execute(
                "INSERT INTO game_analysis_jobs(game_id,status,analysis_version,updated_at) VALUES('health-engine-game','queued',1,%s)",
                (now.isoformat(),),
            )
            db.execute(
                "UPDATE game_analysis_jobs SET status='leased',lease_id='first',lease_expires_at=%s,updated_at=%s WHERE game_id='health-engine-game'",
                ((now + timedelta(seconds=60)).isoformat(), now.isoformat()),
            )
            db.execute(
                "UPDATE game_analysis_jobs SET lease_id='replacement',lease_expires_at=%s,updated_at=%s WHERE game_id='health-engine-game'",
                (
                    (now + timedelta(seconds=150)).isoformat(),
                    (now + timedelta(seconds=90)).isoformat(),
                ),
            )
            db.execute(
                "UPDATE game_analysis_jobs SET updated_at=%s WHERE game_id='health-engine-game'",
                ((now + timedelta(seconds=100)).isoformat(),),
            )
            assert db.execute(
                "SELECT admitted_seconds,execution_id,last_progress_at FROM activity_work_progress WHERE work_id='health-engine-game'"
            ).fetchone() == (60.0, "replacement", None)
        # Populate counts without requiring a raw queue scan in the read path.
        with psycopg.connect(url) as db:
            db.execute(
                "INSERT INTO background_tasks(id,kind,deduplication_key,payload_json,next_attempt_at,created_at,updated_at) SELECT 'health-game-'||n,'game_derivation_positions','health-game-'||n,'{}',%s,%s,%s FROM generate_series(1,2143) n",
                (now.isoformat(), now.isoformat(), now.isoformat()),
            )
            # Every cached counter is produced through the same bounded kind reducer.
            db.execute(
                "UPDATE activity_pipeline_health SET bootstrap_ready=1,checked_at=NULL"
            )
        actual_now = datetime.now(timezone.utc)
        for _ in range(35):
            assert activity_health.monitor_one_pipeline(
                now=actual_now, evidence={"available": False, "foreground": False}
            )
        snapshot = background_diagnostics.snapshot()
        assert snapshot.available, snapshot
        assert snapshot.query_duration_seconds < 0.100, snapshot
        assert (
            sum(
                row.count
                for row in snapshot.queues
                if row.queue == "durable" and row.state == "queued"
            )
            == 2143 + queued_before_fixture
        )
        with psycopg.connect(url) as db:
            assert (
                db.execute(
                    "SELECT health FROM activity_pipeline_health WHERE kind='game_derivation_positions'"
                ).fetchone()[0]
                == "unknown"
            )
            db.execute(
                "UPDATE activity_pipeline_health SET diagnostics_at='2000-01-01T00:00:00+00:00'"
            )
        stale = background_diagnostics.snapshot()
        assert not stale.available and stale.unavailable_reason == "cache_stale"
        print(
            {
                "test": "test_postgres_activity_monitor_control_foreground_restart_dedup_recovery_and_cached_read_budget",
                "read_ms": round(snapshot.query_duration_seconds * 1000, 3),
                "foreground_ms": round(foreground_seconds * 1000, 3),
                "foreground_review_ms": round(foreground_review_seconds * 1000, 3),
                "duration_seconds": round(time.monotonic() - started, 2),
            }
        )


def browser_fixture(action, identity):
    import check_postgres_graph_retention as fixtures

    if not identity.startswith("activity-health-proof-") or str(
        uuid.UUID(identity.removeprefix("activity-health-proof-"))
    ) != identity.removeprefix("activity-health-proof-"):
        raise ValueError("A browser-owned health fixture identity is required")
    fixtures.validate_disposable_database()
    now = datetime.now(timezone.utc)
    with (
        patch.dict(
            os.environ,
            {
                "TEMPO_DATABASE_WRITE_URL": fixtures.DATABASE_URL,
                "TEMPO_DATABASE_READ_URL": fixtures.DATABASE_URL,
            },
        ),
        postgres_store.connection() as db,
    ):
        if action == "seed":
            db.execute(
                "INSERT INTO background_tasks(id,kind,deduplication_key,state,phase,lease_token,lease_expires_at,next_attempt_at,created_at,updated_at) VALUES(?,'daily_queue',?,'leased','fixture',?,'2050-01-01','2050-01-01',?,?)",
                (identity, identity, identity, now.isoformat(), now.isoformat()),
            )
            work = dict(
                db.execute(
                    "SELECT * FROM activity_work_progress WHERE source='durable' AND work_id=?",
                    (identity,),
                ).fetchone()
            )
            # A synthetic unresolved episode exercises the real PostgreSQL read/
            # browser boundary. Monitor thresholds/claims have separate native proof.
            activity_health.reconcile_incident(db, work, "repeated_timeout", now)
        elif action == "recover":
            from app.services.durable_tasks import complete_task_slice_in_transaction

            task = dict(
                db.execute(
                    "SELECT * FROM background_tasks WHERE id=?", (identity,)
                ).fetchone()
            )
            assert complete_task_slice_in_transaction(db, task)
            work = dict(
                db.execute(
                    "SELECT * FROM activity_work_progress WHERE source='durable' AND work_id=?",
                    (identity,),
                ).fetchone()
            )
            activity_health.reconcile_incident(db, work, None, now)
        elif action == "cleanup":
            db.execute(
                "DELETE FROM activity_notification_changes WHERE incident_id IN (SELECT id FROM activity_notification_incidents WHERE work_id=?)",
                (identity,),
            )
            db.execute(
                "DELETE FROM activity_notification_incidents WHERE work_id=?",
                (identity,),
            )
            db.execute("DELETE FROM background_tasks WHERE id=?", (identity,))
        else:
            raise ValueError("Unknown browser health fixture action")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        proof_activity_health(os.environ["TEMPO_ACTIVITY_PROOF_URL"])
    elif len(sys.argv) == 3:
        browser_fixture(*sys.argv[1:])
    else:
        raise ValueError("Expected owned browser action and identity")
