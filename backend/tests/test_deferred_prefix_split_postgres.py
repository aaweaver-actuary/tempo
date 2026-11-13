"""Deferred prefix split transaction proof; requires the owned PostgreSQL fixture.

These tests use synthetic retained review receipts. They do not claim a browser
or assembled save-to-next-card journey; the delivery owner runs that separately.
"""
from contextlib import contextmanager
from datetime import date
import json
import uuid

import chess
import pytest

from native_study_fixture import owned_postgres
from app import prefix_split_commands as commands
from app.services import deferred_prefix_split_recovery as recovery
from app.command_gateway import CommandPrerequisitePending
from tempo_storage.commands import queue as queue_storage
from tempo_storage.study import deferred_prefix_splits as intents


@pytest.fixture
def native(owned_postgres, monkeypatch):
    @contextmanager
    def borrowed(*, background):
        assert background
        with owned_postgres() as database:
            yield database
    monkeypatch.setattr(recovery, 'connection', borrowed)
    return owned_postgres


def seed(database, suffix):
    identity = 'p06-' + suffix + '-' + uuid.uuid4().hex
    day = date.today().isoformat()
    database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'fixture',%s)",
                            (identity, identity, day))
    database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,content_type,revision)
        VALUES(%s,%s,'prefix',%s,%s,'white','learning',%s,'opening',3)""",
        (identity, identity, chess.STARTING_FEN, json.dumps(['e2e4','e7e5','g1f3','b8c6','f1b5']), day))
    database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (identity, identity))
    entry = database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,card_bucket,admission_kind,admission_repertoire_id)
        VALUES(%s,%s,0,0,'opening','due',%s) RETURNING id""", (day, identity, identity)).fetchone()[0]
    database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s', (entry,))
    return {'card': identity, 'day': day, 'entry': entry, 'attempt': identity + '-attempt'}


def accept(database, case):
    return commands.accept_prefix_split(database, {'card_id': case['card'],
        'request': {'expected_revision': 3, 'queue_entry_id': case['entry']}})


