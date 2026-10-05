"""Exercise real task delivery and durable retry state on disposable PostgreSQL."""

from __future__ import annotations

from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import os
import json
import threading
import time
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json

from celery.exceptions import TimeoutError as CeleryTimeout
from fastapi import HTTPException
from psycopg.errors import TransactionTimeout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import command_dispatch, postgres_store, tasks
from app.command_gateway import (
    CommandConflict, MAX_BACKGROUND_CYCLE_ATTEMPTS, claim_recoverable_operation,
    execute_command, read_operation, record_operation_attempt, register_command, request_digest,
)


def verify_pgn_discard_fencing() -> None:
    from app.pgn_import_commands import discard_pgn_import, prepare_import_payload
    from app.services.pgn import parse_pgn
    owned_ids = []
    source_name = f"discard-proof-{uuid.uuid4().hex}.pgn"
    games_found, lines = parse_pgn('1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 *')
    payload = prepare_import_payload(source_name, "white", 2, games_found, lines)
    try:
        for previous_state in ("unknown", "queued", "executing", "retrying", "pending", "blocked", "failed"):
            target_id = f"discard-proof-{uuid.uuid4().hex}"
            owned_ids.append(target_id)
            with postgres_store.connection(read_only=False) as database:
                if previous_state != "unknown":
                    database.raw.execute(
                        "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,attempt_token) VALUES(%s,'imports.pgn.admit',%s,%s,%s,'old-token')",
                        (target_id, request_digest("imports.pgn.admit", payload), previous_state, json.dumps(payload)),
                    )
                assert discard_pgn_import(database, {"operation_id": target_id})["outcome"] == "discarded"
            try:
                claimed, _, _, _ = record_operation_attempt(target_id, "imports.pgn.admit", payload, background=False)
                assert not claimed
                execute_command(target_id, "imports.pgn.admit", payload, attempt_token="old-token")
            except (CommandConflict, RuntimeError):
                pass
            else:
                raise AssertionError("Discarded admission was accepted")
            with postgres_store.connection(read_only=True) as database:
                assert tuple(database.raw.execute("SELECT state,payload_json,attempt_token FROM operation_receipts WHERE operation_id=%s", (target_id,)).fetchone()) == ("failed", None, None)
                assert read_operation(target_id)["error"]["code"] == "import_discarded"
                assert database.raw.execute("SELECT COUNT(*) FROM repertoires WHERE source_name=%s", (source_name,)).fetchone()[0] == 0

        race_id = f"discard-race-{uuid.uuid4().hex}"
        owned_ids.append(race_id)
        admission_locked = threading.Event()
        release_admission = threading.Event()
        application_name = f"discard-{uuid.uuid4().hex}"
        result = {"repertoire_id": "committed-proof"}

        def complete_admission():
            with postgres_store.connection(read_only=False) as database:
                database.raw.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (race_id,))
                database.raw.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state) VALUES(%s,'imports.pgn.admit','race','pending')", (race_id,))
                admission_locked.set()
                assert release_admission.wait(10), "Admission race was not released"
                database.raw.execute("UPDATE operation_receipts SET state='complete',response_json=%s WHERE operation_id=%s", (json.dumps(result), race_id))

        def discard_during_admission():
            with postgres_store.connection(read_only=False) as database:
                database.raw.execute("SELECT set_config('application_name',%s,true)", (application_name,))
                return discard_pgn_import(database, {"operation_id": race_id})

        with ThreadPoolExecutor(max_workers=2) as executor:
            admission = executor.submit(complete_admission)
            assert admission_locked.wait(10)
            discard = executor.submit(discard_during_admission)
            try:
                deadline = time.monotonic() + 5
                while True:
                    with postgres_store.connection(read_only=True) as database:
                        waiting = database.raw.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'", (application_name,)).fetchone()[0]
                    if waiting:
                        break
                    assert time.monotonic() < deadline, "Discard never reached admission's lock"
                    time.sleep(0.01)
            finally:
                release_admission.set()
            admission.result()
            assert discard.result() == {"operation_id": race_id, "outcome": "already_complete", "import_result": result}
    finally:
        with postgres_store.connection(read_only=False) as database:
            database.raw.execute("DELETE FROM operation_receipts WHERE operation_id=ANY(%s::text[])", (owned_ids,))
    print("PASS discarded PGN delivery cannot restore its payload or create repertoire data; admission race preserves completion")

