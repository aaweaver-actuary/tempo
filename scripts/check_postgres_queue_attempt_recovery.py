"""Real foreground/maintenance interleaving and poisoned-receipt recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid
from threading import Event
from unittest.mock import patch

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import postgres_store, queue_commands, queue_attempt_origins, review_commands  # registers the production review handlers
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
                database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (identifier, card_id))
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
        # A stale guided marker must not poison content reassigned to the same
        # projection ID. The production foreground handler receives displayed identity.
        with postgres_store.connection() as database:
            database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(?,?,-200,'review',?) RETURNING id", (today, card_ids[2], identifier))
            reassigned_entry = database.execute("SELECT id FROM daily_queue WHERE card_id=? AND position=-200", (card_ids[2],)).fetchone()[0]
            database.execute("UPDATE daily_queue SET card_id=? WHERE id=?", (card_ids[3], reassigned_entry))
        for suffix, marker_payload in (("legacy", {"entry_id": reassigned_entry}),
                                       ("identified", {"entry_id": reassigned_entry, "card_id": card_ids[2], "expected_revision": 2})):
            marker_operation = f"{identifier}-stale-marker-{suffix}"
            operations.append(marker_operation)
            assert execute_command(marker_operation, "queue.attempt_failed", marker_payload) is None
            assert read_operation(marker_operation)["error"]["code"] == "queue_attempt_unprovable"
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT attempt_failed FROM daily_queue WHERE id=?", (reassigned_entry,)).fetchone()[0] == 0
        current_marker_operation = f"{identifier}-current-marker"
        operations.append(current_marker_operation)
        current_marker = {"entry_id": reassigned_entry, "card_id": card_ids[3], "expected_revision": 1}
        assert execute_command(current_marker_operation, "queue.attempt_failed", current_marker)["attempt_failed"]
        assert execute_command(current_marker_operation, "queue.attempt_failed", current_marker)["attempt_failed"]
        print("PASS test_postgres_stale_guided_marker_reassignment_and_idempotent_current_context")
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


def test_postgres_guided_marker_locks_displayed_revision_until_commit():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Guided-marker proof requires a disposable PostgreSQL instance")
    dsn = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_DATABASE_WRITE_URL"] = dsn
    os.environ["TEMPO_DATABASE_READ_URL"] = dsn
    postgres_store.close_pools()
    identifier = f"guided-revision-proof-{uuid.uuid4()}"
    card_ids = [f"{identifier}-{suffix}" for suffix in ("marker-first", "edit-first")]
    today = date.today().isoformat()
    operations = []
    validated = Event()
    release_marker = Event()
    executor = ThreadPoolExecutor(max_workers=2)
    editor = psycopg.connect(dsn)
    original_validation = queue_attempt_origins.validate_failure_marker
    marker_pid = []
    try:
        with postgres_store.connection() as database:
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(?,?,'synthetic',?,0)", (identifier, "Guided revision proof", today))
            entries = []
            for index, card_id in enumerate(card_ids):
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,first_correct_at) VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,?,?)",
                    (card_id, identifier, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", today, today, today))
                database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (identifier, card_id))
                entries.append(database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(?,?,?,'review',?) RETURNING id", (today, card_id, -1000000 + index, identifier)).fetchone()[0])

        def pause_after_validation(database, entry_id, card_id=None, expected_revision=None):
            original_validation(database, entry_id, card_id, expected_revision)
            if entry_id == entries[0]:
                marker_pid.append(database.execute("SELECT pg_backend_pid()").fetchone()[0])
                validated.set()
                if not release_marker.wait(timeout=15):
                    raise AssertionError("Marker barrier was not released")

        def edit_revision():
            editor.execute("UPDATE cards SET revision=2,moves_json='[\"d2d4\"]' WHERE id=%s", (card_ids[0],))
            editor.commit()

        marker_operation = f"{identifier}-marker"
        operations.append(marker_operation)
        with patch.object(queue_attempt_origins, "validate_failure_marker", pause_after_validation):
            marker_future = executor.submit(execute_command, marker_operation, "queue.attempt_failed",
                {"entry_id": entries[0], "card_id": card_ids[0], "expected_revision": 1})
            assert validated.wait(timeout=5), "Production marker did not reach validation"
            editor_future = executor.submit(edit_revision)
            try:
                deadline = time.monotonic() + 5
                with psycopg.connect(dsn, autocommit=True) as observer:
                    while True:
                        blocked = observer.execute("SELECT wait_event_type,pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid=%s", (editor.info.backend_pid,)).fetchone()
                        if blocked and blocked[0] == "Lock" and marker_pid[0] in blocked[1]:
                            break
                        assert not editor_future.done(), "Editor committed R2 before the validated R1 marker committed"
                        assert time.monotonic() < deadline, f"Editor did not wait for marker lock: {blocked}"
            finally:
                release_marker.set()
            assert marker_future.result(timeout=5)["attempt_failed"]
            editor_future.result(timeout=5)
        with postgres_store.connection(read_only=True) as database:
            origins = [tuple(row) for row in database.execute("SELECT revision,attempt_failed FROM queue_attempt_origins WHERE queue_entry_id=? ORDER BY revision", (entries[0],))]
            assert origins == [(1, 1), (2, 0)], origins
            projection = database.execute("SELECT attempt_failed,status,admission_kind,admission_repertoire_id,cycle FROM daily_queue WHERE id=?", (entries[0],)).fetchone()
            assert tuple(projection) == (0, "queued", "review", identifier, 0)
        review_operation = f"{identifier}-review"
        operations.append(review_operation)
        payload = {"card_id": card_ids[0], "review": {"outcome": "correct", "guided": False,
            "queue_entry_id": entries[0], "expected_revision": 2, "attempt_id": f"{identifier}-R2",
            "recorded_at": datetime.now(timezone.utc).isoformat()}}
        saved = execute_command(review_operation, "cards.review", payload)
        assert saved["persisted"]
        assert execute_command(review_operation, "cards.review", payload) == saved
        replay_operation = f"{identifier}-review-replay"
        operations.append(replay_operation)
        assert execute_command(replay_operation, "cards.review.reconcile", payload) == saved
        with postgres_store.connection() as database:
            assert [tuple(row) for row in database.execute("SELECT rating,guided FROM reviews WHERE card_id=?", (card_ids[0],))] == [("correct", 0)]
            # Opposite ordering: editing commits before the stale marker arrives.
            database.execute("UPDATE cards SET revision=2,moves_json='[\"d2d4\"]' WHERE id=?", (card_ids[1],))
        stale_operation = f"{identifier}-edit-first-marker"
        operations.append(stale_operation)
        assert execute_command(stale_operation, "queue.attempt_failed", {"entry_id": entries[1], "card_id": card_ids[1], "expected_revision": 1}) is None
        assert read_operation(stale_operation)["error"]["code"] == "queue_attempt_unprovable"
        with postgres_store.connection(read_only=True) as database:
            assert database.execute("SELECT attempt_failed FROM daily_queue WHERE id=?", (entries[1],)).fetchone()[0] == 0
            assert [tuple(row) for row in database.execute("SELECT revision,attempt_failed FROM queue_attempt_origins WHERE queue_entry_id=? ORDER BY revision", (entries[1],))] == [(1, 0), (2, 0)]
        print("PASS test_postgres_guided_marker_locks_displayed_revision_until_commit (marker-first and edit-first)")
    finally:
        release_marker.set()
        executor.shutdown(wait=True)
        editor.rollback()
        editor.close()
        postgres_store.close_pools()
        with postgres_store.connection() as database:
            for operation_id in operations:
                database.execute("DELETE FROM operation_receipts WHERE operation_id=?", (operation_id,))
            for card_id in card_ids:
                database.execute("DELETE FROM review_schedule_snapshots WHERE review_id IN (SELECT id FROM reviews WHERE card_id=?)", (card_id,))
                database.execute("DELETE FROM review_attempt_receipts WHERE card_id=?", (card_id,))
            database.execute("DELETE FROM repertoires WHERE id=?", (identifier,))
            for card_id in card_ids:
                database.execute("DELETE FROM queue_attempt_origins WHERE card_id=?", (card_id,))
        postgres_store.close_pools()


def test_postgres_retired_opening_evidence_reconciliation_is_atomic_and_replay_safe():
    from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
    from app.services.postgres_opening_evidence import prepare_checkpoint, persist_checkpoint
    from check_postgres_opening_evidence import (
        _create_color_fixture, _transport_color_fixture, _color_checkpoint,
        _fixture_scheduling, _retain_color_provenance, shadow_digest,
    )
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Opening reconciliation proof requires disposable PostgreSQL")
    dsn = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_DATABASE_WRITE_URL"] = dsn
    os.environ["TEMPO_DATABASE_READ_URL"] = dsn
    postgres_store.close_pools()
    operations = []
    fixtures = []
    today = date.today().isoformat()
    with postgres_store.connection() as database:
        snapshot = snapshot_queue_environment(database, (today, today))
    try:
        for scenario in ("valid", "guided", "changed", "unprovable"):
            fixture = _create_color_fixture('black')
            fixtures.append(fixture)
            manifest = _transport_color_fixture(fixture)['opening_decision_manifest']
            checkpoint = _color_checkpoint(fixture, manifest).model_dump(mode='json')
            checkpoint['terminal']['state'] = 'complete'
            completion = OpeningEvidenceCheckpoint.model_validate(checkpoint)
            prepared = prepare_checkpoint(completion)
            payload = {'card_id':fixture['card_id'], 'review':{
                'attempt_id':completion.attempt_id, 'queue_entry_id':fixture['queue_id'],
                'outcome':'again' if scenario == 'guided' else 'correct', 'guided':scenario == 'guided',
                'expected_revision':completion.manifest.card_revision, 'recorded_at':completion.terminal.ended_at,
                'opening_evidence_completion':checkpoint}, 'prepared_manifest':prepared}
            original_operation = fixture['card_id']+'-original-save'
            reconcile_operation = fixture['card_id']+'-reconcile'
            operations.extend((original_operation, reconcile_operation))
            with postgres_store.connection() as database:
                # Previously acknowledged capture remains durable if this operation conflicts.
                active = {**checkpoint, 'events':[], 'terminal':None}
                persist_checkpoint(database, {'checkpoint':active,'prepared_manifest':prepared})
                database.execute('DELETE FROM daily_queue WHERE id=?', (fixture['queue_id'],))
                # A historical failed delivery must remain failed; recovery has its own transport identity.
                database.execute_native(
                    "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,error_json) "
                    "VALUES(%s,'cards.review',%s,'failed',%s,%s)",
                    (original_operation, request_digest('cards.review',payload), json.dumps(payload),
                     json.dumps({'status_code':409,'message':'Historical queue projection was unavailable'})))
                if scenario == 'changed':
                    database.execute("UPDATE cards SET moves_json='[\"d2d4\",\"d7d5\"]' WHERE id=?", (fixture['card_id'],))
                elif scenario == 'unprovable':
                    database.execute('DELETE FROM queue_attempt_origins WHERE queue_entry_id=?', (fixture['queue_id'],))
                elif scenario == 'guided':
                    # The validated parent color must survive mutable fallback changes.
                    database.execute("UPDATE repertoire_lines SET trained_color='white' WHERE repertoire_id=?", (fixture['repertoire_id'],))
                before_shadow = shadow_digest(database, fixture['card_id'])
                before_scheduling = _fixture_scheduling(database, fixture)
            try:
                execute_command(original_operation, 'cards.review', payload)
            except RuntimeError as error:
                assert str(error) == 'Historical queue projection was unavailable'
            else:
                raise AssertionError('The original failed transport receipt must remain authoritative')
            result = execute_command(reconcile_operation, 'cards.review.reconcile', payload)
            assert result is not None, read_operation(reconcile_operation)
            assert read_operation(original_operation)['state'] == 'failed'
            with postgres_store.connection() as database:
                if scenario in {'changed','unprovable'}:
                    expected_code = 'card_revision_changed' if scenario == 'changed' else 'queue_attempt_unprovable'
                    assert result['persisted'] is False and result['conflict']['code'] == expected_code
                    assert shadow_digest(database, fixture['card_id']) == before_shadow, 'Conflict committed partial evidence'
                    assert _fixture_scheduling(database, fixture) == before_scheduling, 'Conflict committed review/scheduling writes'
                    assert database.execute('SELECT COUNT(*) FROM review_attempt_receipts WHERE attempt_id=?', (completion.attempt_id,)).fetchone()[0] == 0
                else:
                    assert result['persisted'] is True
                    assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?', (fixture['card_id'],)).fetchone()[0] == 1
                    receipt = database.execute('SELECT attempt_id,completed_at,request_json FROM review_attempt_receipts WHERE attempt_id=?', (completion.attempt_id,)).fetchone()
                    assert receipt['attempt_id'] == completion.attempt_id
                    assert json.loads(receipt['request_json'])['opening_evidence_completion'] == checkpoint
                    assert json.loads(receipt['request_json'])['recorded_at'] == fixture['now'].isoformat()
                    binding = database.execute_native('SELECT state,review_id,completed_at,review_result_json FROM opening_evidence_attempts WHERE attempt_id=%s', (completion.attempt_id,)).fetchone()
                    assert binding['state'] == 'complete' and binding['review_id'] == result['review_id']
                    assert binding['completed_at'] == fixture['now'] and json.loads(binding['review_result_json']) == result
                    assert database.execute_native('SELECT COUNT(*) FROM opening_evidence_observations WHERE attempt_id=%s', (completion.attempt_id,)).fetchone()[0] == 1
                    if scenario == 'guided':
                        assert result['requeue_entry_id'] is not None
                        context = database.execute_native('SELECT effective_trained_color FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s AND presentation_snapshot_id=%s AND repertoire_id=%s',
                            (result['requeue_entry_id'],completion.manifest.presentation_snapshot_id,completion.manifest.repertoire_id)).fetchone()
                        assert context[0] == 'black'
                after_shadow = shadow_digest(database, fixture['card_id'])
                after_scheduling = _fixture_scheduling(database, fixture)
            assert execute_command(reconcile_operation, 'cards.review.reconcile', payload) == result
            replay_operation = fixture['card_id']+'-reconcile-replay'
            operations.append(replay_operation)
            assert execute_command(replay_operation, 'cards.review.reconcile', payload) == result
            with postgres_store.connection() as database:
                assert shadow_digest(database, fixture['card_id']) == after_shadow
                assert _fixture_scheduling(database, fixture) == after_scheduling
            _retain_color_provenance(fixture)
        print('PASS test_postgres_retired_opening_evidence_reconciliation_is_atomic_and_replay_safe (valid/guided/changed/unprovable, failed transport, receipt replay, completion, immutable color)')
    finally:
        with postgres_store.connection() as database:
            for operation in operations:
                database.execute('DELETE FROM operation_receipts WHERE operation_id=?', (operation,))
            for fixture in fixtures:
                database.execute('DELETE FROM review_schedule_snapshots WHERE review_id IN (SELECT id FROM reviews WHERE card_id=?)', (fixture['card_id'],))
                database.execute('DELETE FROM review_attempt_receipts WHERE card_id=?', (fixture['card_id'],))
                database.execute('DELETE FROM repertoires WHERE id=?', (fixture['repertoire_id'],))
                database.execute('DELETE FROM queue_attempt_origins WHERE card_id=?', (fixture['card_id'],))
            restore_queue_environment(database, snapshot)
        postgres_store.close_pools()


def test_postgres_real_game_obligation_admission_restart_publication_and_remediation():
    """One published canonical miss overrides locks/caps and survives restart."""
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Real-game obligation proof requires disposable PostgreSQL")
    from app.services import postgres_queue_refresh as refresh
    from app.services.real_game_feedback import MISS_REASON, promote_real_game_card, has_outstanding_real_game_miss
    from app.services.durable_tasks import claim_task, enqueue_task_in_transaction
    os.environ["TEMPO_DATABASE_WRITE_URL"] = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_DATABASE_READ_URL"] = os.environ["TEMPO_DATABASE_WRITE_URL"]
    postgres_store.close_pools()
    identifier = f"real-game-obligation-{uuid.uuid4()}"
    card_ids = [f"{identifier}-{suffix}" for suffix in ("a-locked", "b-studied", "z-active")]
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    now = datetime.now(timezone.utc)
    operations = []
    snapshot = None
    positions = []
    saved_days = []
    try:
        with postgres_store.connection() as database:
            snapshot = snapshot_queue_environment(database, (today, tomorrow))
            positions = [tuple(row) for row in database.execute("SELECT id,position FROM daily_queue WHERE queue_date IN (?,?)", (today,tomorrow))]
            saved_days = [dict(row) for row in database.execute("SELECT * FROM daily_queue_days WHERE queue_date IN (?,?)", (today,tomorrow))]
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day,canonical_prefix_revision) VALUES(?,?,'synthetic',?,0,1)", (identifier,identifier,today))
            for index, card_id in enumerate(card_ids):
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,unlock_after_card_id) VALUES(?,?,'prefix',?,'[\"e2e4\"]',?,?,?,?)",
                    (card_id,identifier,"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                     'locked' if index == 0 else 'mature', (date.today()+timedelta(days=90)).isoformat(), None if index == 0 else today, None))
                database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (identifier,card_id))
                if index:
                    database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES(?,'correct',?,0,90,'study')", (card_id,(now-timedelta(days=2)).isoformat()))
            database.execute("UPDATE cards SET unlock_after_card_id=? WHERE id=?", (card_ids[2],card_ids[0]))
            active_entry = database.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,-1000000) RETURNING id", (today,card_ids[2])).fetchone()[0]
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES(?,'lichess','obligation',?,'rapid',1,'white','1-0',?,'[\"d2d4\"]')", (identifier,now.isoformat(),"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"))
            database.execute("INSERT INTO game_derivation_jobs(game_id,derivation_version,published_repertoire_version,updated_at) VALUES(?,2,1,?)", (identifier,now.isoformat()))
            for version in (1,2):
                for index, card_id in enumerate(card_ids[:2]):
                    database.execute("INSERT INTO repertoire_decision_events_staged(id,game_id,derivation_version,repertoire_id,card_id,ply,fen_key,expected_uci,actual_uci,outcome,played_at,updated_at) VALUES(?,?,?,?,?,?,?,'e2e4','d2d4','miss',?,?)",
                        (f'{identifier}-{index}',identifier,version,identifier,card_id,index*2,'known',now.isoformat(),now.isoformat()))
            before_parent = dict(database.execute("SELECT * FROM cards WHERE id=?", (card_ids[2],)).fetchone())
            before_studied = dict(database.execute("SELECT * FROM cards WHERE id=?", (card_ids[1],)).fetchone())
            # No exact-history match exists. Only the atomically published events are visible.
            assert database.execute("SELECT COUNT(*) FROM current_repertoire_decision_events WHERE game_id=?", (identifier,)).fetchone()[0] == 2
        ordinary_order_plan = refresh._prepare_queue_randomization(today)
        with postgres_store.connection() as database:
            promote_real_game_card(database,card_ids[1],today)
            # Membership is unchanged; a prepared ordinary mix must respect the
            # newly published priority at commit rather than overwrite it.
            assert refresh._publish_queue_randomization(database,today,ordinary_order_plan) is False
        plan = refresh._prepare_prioritized_openings(today)
        locked_plan = next(item for item in plan if item['card_id'] == card_ids[0])
        with postgres_store.connection() as database:
            refresh._admit_one_prioritized_opening(database,today,locked_plan)
            entry = database.execute("SELECT id FROM daily_queue WHERE queue_date=? AND card_id=?", (today,card_ids[0])).fetchone()[0]
            assert refresh._reconcile_one_unseen_entry(database,today,{'id':entry,'card_id':card_ids[0],'repertoire_id':identifier},0)
            promote_real_game_card(database,card_ids[0],today)
            promote_real_game_card(database,card_ids[1],today)
            assert dict(database.execute("SELECT * FROM cards WHERE id=?", (card_ids[2],)).fetchone()) == before_parent
            assert dict(database.execute("SELECT * FROM cards WHERE id=?", (card_ids[1],)).fetchone()) == before_studied
            assert database.execute("SELECT COUNT(*) FROM daily_queue WHERE queue_date=? AND card_id=?", (today,card_ids[1])).fetchone()[0] == 1
        with postgres_store.connection() as database:
            database.execute("UPDATE daily_queue SET gameplay_priority_reason=NULL WHERE queue_date=? AND card_id IN (?,?)", (today,card_ids[0],card_ids[1]))
        stale_priority_plan = refresh._prepare_queue_randomization(today)
        with postgres_store.connection() as database:
            promote_real_game_card(database,card_ids[0],today)
            promote_real_game_card(database,card_ids[1],today)
            assert refresh._publish_queue_randomization(database,today,stale_priority_plan)
            ordered = [row[0] for row in database.execute("SELECT card_id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id", (today,))]
            assert ordered[:2] == card_ids[:2]
        operation = f'{identifier}-retained-marker'
        operations.append(operation)
        assert execute_command(operation,'queue.attempt_failed',{'entry_id':active_entry,'card_id':card_ids[2],'expected_revision':1})['attempt_failed']
        with postgres_store.connection() as database:
            enqueue_task_in_transaction(database,'daily_queue','current',{'queue_date':tomorrow,'_queue_phase':'real_game_misses'},priority=10)
        abandoned = claim_task('daily_queue')
        with postgres_store.connection() as database:
            database.execute("UPDATE background_tasks SET lease_expires_at=? WHERE id=?", ((now-timedelta(seconds=1)).isoformat(),abandoned['id']))
        postgres_store.close_pools()
        assert not refresh.execute_postgres_queue_refresh_slice(abandoned)
        phases = []
        for _ in range(30):
            task = claim_task('daily_queue')
            if not task:
                break
            phases.append(task['payload'].get('_queue_phase'))
            refresh.execute_postgres_queue_refresh_slice(task)
        else:
            raise AssertionError('Bounded obligation refresh did not finish')
        assert phases.count('real_game_misses') >= 3
        with postgres_store.connection() as database:
            queued = database.execute("SELECT card_id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id", (tomorrow,)).fetchall()
            assert [row[0] for row in queued if row[0] in card_ids[:2]] == card_ids[:2]
            studied_entry = database.execute("SELECT id FROM daily_queue WHERE queue_date=? AND card_id=?", (today,card_ids[1])).fetchone()[0]
        operation = f'{identifier}-remediate'
        operations.append(operation)
        payload = {'card_id':card_ids[1],'review':{'outcome':'correct','guided':False,'queue_entry_id':studied_entry,'expected_revision':1,'attempt_id':operation,'recorded_at':(now+timedelta(seconds=1)).isoformat()}}
        assert execute_command(operation,'cards.review',payload)['persisted']
        assert execute_command(operation,'cards.review',payload)['persisted']
        with postgres_store.connection() as database:
            assert not has_outstanding_real_game_miss(database,card_ids[1])
            assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (card_ids[1],)).fetchone()[0] == 2
            assert database.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND gameplay_priority_reason=?", (card_ids[1],MISS_REASON)).fetchone()[0] == 0
            # A not-yet-published replacement must never create a new obligation.
            database.execute("UPDATE repertoire_decision_events_staged SET played_at=? WHERE game_id=? AND derivation_version=2", ((now+timedelta(seconds=2)).isoformat(),identifier))
            assert not has_outstanding_real_game_miss(database,card_ids[1])
            database.execute("UPDATE game_derivation_jobs SET published_repertoire_version=2 WHERE game_id=?", (identifier,))
            assert has_outstanding_real_game_miss(database,card_ids[1])
            assert promote_real_game_card(database,card_ids[1],today)
            assert database.execute("SELECT COUNT(*) FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued'", (today,card_ids[1])).fetchone()[0] == 1
    finally:
        with postgres_store.connection() as database:
            for operation in operations:
                database.execute('DELETE FROM operation_receipts WHERE operation_id=?',(operation,))
            database.execute('DELETE FROM imported_games WHERE id=?',(identifier,))
            for card_id in card_ids:
                database.execute('DELETE FROM review_attempt_receipts WHERE card_id=?',(card_id,))
                database.execute('DELETE FROM review_schedule_snapshots WHERE review_id IN (SELECT id FROM reviews WHERE card_id=?)',(card_id,))
                database.execute('DELETE FROM reviews WHERE card_id=?',(card_id,))
            database.execute('DELETE FROM repertoires WHERE id=?',(identifier,))
            for card_id in card_ids:
                database.execute('DELETE FROM queue_attempt_origins WHERE card_id=?',(card_id,))
            for entry_id,position in positions:
                database.execute('UPDATE daily_queue SET position=? WHERE id=?',(position,entry_id))
            database.execute('DELETE FROM daily_queue_days WHERE queue_date IN (?,?)',(today,tomorrow))
            for day in saved_days:
                database.execute('INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at) VALUES(?,?,?,?)',tuple(day[column] for column in ('queue_date','seed','membership_hash','generated_at')))
            if snapshot is not None:
                restore_queue_environment(database,snapshot)
        postgres_store.close_pools()


if __name__ == "__main__":
    test_postgres_real_game_obligation_admission_restart_publication_and_remediation()
    test_postgres_retired_opening_evidence_reconciliation_is_atomic_and_replay_safe()
    test_postgres_guided_marker_locks_displayed_revision_until_commit()
    test_postgres_queue_attempt_maintenance_contention_restart_and_receipt_recovery()