def save_receipt(database, case):
    review_id = database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind,source_ref)
        VALUES(%s,'correct',%s,0,1,'study',%s) RETURNING id""",
        (case['card'], case['day'], 'attempt:' + case['attempt'])).fetchone()[0]
    receipt = json.dumps({'persisted': True, 'review_id': review_id}, separators=(',', ':'))
    database.execute_native("""INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,review_id,scheduling_status,result_json)
        VALUES(%s,%s,%s,'correct',0,%s,'saved',%s)""",
        (case['attempt'], case['card'], case['entry'], review_id, receipt))
    database.execute_native("UPDATE daily_queue SET status='complete' WHERE id=%s", (case['entry'],))
    return review_id, receipt


def fetch_intent(database, case):
    return database.execute_native('SELECT * FROM study_deferred_prefix_splits WHERE card_id=%s', (case['card'],)).fetchone()


def make_due(database, intent_id):
    database.execute_native("UPDATE study_deferred_prefix_splits SET next_check_at=NOW()-INTERVAL '1 second' WHERE intent_id=%s", (intent_id,))


@pytest.mark.parametrize('review_first', [False, True], ids=['accept-before-review', 'review-before-accept'])
def test_original_review_and_acceptance_order_recover_same_intent(native, review_first):
    with native() as db:
        case = seed(db, str(review_first))
        if review_first:
            review_id, receipt = save_receipt(db, case)
        accepted = accept(db, case)
        duplicate = accept(db, case)
        assert accepted['status'] == 'waiting_review'
        assert duplicate['intent_id'] == accepted['intent_id']
        if not review_first:
            review_id, receipt = save_receipt(db, case)
        saved = dict(fetch_intent(db, case))
        assert (saved['card_id'], saved['source_revision'], saved['queue_entry_id']) == (case['card'], 3, case['entry'])
    assert recovery.enqueue_one_ready_split()
    with native() as db:
        ready = dict(fetch_intent(db, case))
        assert (ready['intent_id'], ready['state'], ready['review_attempt_id'], ready['review_id']) == (
            accepted['intent_id'], 'ready', case['attempt'], review_id)
        assert ready['child_operation_id'] == 'prefix-split-intent:' + accepted['intent_id']
        operation = db.execute_native('SELECT command_name,payload_json FROM operation_receipts WHERE operation_id=%s', (ready['child_operation_id'],)).fetchone()
        assert operation['command_name'] == 'cards.prefix_split.apply_deferred'
        assert json.loads(operation['payload_json']) == {'intent_id': accepted['intent_id']}
        assert db.execute_native('SELECT result_json FROM review_attempt_receipts WHERE attempt_id=%s', (case['attempt'],)).fetchone()[0] == receipt
        result = commands.apply_deferred_prefix_split(db, {'intent_id': accepted['intent_id']})
        assert result['status'] == 'applied'
        assert dict(fetch_intent(db, case))['result_json'] == result['result']
        assert db.execute_native('SELECT card_id FROM reviews WHERE id=%s', (review_id,)).fetchone()[0] == case['card']
        assert db.execute_native('SELECT result_json FROM review_attempt_receipts WHERE attempt_id=%s', (case['attempt'],)).fetchone()[0] == receipt


def test_acceptance_transaction_rollback_preserves_issued_attempt(native):
    with native() as db:
        case = seed(db, 'rollback')
    with pytest.raises(RuntimeError, match='rollback sentinel'):
        with native() as db:
            accepted = accept(db, case)
            assert fetch_intent(db, case)['intent_id'] == accepted['intent_id']
            raise RuntimeError('rollback sentinel')
    with native() as db:
        assert fetch_intent(db, case) is None
        assert db.execute_native('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s', (case['entry'],)).fetchone()[0] == 1
        assert db.execute_native('SELECT archived FROM cards WHERE id=%s', (case['card'],)).fetchone()[0] == 0


def test_stale_source_holds_original_receipt_without_split(native):
    with native() as db:
        case = seed(db, 'stale')
        accepted = accept(db, case)
        review_id, receipt = save_receipt(db, case)
        db.execute_native('UPDATE cards SET revision=4 WHERE id=%s', (case['card'],))
    assert recovery.enqueue_one_ready_split()
    with native() as db:
        held = fetch_intent(db, case)
        assert (held['intent_id'], held['state'], held['reason']) == (accepted['intent_id'], 'held', 'source_changed')
        assert db.execute_native('SELECT COUNT(*) FROM prefix_splits WHERE source_card_id=%s', (case['card'],)).fetchone()[0] == 0
        assert tuple(db.execute_native('SELECT review_id,result_json FROM review_attempt_receipts WHERE attempt_id=%s', (case['attempt'],)).fetchone()) == (review_id, receipt)


def test_competing_issued_attempt_keeps_child_identity_until_retry(native):
    with native() as db:
        case = seed(db, 'compete'); accepted = accept(db, case); save_receipt(db, case)
    assert recovery.enqueue_one_ready_split()
    with native() as db:
        ready = dict(fetch_intent(db, case)); child = ready['child_operation_id']
        second = db.execute_native("INSERT INTO daily_queue(queue_date,card_id,cycle,position) VALUES(%s,%s,1,1) RETURNING id", (case['day'], case['card'])).fetchone()[0]
        db.execute_native('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s', (second,))
    with pytest.raises(CommandPrerequisitePending):
        with native() as db:
            commands.apply_deferred_prefix_split(db, {'intent_id': accepted['intent_id']})
    with native() as db:
        assert fetch_intent(db, case)['child_operation_id'] == child
        assert db.execute_native('SELECT COUNT(*) FROM prefix_splits WHERE source_card_id=%s', (case['card'],)).fetchone()[0] == 0
        db.execute_native("UPDATE daily_queue SET status='complete' WHERE id=%s", (second,))
    with native() as db:
        assert commands.apply_deferred_prefix_split(db, {'intent_id': accepted['intent_id']})['status'] == 'applied'
        assert fetch_intent(db, case)['child_operation_id'] == child


def test_locked_first_intent_yields_to_other_and_recovers_after_release(native, monkeypatch):
    with native() as db:
        first = seed(db, 'first'); first_result = accept(db, first); save_receipt(db, first)
        second = seed(db, 'second'); second_result = accept(db, second); save_receipt(db, second)
        db.execute_native("UPDATE study_deferred_prefix_splits SET next_check_at=NOW()-INTERVAL '2 minutes' WHERE intent_id=%s", (first_result['intent_id'],))
        db.execute_native("UPDATE study_deferred_prefix_splits SET next_check_at=NOW()-INTERVAL '1 minute' WHERE intent_id=%s", (second_result['intent_id'],))
    if first_result['intent_id'] > second_result['intent_id']:
        first, second = second, first
        first_result, second_result = second_result, first_result
    monkeypatch.setattr(recovery, '_scan_cursor', '')
    # A separate transaction models a live worker holding both source and intent.
    with native() as locked:
        commands._lock_prefix_card(locked, first['card'])
        intents.intent(locked, first_result['intent_id'], lock=True)
        assert recovery.enqueue_one_ready_split() is False
        assert recovery.enqueue_one_ready_split(), 'the next bounded beat must reach the second intent'
        with native() as db:
            assert fetch_intent(db, first)['state'] == 'waiting_review'
            assert fetch_intent(db, second)['state'] == 'ready'
    with native() as db:
        make_due(db, first_result['intent_id'])
    assert recovery.enqueue_one_ready_split()
    with native() as db:
        assert fetch_intent(db, first)['state'] == 'ready'
        assert fetch_intent(db, first)['review_attempt_id'] == first['attempt']
        assert fetch_intent(db, second)['review_attempt_id'] == second['attempt']
        # Do not leave this test's ready records ahead of subsequent tests.
        db.execute_native("UPDATE study_deferred_prefix_splits SET next_check_at=NOW()+INTERVAL '1 day' WHERE intent_id IN (%s,%s)",
                          (first_result['intent_id'], second_result['intent_id']))


def test_waiting_split_does_not_block_unrelated_queue_head(native):
    with native() as db:
        blocked = seed(db, 'queue-held'); accepted = accept(db, blocked)
        db.execute_native("UPDATE daily_queue SET status='complete' WHERE id=%s", (blocked['entry'],))
        # A waiting split does not veto ordinary issuance of its source card.
        source_entry = db.execute_native("INSERT INTO daily_queue(queue_date,card_id,cycle,position,card_bucket,admission_kind,admission_repertoire_id) VALUES(%s,%s,1,-100,'opening','due',%s) RETURNING id",
                          (blocked['day'], blocked['card'], blocked['card'])).fetchone()[0]
        other = seed(db, 'queue-other')
        db.execute_native("UPDATE daily_queue SET position=-90 WHERE id=%s", (other['entry'],))
        head = queue_storage.prepare_queue_head(db, 'not_due')(blocked['day'])
        assert head is not None and head['id'] == source_entry
        db.execute_native("UPDATE daily_queue SET status='complete' WHERE id=%s", (source_entry,))
        head = queue_storage.prepare_queue_head(db, 'not_due')(blocked['day'])
        assert head is not None and head['id'] == other['entry']
        assert fetch_intent(db, blocked)['intent_id'] == accepted['intent_id']
        assert fetch_intent(db, blocked)['state'] == 'waiting_review'
        db.execute_native("UPDATE study_deferred_prefix_splits SET next_check_at=NOW()+INTERVAL '1 day' WHERE intent_id=%s", (accepted['intent_id'],))


def test_lost_child_wake_recovers_same_operation_via_existing_worker(native, monkeypatch):
    from contextlib import nullcontext
    from app import command_gateway as gateway, tasks
    from app.services import queue_refresh_wakeup

    with native() as db:
        case = seed(db, 'wake'); accepted = accept(db, case); save_receipt(db, case)
    # No broker wake: the periodic scanner discovers the durable accepted intent.
    assert recovery.enqueue_one_ready_split()
    with native() as db:
        child = fetch_intent(db, case)['child_operation_id']
        db.execute_native("UPDATE operation_receipts SET updated_at=NOW()-INTERVAL '1 minute' WHERE operation_id=%s", (child,))

    @contextmanager
    def writer(_background):
        with native() as db:
            yield db
    monkeypatch.setattr(gateway, '_writer_connection', writer)
    monkeypatch.setattr(tasks.activity_gate, 'background_job', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(tasks.activity_gate, 'background_control', lambda: nullcontext())
    monkeypatch.setattr(tasks, 'study_only_enabled', lambda: True)
    dispatched = []
    monkeypatch.setattr(tasks.celery_app, 'send_task', lambda name, **kw: dispatched.append((name, kw)))
    monkeypatch.setattr(queue_refresh_wakeup, 'wake_queue_refresh', lambda *a, **kw: None)
    assert tasks.recover_study_operations.run()
    assert len(dispatched) == 1
    name, call = dispatched[0]
    assert name == 'app.tasks.execute_foreground_command'
    assert call['task_id'] == child
    assert call['args'] == [child, 'cards.prefix_split.apply_deferred', {'intent_id': accepted['intent_id']}]
    should_run, payload, token, _ = gateway.record_operation_attempt(child, call['args'][1], call['args'][2], background=False)
    assert should_run and payload == {'intent_id': accepted['intent_id']}
    result = gateway.execute_command(child, call['args'][1], payload, attempt_token=token)
    assert result['status'] == 'applied'
    with native() as db:
        assert fetch_intent(db, case)['state'] == 'applied'
        receipt = db.execute_native('SELECT state,response_json FROM operation_receipts WHERE operation_id=%s', (child,)).fetchone()
        assert receipt['state'] == 'complete' and json.loads(receipt['response_json']) == result


def test_delayed_split_readiness_respects_queued_grace_and_recovers_original_child(native, monkeypatch):
    """Synthetic review receipt; only the owned child timestamp simulates elapsed time.

    Real PostgreSQL locks/receipt selection are exercised. Celery publication is
    observed in process; this does not establish assembled broker behavior.
    """
    from contextlib import nullcontext
    from app import command_gateway as gateway, tasks
    from app.services import queue_refresh_wakeup

    with native() as db:
        case = seed(db, 'late-grace')
        accepted = accept(db, case)
        review_id, original_review = save_receipt(db, case)
        other = 'p06-unrelated-' + uuid.uuid4().hex
        db.execute_native("""INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,background,updated_at)
            VALUES(%s,'settings.update','unrelated-test','queued','{}',false,NOW()-INTERVAL '1 minute')""", (other,))

    @contextmanager
    def writer(_background):
        with native() as db:
            yield db
    monkeypatch.setattr(gateway, '_writer_connection', writer)
    monkeypatch.setattr(tasks.activity_gate, 'background_job', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(tasks.activity_gate, 'background_control', lambda: nullcontext())
    monkeypatch.setattr(tasks, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(queue_refresh_wakeup, 'wake_queue_refresh', lambda *a, **kw: None)
    monkeypatch.setattr(recovery, '_scan_cursor', '')
    dispatched = []
    monkeypatch.setattr(tasks.celery_app, 'send_task', lambda name, **kw: dispatched.append((name, kw)))

    # A real owner lock delays this split while another eligible receipt routes.
    with native() as locked:
        commands._lock_prefix_card(locked, case['card'])
        assert tasks.recover_study_operations.run()
        assert [(name, call['task_id'], call['queue']) for name, call in dispatched] == [
            ('app.tasks.execute_foreground_command', other, 'foreground')]
        with native() as db:
            waiting = fetch_intent(db, case)
            assert (waiting['intent_id'], waiting['state'], waiting['queue_entry_id']) == (accepted['intent_id'], 'waiting_review', case['entry'])
            assert db.execute_native('SELECT result_json FROM review_attempt_receipts WHERE attempt_id=%s', (case['attempt'],)).fetchone()[0] == original_review
    with native() as db:
        make_due(db, accepted['intent_id'])
    dispatched.clear()
    # The recovery beat enqueues the child after its claim phase; no wake is sent.
    assert tasks.recover_study_operations.run()
    assert dispatched == []
    with native() as db:
        ready = fetch_intent(db, case)
        child = 'prefix-split-intent:' + accepted['intent_id']
        assert (ready['state'], ready['child_operation_id'], ready['review_attempt_id'], ready['review_id']) == ('ready', child, case['attempt'], review_id)
        receipt = db.execute_native("SELECT state,command_name,background,updated_at,NOW()-updated_at AS age FROM operation_receipts WHERE operation_id=%s", (child,)).fetchone()
        assert (receipt['state'], receipt['command_name'], receipt['background']) == ('queued', 'cards.prefix_split.apply_deferred', False)
        assert receipt['age'].total_seconds() < 30
    assert tasks.recover_study_operations.run() is False
    assert dispatched == []
    with native() as db:
        # Simulate passage beyond the actual 30-second DB predicate; no global clock change.
        db.execute_native("UPDATE operation_receipts SET updated_at=NOW()-INTERVAL '31 seconds' WHERE operation_id=%s", (child,))
    assert tasks.recover_study_operations.run()
    assert len(dispatched) == 1
    name, call = dispatched[0]
    assert (name, call['task_id'], call['queue']) == ('app.tasks.execute_foreground_command', child, 'foreground')
    assert call['args'] == [child, 'cards.prefix_split.apply_deferred', {'intent_id': accepted['intent_id']}]
    should_run, payload, token, _ = gateway.record_operation_attempt(child, call['args'][1], call['args'][2], background=False)
    assert should_run and payload == {'intent_id': accepted['intent_id']}
    result = gateway.execute_command(child, call['args'][1], payload, attempt_token=token)
    assert result['status'] == 'applied'
    with native() as db:
        applied = fetch_intent(db, case)
        assert (applied['intent_id'], applied['child_operation_id'], applied['review_attempt_id'], applied['review_id']) == (accepted['intent_id'], child, case['attempt'], review_id)
        assert db.execute_native('SELECT result_json FROM review_attempt_receipts WHERE attempt_id=%s', (case['attempt'],)).fetchone()[0] == original_review
        receipt = db.execute_native('SELECT state,response_json FROM operation_receipts WHERE operation_id=%s', (child,)).fetchone()
        assert receipt['state'] == 'complete' and json.loads(receipt['response_json']) == result