def test_postgres_command_receipt_preserves_http_detail_and_failed_handler_rollback() -> None:
    def reject_after_write(database, payload):
        database.raw.execute(
            "INSERT INTO internal_migrations(name,applied_at) VALUES(%s,NOW()::TEXT)",
            (payload["marker"],),
        )
        raise HTTPException(409, payload["detail"])

    command_name = "test.recovery.http_failure"
    register_command(command_name, reject_after_write)
    for code in ("opening_evidence_conflict", "opening_evidence_unavailable"):
        for deferred in (False, True):
            operation_id = f"http-detail-{uuid.uuid4().hex}"
            detail = {"code": code, "message": "example conflict", "aggregate_review_allowed": True}
            payload = {"marker": operation_id, "detail": detail}

            def finish_worker():
                claimed, saved_payload, attempt_token, _ = record_operation_attempt(
                    operation_id, command_name, payload, background=False,
                )
                if claimed:
                    execute_command(operation_id, command_name, saved_payload, attempt_token=attempt_token)

            def get_task_result(**options):
                assert options["propagate"] is False
                if deferred:
                    raise CeleryTimeout()
                finish_worker()

            with patch.object(command_dispatch.celery_app, "send_task",
                              return_value=SimpleNamespace(get=get_task_result)):
                if deferred:
                    pending = command_dispatch.dispatch_command(
                        command_name, payload, idempotency_key=operation_id,
                    )
                    assert pending.status_code == 202
                    assert json.loads(pending.body)["operation_id"] == operation_id
                    finish_worker()
                else:
                    try:
                        command_dispatch.dispatch_command(command_name, payload, idempotency_key=operation_id)
                    except HTTPException as failure:
                        assert failure.status_code == 409 and failure.detail == detail
                    else:
                        raise AssertionError("Immediate failed receipt did not reconstruct its HTTP error")
                receipt = read_operation(operation_id)
                assert receipt["state"] == "failed"
                assert receipt["error"] == {
                    "status_code": 409, "message": str(HTTPException(409, detail)), "detail": detail,
                }
                # Redelivery must reconstruct the persisted error without another
                # handler execution or changing its immutable failure receipt.
                deferred = False
                try:
                    command_dispatch.dispatch_command(command_name, payload, idempotency_key=operation_id)
                except HTTPException as failure:
                    assert failure.status_code == 409 and failure.detail == detail
                else:
                    raise AssertionError("Failed receipt replay lost HTTP detail")
                assert read_operation(operation_id) == receipt
            with postgres_store.connection(read_only=True) as database:
                error_json = database.raw.execute(
                    "SELECT error_json FROM operation_receipts WHERE operation_id=%s", (operation_id,),
                ).fetchone()[0]
                assert json.loads(error_json) == receipt["error"]
                assert database.raw.execute(
                    "SELECT COUNT(*) FROM internal_migrations WHERE name=%s", (operation_id,),
                ).fetchone()[0] == 0
    print("PASS test_postgres_command_receipt_preserves_http_detail_and_failed_handler_rollback")


