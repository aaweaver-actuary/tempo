"""Synthetic, invocation-owned fixture for prepared-card contention measurements."""
import json
import hashlib
import re
import sys
from datetime import date
from uuid import uuid4

from check_postgres_daily_study_dispatch import configure, seed, cleanup, DSN, FEN
import psycopg


def main():
    action, identifier = sys.argv[1:3]
    if not re.fullmatch(r"training-transition-[a-f0-9-]+", identifier):
        raise ValueError("An invocation-owned synthetic identifier is required")
    configure()
    if action == "resource-sample":
        with psycopg.connect(DSN) as connection:
            waits = connection.execute("SELECT wait_event_type,wait_event,count(*) FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid() GROUP BY wait_event_type,wait_event").fetchall()
            counters = connection.execute("SELECT deadlocks,conflicts,temp_bytes,blk_read_time,blk_write_time "
                "FROM pg_stat_database WHERE datname=current_database()").fetchone()
            pending = connection.execute("SELECT state,count(*),min(created_at)::text "
                "FROM operation_receipts GROUP BY state").fetchall()
        print(json.dumps({"postgres_waits": waits, "database_counters": counters, "command_admission": pending}))
        return
    if action == "proof":
        sys.argv = [sys.argv[0], "seed-small", identifier, date.today().isoformat()]
        main()
        try:
            prove_optional_handoff(identifier)
        finally:
            cleanup(identifier)
        return
    if action == "cleanup":
        cleanup(identifier)
        displaced_positions = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
        with psycopg.connect(DSN) as connection:
            restored = 0
            runtime_reordered = 0
            runtime_removed = 0
            for entry_id, original_position, displaced_position in displaced_positions:
                current = connection.execute("SELECT position FROM daily_queue WHERE id=%s FOR UPDATE", (entry_id,)).fetchone()
                if current is None:
                    runtime_removed += 1
                    continue
                if current[0] != displaced_position:
                    runtime_reordered += 1
                    continue
                # Do not overwrite a newer queue reorder during fixture cleanup.
                restored += connection.execute("UPDATE daily_queue SET position=%s WHERE id=%s AND position=%s",
                    (original_position, entry_id, displaced_position)).rowcount
            print(json.dumps({"restored_queue_positions": restored, "runtime_reordered_positions": runtime_reordered,
                "runtime_removed_entries": runtime_removed}))
        return
    if action in {"seed", "seed-small", "seed-with-existing-queue", "seed-progression", "seed-restoration"}:
        today = sys.argv[3]
        prepared_count = int(sys.argv[4]) if action == "seed-restoration" and len(sys.argv) > 4 else 64
        if not 4 <= prepared_count <= 10000:
            raise ValueError("Prepared fixture count must be between 4 and 10000")
        # Progression needs a small real queue, not the independent 15,000-card
        # backlog measurement. Its projection is settled below before issuance.
        seed(identifier, request_refresh=action in {"seed-small", "seed-progression"}, queue_date=today)
        with psycopg.connect(DSN) as connection:
            connection.execute("UPDATE repertoire_lines SET moves_json='[\"e2e4\",\"e7e5\",\"g1f3\"]' WHERE repertoire_id=%s", (identifier,))
            # Regular browser suites share only their invocation's disposable
            # database. Earlier tests may leave priority-promoted queue entries.
            # Regular coverage must reproduce a queue left by preceding cases.
            # Opt-in fresh-stack timings retain their original source snapshot.
            if action == "seed-with-existing-queue":
                connection.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
                    "VALUES(%s,%s,'Saved alternative','white',%s,'[\"d2d4\"]','2000-01-01')",
                    (identifier+'-alternative', identifier, FEN))
                connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(%s,%s,0,'due',%s)",
                    (today, identifier+'-due', identifier))
            existing_positions = connection.execute("SELECT id,position,card_id FROM daily_queue WHERE queue_date=%s ORDER BY position,id", (today,)).fetchall()
            displacement = prepared_count - min(0, min((row[1] for row in existing_positions), default=0))
            # Queue positions are nonnegative client ordinals. Shift preceding
            # entries together, preserving their relative order, and restore
            # unchanged surviving rows during cleanup after owned rows retire.
            displaced_positions = []
            for entry_id, position, card_id in existing_positions:
                connection.execute("UPDATE daily_queue SET position=%s WHERE id=%s", (position+displacement, entry_id))
                if not card_id.startswith(identifier):
                    displaced_positions.append([entry_id, position, position+displacement])
            for index in range(prepared_count):
                card_id = f"{identifier}-prepared-{index:03}"
                connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,trained_color,introduced_at) VALUES(%s,%s,'prefix',%s,'[\"e2e4\",\"e7e5\",\"g1f3\"]','learning','2000-01-01','white','2000-01-01')", (card_id, identifier, FEN))
                # Established cards isolate A→B progression from first-pass reinforcement.
                connection.execute("UPDATE cards SET first_correct_at='2000-01-01T00:00:00+00:00',interval_days=1 WHERE id=%s", (card_id,))
                if action == "seed-restoration":
                    connection.execute("UPDATE cards SET moves_json='[\"e2e4\"]' WHERE id=%s", (card_id,))
                if action == "seed-progression" and index == 0:
                    # Freeze the tactic presentation before the queue insertion
                    # binds its immutable origin and expected revision.
                    connection.execute("UPDATE cards SET kind='checkpoint',content_type='tactic',moves_json='[\"e2e4\"]' WHERE id=%s", (card_id,))
                connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)", (identifier, card_id))
                connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(%s,%s,%s,'due',%s)", (today, card_id, index, identifier))
            connection.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(%s,'ready',1,0) ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0", (today,))
            # Stable source snapshot across independently fresh PostgreSQL/Redis stacks.
            connection.execute("UPDATE repertoires SET created_at='2000-01-01T00:00:00+00:00' WHERE id=%s", (identifier,))
            connection.execute("UPDATE repertoire_lines SET created_at='2000-01-01T00:00:00+00:00' WHERE repertoire_id=%s", (identifier,))
            connection.execute("UPDATE background_tasks SET created_at='2000-01-01T00:00:00+00:00',updated_at='2000-01-01T00:00:00+00:00',next_attempt_at='2000-01-01T00:00:00+00:00' WHERE id LIKE %s", (identifier+'-analysis-%',))
            snapshot = {
                "cards": connection.execute("SELECT id,revision,state,due_date,first_correct_at,interval_days,moves_json FROM cards WHERE repertoire_id=%s ORDER BY id", (identifier,)).fetchall(),
                "queue": connection.execute("SELECT card_id,position,cycle,admission_kind FROM daily_queue WHERE card_id LIKE %s ORDER BY position", (identifier+'-prepared-%',)).fetchall(),
                "jobs": connection.execute("SELECT id,kind,priority,payload_json,next_attempt_at FROM background_tasks WHERE id LIKE %s ORDER BY id", (identifier+'-analysis-%',)).fetchall(),
            }
            print(json.dumps({"sha256": hashlib.sha256(json.dumps(snapshot, default=str, sort_keys=True).encode()).hexdigest(),
                "displaced_queue_positions": displaced_positions,
                "description": "Canonical ordered source rows; identical fixed IDs/date/scheduling/eligible jobs in each fresh invocation. Runtime attempt IDs, leases and clocks are deliberately excluded."}))
        return
    if action == "verify-progression":
        with psycopg.connect(DSN) as connection:
            first_card = identifier+'-prepared-000'
            scheduling = connection.execute("SELECT due_date,interval_days,repetitions,lapses,fsrs_card_json,stability,guided_review FROM cards WHERE id=%s", (first_card,)).fetchone()
            reviews = connection.execute("SELECT id,rating,previous_interval,next_interval FROM reviews WHERE card_id=%s ORDER BY id", (first_card,)).fetchall()
            receipts = connection.execute("SELECT attempt_id,queue_entry_id,review_id,scheduling_status,result_json FROM review_attempt_receipts WHERE card_id=%s ORDER BY attempt_id", (first_card,)).fetchall()
            completed = connection.execute("SELECT id,status FROM daily_queue WHERE card_id=%s ORDER BY id", (first_card,)).fetchall()
            review_count = connection.execute("SELECT count(*) FROM reviews WHERE card_id LIKE %s", (identifier+'-prepared-%',)).fetchone()[0]
            print(json.dumps({"scheduling": scheduling, "reviews": reviews, "receipts": receipts, "queue": completed,
                "review_count": review_count}, default=str))
        return
    if action == "verify":
        with psycopg.connect(DSN) as connection:
            reviews = connection.execute("SELECT count(*) FROM reviews WHERE card_id LIKE %s", (identifier+'-prepared-%',)).fetchone()[0]
            complete = connection.execute("SELECT count(*) FROM opening_evidence_attempts WHERE state='complete' AND card_id LIKE %s", (identifier+'-prepared-%',)).fetchone()[0]
            events = connection.execute("SELECT count(*) FROM opening_evidence_events e JOIN opening_evidence_attempts a ON a.attempt_id=e.attempt_id WHERE a.state='complete' AND a.card_id LIKE %s", (identifier+'-prepared-%',)).fetchone()[0]
            pending = connection.execute("SELECT count(*) FROM background_tasks WHERE id LIKE %s AND state!='complete'", (identifier+'-analysis-%',)).fetchone()[0]
            recovered = connection.execute("SELECT count(*) FROM opening_evidence_attempts WHERE state='partial' AND card_id=%s", (identifier+'-prepared-000',)).fetchone()[0]
            print(json.dumps({"reviews": reviews, "complete_evidence": complete, "completion_events": events, "pending_maintenance": pending, "historical_partials": recovered}))
        return
    raise ValueError("Unknown fixture action")


