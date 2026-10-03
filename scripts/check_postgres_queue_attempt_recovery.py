"""Real foreground/maintenance interleaving and poisoned-receipt recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import postgres_store, review_commands  # registers the production review handlers
from app.command_gateway import execute_command, read_operation, request_digest
from app.services.postgres_queue_refresh import _reconcile_one_unseen_entry
from check_postgres_repertoire_limits import snapshot_queue_environment, restore_queue_environment


def test_postgres_queue_attempt_maintenance_contention_restart_and_receipt_recovery():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Queue-attempt proof requires a disposable PostgreSQL instance")
    dsn = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_DATABASE_WRITE_URL"] = dsn
    os.environ["TEMPO_DATABASE_READ_URL"] = dsn
    postgres_store.close_pools()
    identifier = f"queue-origin-proof-{uuid.uuid4()}"
    card_ids = [f"{identifier}-{suffix}" for suffix in ("stale", "independent", "changed", "poisoned")]
    today = date.today().isoformat()
    operations = []
    snapshot = None
    queue_positions = []
    executor = ThreadPoolExecutor(max_workers=1)
    waiting_review = None
    try:
        with postgres_store.connection() as database:
            snapshot = snapshot_queue_environment(database, (today, today))
            queue_positions = [tuple(row) for row in database.execute("SELECT id,position FROM daily_queue WHERE queue_date=?", (today,))]
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(?,?,'synthetic',?,0)", (identifier, "Queue recovery proof", today))
            entries = []
            for index, card_id in enumerate(card_ids):
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at) VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,?)",
                                 (card_id, identifier, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", today, today))
                database.execute("INSERT INTO repertoire_cards VALUES(?,?)", (identifier, card_id))
                entries.append(database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(?,?,?,'new',?) RETURNING id", (today, card_id, -100 + index, identifier)).fetchone()[0])
        payloads = [{"card_id": card_id, "review": {"outcome": "correct", "guided": False,
                    "queue_entry_id": entry_id, "attempt_id": f"{identifier}-attempt-{index}",
                    "expected_revision": 1, "recorded_at": datetime.now(timezone.utc).isoformat()}}
                    for index, (card_id, entry_id) in enumerate(zip(card_ids, entries))]
        # Hold only this fixture's maintenance transaction. Its actual bounded
        # production reconciliation deletes the projection before review resumes.
        maintenance = psycopg.connect(dsn, row_factory=postgres_store.tempo_row_factory)
        try:
            maintenance.execute("SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_ids[0],))
            operation = f"{identifier}-foreground"
            operations.append(operation)
            waiting_review = executor.submit(execute_command, operation, "cards.review", payloads[0])
            deadline = time.monotonic() + 5
            with psycopg.connect(dsn, autocommit=True) as observer:
                while not observer.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE 'SELECT id FROM cards%FOR UPDATE%')").fetchone()[0]:
                    if waiting_review.done():
                        raise AssertionError(f"Foreground review unexpectedly finished: {waiting_review.result()}")
                    if time.monotonic() >= deadline:
                        raise AssertionError("Foreground review did not reach the fixture card lock")
                assert observer.execute("SELECT COUNT(*) FROM queue_attempt_origins WHERE queue_entry_id=%s", (entries[0],)).fetchone()[0] == 1
            # Independent foreground reads and a review finish while stale A is
            # waiting; maintenance never owns a traversal or batch transaction.
            independent_operation = f"{identifier}-independent"
            operations.append(independent_operation)
            assert execute_command(independent_operation, "cards.review", payloads[1])["persisted"]
            adapter = postgres_store.PostgresConnection(maintenance)
            assert not _reconcile_one_unseen_entry(adapter, today,
                {"id": entries[0], "card_id": card_ids[0], "repertoire_id": identifier}, 0)
            maintenance.commit()
        finally:
            maintenance.close()
        saved = waiting_review.result(timeout=5)
        assert saved["persisted"] and saved["reconciliation"] == "queue_origin"
        postgres_store.close_pools()
        assert execute_command(operation, "cards.review", payloads[0]) == saved
        replay_operation = f"{identifier}-canonical-replay"
        operations.append(replay_operation)
        assert execute_command(replay_operation, "cards.review.reconcile", payloads[0]) == saved
        with postgres_store.connection() as database:
            assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card_ids[0],)).fetchone()[0] == 1
            assert database.execute("SELECT introduced_at FROM cards WHERE id=?", (card_ids[0],)).fetchone()[0] == today
            assert database.execute("SELECT admission_repertoire_id FROM queue_attempt_origins WHERE queue_entry_id=?", (entries[0],)).fetchone()[0] == identifier
            completed_origin = database.execute("SELECT last_status,review_result_json FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=?", (entries[0], card_ids[0])).fetchone()
            assert completed_origin["last_status"] == "complete"
            assert json.loads(completed_origin["review_result_json"])["review_id"] == saved["review_id"]
            database.execute("DELETE FROM daily_queue WHERE id=?", (entries[2],))
            database.execute("DELETE FROM daily_queue WHERE id=?", (entries[3],))
            database.execute("UPDATE cards SET moves_json='[\"d2d4\"]',revision=2 WHERE id=?", (card_ids[2],))
        failed_operation = f"{identifier}-classified-conflict"
        operations.append(failed_operation)
        assert execute_command(failed_operation, "cards.review", payloads[2]) is None
        receipt = read_operation(failed_operation)
        assert receipt["error"]["code"] == "card_revision_changed" and receipt["error"]["retryable"] is False
        assert receipt["error"]["status_code"] == 409 and isinstance(receipt["error"]["detail"], str)
        # Seed the historical failure shape, which predates structured conflict
        # codes. Recovery gets a different transport identity, never a new result.
        legacy_operation = f"{identifier}-historical-failure"
        operations.append(legacy_operation)
        with postgres_store.connection() as database:
            database.raw.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,error_json) VALUES(%s,'cards.review',%s,'failed',%s)",
                                 (legacy_operation, request_digest("cards.review", payloads[3]), json.dumps({"message": "409: This queue attempt is no longer available", "status_code": 409})))
        try:
            execute_command(legacy_operation, "cards.review", payloads[3])
        except RuntimeError as error:
            assert "409" in str(error)
        else:
            raise AssertionError("Historical failed save receipt must remain authoritative for its transport")
        recovery_operation = f"{identifier}-historical-recovery"
        operations.append(recovery_operation)
        recovered = execute_command(recovery_operation, "cards.review.reconcile", payloads[3])
        assert recovered["persisted"] and recovered["reconciliation"] == "queue_origin"
        assert execute_command(recovery_operation, "cards.review.reconcile", payloads[3]) == recovered
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card_ids[3],)).fetchone()[0] == 1
        conflict_operation = f"{identifier}-retained-conflict"
        operations.append(conflict_operation)
        conflict = execute_command(conflict_operation, "cards.review.reconcile", payloads[2])
        assert conflict["persisted"] is False and conflict["conflict"]["code"] == "card_revision_changed"
        postgres_store.close_pools()
        assert execute_command(conflict_operation, "cards.review.reconcile", payloads[2]) == conflict
        assert read_operation(recovery_operation)["state"] == "complete"
        print("PASS test_postgres_queue_attempt_maintenance_contention_restart_and_receipt_recovery")
    finally:
        executor.shutdown(wait=True)
        postgres_store.close_pools()
        with postgres_store.connection() as database:
            for operation_id in operations:
                database.execute("DELETE FROM operation_receipts WHERE operation_id=?", (operation_id,))
            for card_id in card_ids:
                database.execute("DELETE FROM review_schedule_snapshots WHERE review_id IN (SELECT id FROM reviews WHERE card_id=?)", (card_id,))
                database.execute("DELETE FROM review_attempt_receipts WHERE card_id=?", (card_id,))
            database.execute("DELETE FROM background_tasks WHERE deduplication_key=?", (identifier,))
            database.execute("DELETE FROM repertoires WHERE id=?", (identifier,))
            # Cascading queue deletion can fire the origin trigger: remove the
            # deliberately retained fixture evidence only after content teardown.
            for card_id in card_ids:
                database.execute("DELETE FROM queue_attempt_origins WHERE card_id=?", (card_id,))
            for queue_id, position in queue_positions:
                database.execute("UPDATE daily_queue SET position=? WHERE id=?", (position, queue_id))
            if snapshot is not None:
                restore_queue_environment(database, snapshot)
        postgres_store.close_pools()


if __name__ == "__main__":
    test_postgres_queue_attempt_maintenance_contention_restart_and_receipt_recovery()