def main() -> None:
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("This check requires the disposable PostgreSQL test instance")
    os.environ["TEMPO_DATABASE_WRITE_URL"] = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_DATABASE_READ_URL"] = os.environ["TEMPO_DATABASE_WRITE_URL"]
    tasks.activity_gate.background_job = lambda *_arguments: nullcontext()

    attempts: list[str] = []
    should_fail = True

    def handler(database, payload):
        attempts.append(payload["value"])
        if should_fail:
            raise TransactionTimeout("injected retryable failure")
        database.raw.execute(
            "INSERT INTO internal_migrations(name,applied_at) VALUES(%s,NOW()::TEXT) "
            "ON CONFLICT(name) DO NOTHING", (payload["value"],),
        )
        return payload

    register_command("test.recovery.state", handler)
    operation_id = f"recovery-{uuid.uuid4().hex}"
    payload = {"value": operation_id}

    for attempt_number in range(1, MAX_BACKGROUND_CYCLE_ATTEMPTS + 1):
        if attempt_number > 1:
            with postgres_store.connection(read_only=False) as database:
                database.raw.execute(
                    "UPDATE operation_receipts SET next_retry_at=NOW()-INTERVAL '1 second' "
                    "WHERE operation_id=%s", (operation_id,),
                )
            recovered = claim_recoverable_operation()
            assert recovered and recovered["operation_id"] == operation_id
            postgres_store.close_pools()
        assert tasks.execute_background_command.run(
            operation_id, "test.recovery.state", payload,
        ) is None
        receipt = read_operation(operation_id)
        assert receipt["attempt_count"] == attempt_number
        assert receipt["cycle_attempt_count"] == attempt_number
        assert receipt["state"] == (
            "blocked" if attempt_number == MAX_BACKGROUND_CYCLE_ATTEMPTS else "retrying"
        )
        tasks.execute_background_command.run(operation_id, "test.recovery.state", payload)
        assert read_operation(operation_id)["attempt_count"] == attempt_number

    assert len(attempts) == MAX_BACKGROUND_CYCLE_ATTEMPTS
    original = read_operation(operation_id)
    try:
        tasks.execute_background_command.run(
            operation_id, "test.recovery.state", {"value": "conflict"},
        )
    except CommandConflict:
        pass
    else:
        raise AssertionError("Conflicting delivery was accepted")
    assert read_operation(operation_id) == original

    should_fail = False
    retry_cycle = original["retry_cycle"]
    tasks.execute_background_command.run(
        operation_id, "test.recovery.state", payload, retry_cycle,
    )
    completed = read_operation(operation_id)
    assert completed["state"] == "complete" and completed["retry_cycle"] == retry_cycle + 1
    assert completed["attempt_count"] == MAX_BACKGROUND_CYCLE_ATTEMPTS + 1
    tasks.execute_background_command.run(operation_id, "test.recovery.state", payload, retry_cycle)
    tasks.execute_background_command.run(operation_id, "test.recovery.state", payload)
    assert len(attempts) == MAX_BACKGROUND_CYCLE_ATTEMPTS + 1
    with postgres_store.connection(read_only=True) as database:
        business_rows = database.raw.execute(
            "SELECT COUNT(*) FROM internal_migrations WHERE name=%s", (operation_id,),
        ).fetchone()[0]
    assert business_rows == 1
    assert read_operation(operation_id) == completed

    stale_id = f"stale-{uuid.uuid4().hex}"
    claimed, saved_payload, old_token, _ = record_operation_attempt(
        stale_id, "test.recovery.state", {"value": stale_id}, background=True,
    )
    assert claimed and saved_payload == {"value": stale_id}
    with postgres_store.connection(read_only=False) as database:
        database.raw.execute(
            "UPDATE operation_receipts SET lease_expires_at=NOW()-INTERVAL '1 second' "
            "WHERE operation_id=%s", (stale_id,),
        )
    recovered = claim_recoverable_operation()
    assert recovered and recovered["operation_id"] == stale_id
    claimed, _, new_token, _ = record_operation_attempt(
        stale_id, "test.recovery.state", {"value": stale_id}, background=True,
    )
    assert claimed and old_token != new_token
    assert execute_command(stale_id, "test.recovery.state", {"value": stale_id},
                           background=True, attempt_token=old_token) is None
    assert read_operation(stale_id)["state"] == "executing"
    assert execute_command(stale_id, "test.recovery.state", {"value": stale_id},
                           background=True, attempt_token=new_token) == {"value": stale_id}
    assert read_operation(stale_id)["state"] == "complete"

    # A wrong payload cannot poison queued, executing, retrying, or blocked originals.
    for state in ("queued", "executing", "retrying", "blocked"):
        state_id = f"conflict-{state}-{uuid.uuid4().hex}"
        with postgres_store.connection(read_only=False) as database:
            database.raw.execute(
                "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,"
                "payload_json,background) VALUES(%s,'test.recovery.state',%s,%s,%s,TRUE)",
                (state_id, request_digest("test.recovery.state", {"value": state_id}),
                 state, '{"value":"' + state_id + '"}'),
            )
        before = read_operation(state_id)
        try:
            tasks.execute_background_command.run(
                state_id, "test.recovery.state", {"value": "changed"},
            )
        except CommandConflict:
            pass
        else:
            raise AssertionError(f"Conflict in {state} state was accepted")
        assert read_operation(state_id) == before
        if state == "queued":
            tasks.execute_background_command.run(
                state_id, "test.recovery.state", {"value": state_id},
            )
            assert read_operation(state_id)["state"] == "complete"

    concurrent_id = f"concurrent-{uuid.uuid4().hex}"
    with postgres_store.connection(read_only=False) as database:
        database.raw.execute(
            "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,"
            "payload_json,background) VALUES(%s,'test.recovery.state',%s,'queued',%s,TRUE)",
            (concurrent_id, request_digest("test.recovery.state", {"value": concurrent_id}),
             '{"value":"' + concurrent_id + '"}'),
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        valid = executor.submit(tasks.execute_background_command.run,
                                concurrent_id, "test.recovery.state", {"value": concurrent_id})
        conflicting = executor.submit(tasks.execute_background_command.run,
                                      concurrent_id, "test.recovery.state", {"value": "changed"})
        assert valid.result() == {"value": concurrent_id}
        try:
            conflicting.result()
        except CommandConflict:
            pass
        else:
            raise AssertionError("Concurrent conflicting task was accepted")
    assert read_operation(concurrent_id)["state"] == "complete"
    with postgres_store.connection(read_only=True) as database:
        assert database.raw.execute(
            "SELECT COUNT(*) FROM internal_migrations WHERE name=%s", (concurrent_id,),
        ).fetchone()[0] == 1

    legacy_id = f"legacy-{uuid.uuid4().hex}"
    with postgres_store.connection(read_only=False) as database:
        database.raw.execute(
            "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state) "
            "VALUES(%s,'test.recovery.state','unrecoverable','pending')", (legacy_id,),
        )
    assert "matching journal or outbox" in read_operation(legacy_id)["message"]

    verify_pgn_discard_fencing()

    test_postgres_command_receipt_preserves_http_detail_and_failed_handler_rollback()
    postgres_store.close_pools()
    print("PASS finite retries, restart, conflict states and race, explicit cycle, stale lease, and one business effect")


if __name__ == "__main__":
    main()