def prove_optional_handoff(identifier):
    """Real locks, post-review head validation and both durable receipt layers."""
    from app import postgres_store
    from app.command_gateway import execute_command, request_digest
    from app.models import ReviewRequest

    today = date.today().isoformat()
    with psycopg.connect(DSN) as connection:
        connection.execute("UPDATE daily_queue SET position=position-1000 WHERE card_id LIKE %s", (identifier+'-prepared-%',))
        entries = connection.execute("SELECT q.id,c.id,c.revision,q.cycle FROM daily_queue q JOIN cards c ON q.card_id=c.id WHERE c.id LIKE %s ORDER BY c.id", (identifier+'-prepared-%',)).fetchall()

    def identity(index):
        entry, card, revision, cycle = entries[index]
        return {"queue_entry_id": entry, "card_id": card, "revision": revision, "cycle": cycle, "local_date": today}

    def issue(index):
        item = identity(index)
        assert execute_command(str(uuid4()), "queue.issue_head", {"queue_date": today, "entry_id": item["queue_entry_id"],
            "card_id": item["card_id"], "expected_revision": item["revision"]})["issued"]

    def review(index, next_index=None):
        item = identity(index)
        request = ReviewRequest(outcome="correct", queue_entry_id=item["queue_entry_id"], expected_revision=item["revision"],
            attempt_id=f"{identifier[-36:]}-{index}", prepared_next=identity(next_index) if next_index is not None else None)
        return {"card_id": item["card_id"], "review": request.durable_payload()}

    issue(0)
    with psycopg.connect(DSN) as editing:
        editing.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{identity(1)['card_id']}",))
        result = execute_command(str(uuid4()), "cards.review", review(0, 1))
        assert result["persisted"] and "prepared_continuation" not in result
    issue(1)
    payload = review(1, 2)
    operation = str(uuid4())
    result = execute_command(operation, "cards.review", payload)
    assert result["prepared_continuation"] == {**identity(2), "attempt_failed": False}
    postgres_store.close_pools()
    assert execute_command(operation, "cards.review", payload) == result
    assert execute_command(str(uuid4()), "cards.review", payload) == result
    with psycopg.connect(DSN) as connection:
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id=%s", (identity(1)["card_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s", (identity(2)["queue_entry_id"],)).fetchone()[0] == 1
        connection.execute("UPDATE daily_queue SET position=-2000 WHERE id=%s", (identity(4)["queue_entry_id"],))
    assert "prepared_continuation" not in execute_command(str(uuid4()), "cards.review", review(2, 3))
    issue(4)
    legacy = review(4)
    assert "prepared_next" not in legacy["review"]
    old_shape = {"outcome": "correct", "guided": False, "queue_entry_id": identity(4)["queue_entry_id"], "recorded_at": None,
        "expected_review_id": None, "expected_revision": 1, "attempt_id": legacy["review"]["attempt_id"]}
    assert request_digest("cards.review", legacy) == request_digest("cards.review", {"card_id": identity(4)["card_id"], "review": old_shape})
    legacy_result = execute_command(str(uuid4()), "cards.review", legacy)
    assert legacy_result["persisted"] and "prepared_continuation" not in legacy_result
    print("prepared_continuation_native_lock_head_and_receipt_replay: passed")
    test_postgres_prepared_optional_locks_decline_without_blocking_review(identity, issue, review)
    test_postgres_prepared_issuance_fences_competing_edits_and_deletions(identity, issue, review)


def test_postgres_prepared_optional_locks_decline_without_blocking_review(identity, issue, review):
    """Exercise native advisory/row contention with both lexical card orders."""
    from app.command_gateway import execute_command

    for reviewed_index, successor_index, contention in [(6, 5, "advisory"), (8, 9, "row")]:
        reviewed_identity = identity(reviewed_index)
        successor_identity = identity(successor_index)
        with psycopg.connect(DSN) as connection:
            connection.execute("UPDATE daily_queue SET position=-4000 WHERE id=%s", (reviewed_identity["queue_entry_id"],))
            connection.execute("UPDATE daily_queue SET position=-3999 WHERE id=%s", (successor_identity["queue_entry_id"],))
        issue(reviewed_index)
        operation = str(uuid4())
        payload = review(reviewed_index, successor_index)
        with psycopg.connect(DSN) as editing:
            if contention == "advisory":
                editing.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{successor_identity['card_id']}",))
            else:
                # Isolate row contention from other mutation-trigger locks.
                editing.execute("SELECT id FROM cards WHERE id=%s FOR UPDATE", (successor_identity["card_id"],))
            result = execute_command(operation, "cards.review", payload)
            assert result["persisted"] and "prepared_continuation" not in result
            # The editor still owns its lock when persistence has completed.
            with psycopg.connect(DSN) as observer:
                assert observer.execute("SELECT count(*) FROM reviews WHERE card_id=%s", (reviewed_identity["card_id"],)).fetchone()[0] == 1
                assert observer.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s", (successor_identity["queue_entry_id"],)).fetchone()[0] == 0
            if contention == "row":
                editing.execute("UPDATE cards SET revision=revision+1 WHERE id=%s", (successor_identity["card_id"],))
        assert execute_command(operation, "cards.review", payload) == result
        assert execute_command(str(uuid4()), "cards.review", payload) == result
        if contention == "row":
            assert execute_command(str(uuid4()), "queue.issue_head", {"queue_date": successor_identity["local_date"],
                "entry_id": successor_identity["queue_entry_id"], "card_id": successor_identity["card_id"],
                "expected_revision": successor_identity["revision"]}) == {"issued": False}
    # Validation after a completed deletion must preserve the preceding review.
    with psycopg.connect(DSN) as connection:
        connection.execute("UPDATE daily_queue SET position=-5000 WHERE id=%s", (identity(10)["queue_entry_id"],))
        connection.execute("UPDATE daily_queue SET position=-4999 WHERE id=%s", (identity(11)["queue_entry_id"],))
        connection.execute("INSERT INTO deleted_cards(card_id,deleted_at) VALUES(%s,%s)", (identity(11)["card_id"], date.today().isoformat()))
    issue(10)
    deleted_successor_result = execute_command(str(uuid4()), "cards.review", review(10, 11))
    assert deleted_successor_result["persisted"] and "prepared_continuation" not in deleted_successor_result
    print("PASS test_postgres_prepared_optional_locks_decline_without_blocking_review")


def test_postgres_prepared_issuance_fences_competing_edits_and_deletions(identity, issue, review):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic
    from unittest.mock import patch
    from app import postgres_store, review_commands
    from app.card_deletion import permanent_delete_card
    from app.command_gateway import execute_command

    for reviewed_index, successor_index, mutation in [(30, 31, "edit"), (32, 33, "delete")]:
        successor_identity = identity(successor_index)
        with psycopg.connect(DSN) as connection:
            connection.execute("UPDATE daily_queue SET position=%s WHERE id=%s", (-6000-reviewed_index, identity(reviewed_index)["queue_entry_id"]))
            connection.execute("UPDATE daily_queue SET position=%s WHERE id=%s", (-5999-reviewed_index, successor_identity["queue_entry_id"]))
        issue(reviewed_index)
        issuance_reached, release_issuance, mutation_started = Event(), Event(), Event()
        review_pids, mutation_pids = [], []
        original_issuance = review_commands._issue_prepared_continuation

        def pause_issuance(database, candidate):
            review_pids.append(database.execute_native("SELECT pg_backend_pid()").fetchone()[0])
            issuance_reached.set()
            assert release_issuance.wait(timeout=15), "Prepared issuance barrier was not released"
            return original_issuance(database, candidate)

        def mutate_successor():
            with postgres_store.connection() as database:
                mutation_pids.append(database.execute_native("SELECT pg_backend_pid()").fetchone()[0])
                mutation_started.set()
                if mutation == "delete":
                    permanent_delete_card(database, successor_identity["card_id"], successor_identity["revision"])
                else:
                    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{successor_identity['card_id']}",))
                    database.execute("UPDATE cards SET revision=revision+1 WHERE id=?", (successor_identity["card_id"],))

        with ThreadPoolExecutor(max_workers=2) as executor, patch.object(review_commands, "_issue_prepared_continuation", pause_issuance):
            review_future = executor.submit(execute_command, str(uuid4()), "cards.review", review(reviewed_index, successor_index))
            try:
                assert issuance_reached.wait(timeout=5), "Review did not reach prepared issuance"
                mutation_future = executor.submit(mutate_successor)
                assert mutation_started.wait(timeout=5), "Competing mutation did not start"
                deadline = monotonic() + 5
                with psycopg.connect(DSN, autocommit=True) as observer:
                    while True:
                        blocked = observer.execute("SELECT wait_event_type,pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid=%s", (mutation_pids[0],)).fetchone()
                        if blocked and blocked[0] == "Lock" and review_pids[0] in blocked[1]:
                            break
                        assert not mutation_future.done(), "Mutation escaped the prepared-card lock"
                        assert monotonic() < deadline, f"Mutation did not wait on review: {blocked}"
            finally:
                release_issuance.set()
            result = review_future.result(timeout=15)
            mutation_future.result(timeout=15)
        assert result["persisted"] and result["prepared_continuation"] == {**successor_identity, "attempt_failed": False}
        assert execute_command(str(uuid4()), "queue.issue_head", {"queue_date": successor_identity["local_date"],
            "entry_id": successor_identity["queue_entry_id"], "card_id": successor_identity["card_id"],
            "expected_revision": successor_identity["revision"]}) == {"issued": False}
        with psycopg.connect(DSN) as observer:
            assert observer.execute("SELECT count(*) FROM reviews WHERE card_id=%s", (identity(reviewed_index)["card_id"],)).fetchone()[0] == 1
    print("PASS test_postgres_prepared_issuance_fences_competing_edits_and_deletions")


if __name__ == "__main__":
    main()
