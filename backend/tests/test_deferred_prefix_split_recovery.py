"""Pure owner coordination checks; SQL, locks and rollback need native proof."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.services import deferred_prefix_split_recovery as recovery


@pytest.fixture
def harness(monkeypatch):
    events = []
    database = object()
    intent = {"intent_id": "intent", "card_id": "source", "source_revision": 3,
              "queue_entry_id": 17, "state": "waiting_review"}
    facts = {"card": {"id": "source", "revision": 3}, "matches": [], "active": False, "receipt_ok": True}

    @contextmanager
    def connection(*, background):
        assert background
        yield database

    monkeypatch.setattr(recovery, "connection", connection)
    monkeypatch.setattr(recovery.storage, "due_intent", lambda db, after: {"intent_id": "intent", "card_id": "source"})
    monkeypatch.setattr(recovery.storage, "try_lock_source", lambda db, card: ("locked", facts["card"]))
    monkeypatch.setattr(recovery.storage, "intent", lambda db, key, *, lock, skip_locked: intent)
    monkeypatch.setattr(recovery.storage, "matching_reviews", lambda *args: facts["matches"])
    monkeypatch.setattr(recovery.split_storage, "has_active_issued_attempt", lambda *args: facts["active"])
    monkeypatch.setattr(recovery.storage, "delay_check", lambda db, key: events.append(("delay", key)))
    monkeypatch.setattr(recovery.storage, "set_held", lambda db, key, reason: events.append(("held", key, reason)))
    monkeypatch.setattr(recovery.receipt_storage, "enqueue_foreground_operation", lambda db, operation, command, digest, payload:
                        events.append(("enqueue", operation, command, payload)) or facts["receipt_ok"])
    monkeypatch.setattr(recovery.storage, "set_ready", lambda db, key, attempt, review, operation:
                        events.append(("ready", key, attempt, review, operation)) or True)
    return facts, events, intent


def review(attempt="original", result='{"persisted":true,"review_id":41}'):
    return {"attempt_id": attempt, "review_id": 41, "result_json": result}


def test_waiting_requires_real_receipt_and_never_publishes_an_active_attempt(harness):
    facts, events, _ = harness
    assert recovery.enqueue_one_ready_split() is False
    assert events == [("delay", "intent")]
    events.clear(); facts["matches"] = [review()]; facts["active"] = True
    assert recovery.enqueue_one_ready_split() is False
    assert events == [("delay", "intent")]


def test_exact_review_enqueues_one_stable_foreground_child_before_ready(harness):
    facts, events, _ = harness
    facts["matches"] = [review()]
    assert recovery.enqueue_one_ready_split() is True
    assert events == [
        ("enqueue", "prefix-split-intent:intent", "cards.prefix_split.apply_deferred", {"intent_id": "intent"}),
        ("ready", "intent", "original", 41, "prefix-split-intent:intent"),
    ]


@pytest.mark.parametrize("matches", [[review(), review("other")], [review(result='{"persisted":true,"review_id":null}')]])
def test_ambiguous_or_nonmatching_receipt_is_held_without_a_child(harness, matches):
    facts, events, _ = harness; facts["matches"] = matches
    assert recovery.enqueue_one_ready_split() is True
    assert events == [("held", "intent", "original_review_ambiguous")]


def test_changed_source_or_conflicting_child_is_held(harness):
    facts, events, _ = harness; facts["card"]["revision"] = 4
    assert recovery.enqueue_one_ready_split() is True
    assert events == [("held", "intent", "source_changed")]
    events.clear(); facts["card"]["revision"] = 3; facts["matches"] = [review()]; facts["receipt_ok"] = False
    assert recovery.enqueue_one_ready_split() is True
    assert events[-1] == ("held", "intent", "child_identity_conflict")
    assert not any(event[0] == "ready" for event in events)


@pytest.mark.parametrize("state", ["failed", "blocked", "complete"])
def test_terminal_child_without_atomic_intent_result_releases_issuance_into_truthful_hold(harness, monkeypatch, state):
    _, events, intent = harness
    intent.update(state="ready", child_operation_id="prefix-split-intent:intent")
    monkeypatch.setattr(recovery.receipt_storage, "foreground_operation_state", lambda *args:
                        {"command_name": "cards.prefix_split.apply_deferred", "state": state, "error_json": None, "last_error_json": None})
    assert recovery.enqueue_one_ready_split() is True
    assert events == [("held", "intent", "child_" + state)]


def test_essential_recovery_dispatches_existing_operation_before_one_intent_check(monkeypatch):
    from contextlib import nullcontext
    from app import tasks
    events = []
    operation = {"operation_id": "existing", "command_name": "cards.review", "payload": {"original": True}, "background": False}
    monkeypatch.setattr(tasks.activity_gate, "background_job", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(tasks.activity_gate, "background_control", lambda: nullcontext())
    monkeypatch.setattr(tasks, "study_only_enabled", lambda: True)
    monkeypatch.setattr(tasks, "claim_recoverable_operation", lambda **kw: operation)
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *a, **kw: events.append(("dispatch", kw["task_id"])))
    monkeypatch.setattr(recovery, "enqueue_one_ready_split", lambda published: events.append(("scan",)) or True)
    assert tasks.recover_study_operations.run() is True
    assert events == [("dispatch", "existing"), ("scan",)]


def test_raced_issued_attempt_defers_the_same_child_without_publishing(monkeypatch):
    from app import prefix_split_commands as commands
    from app.command_gateway import CommandPrerequisitePending
    intent = {"intent_id": "intent", "card_id": "source", "source_revision": 3, "queue_entry_id": 17,
              "state": "ready", "review_attempt_id": "original", "review_id": 41}
    monkeypatch.setattr(commands.deferred_storage, "intent", lambda *a, **kw: intent)
    monkeypatch.setattr(commands, "_lock_prefix_card", lambda *a: None)
    monkeypatch.setattr(commands.deferred_storage, "matching_reviews", lambda *a: [review()])
    monkeypatch.setattr(commands.split_storage, "has_active_issued_attempt", lambda *a: True)
    monkeypatch.setattr(commands, "_apply_split", lambda *a: pytest.fail("must not apply while another attempt is active"))
    monkeypatch.setattr(commands.deferred_storage, "set_held", lambda *a: pytest.fail("recoverable prerequisite must not become terminal"))
    with pytest.raises(CommandPrerequisitePending, match="issued attempt"):
        commands.apply_deferred_prefix_split(object(), {"intent_id": "intent"})


def test_gateway_preserves_same_operation_when_prerequisite_is_pending(monkeypatch):
    from contextlib import nullcontext
    from app import command_gateway as gateway
    events = []
    payload = {"intent_id": "intent"}
    command = "test.deferred-prerequisite"
    monkeypatch.setattr(gateway, "_writer_connection", lambda background: nullcontext(object()))
    monkeypatch.setattr(gateway.storage, "receipt_writer", lambda db: db)
    monkeypatch.setattr(gateway.storage, "lock_operation", lambda db, op: events.append(("lock", op)))
    monkeypatch.setattr(gateway.storage, "insert_pending", lambda *args: None)
    monkeypatch.setattr(gateway.storage, "locked_publication", lambda *args:
                        (command, gateway.request_digest(command, payload), "executing", None, None, "token"))
    monkeypatch.setattr(gateway.storage, "begin_handler", lambda db: events.append(("savepoint",)))
    monkeypatch.setattr(gateway.storage, "rollback_handler", lambda db: events.append(("rollback",)))
    monkeypatch.setattr(gateway.storage, "release_handler", lambda db: events.append(("release",)))
    monkeypatch.setattr(gateway.storage, "defer_prerequisite", lambda db, op, message: events.append(("defer", op, message)))
    monkeypatch.setattr(gateway.storage, "fail_operation", lambda *args: pytest.fail("must not create a failed receipt"))
    def handler(db, value):
        assert value == payload
        raise gateway.CommandPrerequisitePending("original attempt remains active")
    monkeypatch.setitem(gateway._handlers, command, handler)
    assert gateway.execute_command("same-child", command, payload, attempt_token="token") is None
    assert events == [("lock", "same-child"), ("savepoint",), ("rollback",), ("release",),
                      ("defer", "same-child", "original attempt remains active")]


def test_locked_first_intent_does_not_starve_later_due_intent(harness, monkeypatch):
    facts, events, _ = harness
    facts['matches'] = [review()]
    candidates = {'': {'intent_id': 'a', 'card_id': 'busy'}, 'a': {'intent_id': 'intent', 'card_id': 'source'}}
    monkeypatch.setattr(recovery, '_scan_cursor', '')
    monkeypatch.setattr(recovery.storage, 'due_intent', lambda db, after: candidates.get(after))
    monkeypatch.setattr(recovery.storage, 'try_lock_source', lambda db, card: ('busy', None) if card == 'busy' else ('locked', facts['card']))
    monkeypatch.setattr(recovery.storage, 'delay_check_if_unlocked', lambda *args: None)
    assert recovery.enqueue_one_ready_split() is False
    assert recovery.enqueue_one_ready_split() is True
    assert events[-1] == ('ready', 'intent', 'original', 41, 'prefix-split-intent:intent')


def test_new_child_wake_occurs_after_owner_commit_and_keeps_exact_identity(harness, monkeypatch):
    from contextlib import nullcontext
    from app import tasks
    facts, events, _ = harness
    facts['matches'] = [review()]
    @contextmanager
    def connection(*, background):
        yield object()
        events.append(('committed',))
    monkeypatch.setattr(recovery, 'connection', connection)
    monkeypatch.setattr(tasks.activity_gate, 'background_job', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(tasks.activity_gate, 'background_control', lambda: nullcontext())
    monkeypatch.setattr(tasks, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(tasks, 'claim_recoverable_operation', lambda **kw: None)
    monkeypatch.setattr(tasks.celery_app, 'connection_for_write', lambda **kw: nullcontext(object()))
    def publish(name, **kwargs):
        assert events[-1] == ('committed',)
        events.append(('published', name, kwargs))
    monkeypatch.setattr(tasks.celery_app, 'send_task', publish)
    assert tasks.recover_study_operations.run()
    _, name, sent = events[-1]
    assert name == 'app.tasks.execute_foreground_command'
    assert sent['task_id'] == 'prefix-split-intent:intent' and sent['queue'] == 'foreground'
    assert sent['args'] == ['prefix-split-intent:intent', 'cards.prefix_split.apply_deferred', {'intent_id':'intent'}]
    assert sent['retry'] is False


def test_rolled_back_child_never_wakes_and_broker_failure_does_not_undo_commit(harness, monkeypatch):
    from contextlib import nullcontext
    from app import tasks
    facts, events, _ = harness
    facts['matches'] = [review()]
    @contextmanager
    def failed_commit(*, background):
        yield object()
        raise RuntimeError('commit failed')
    monkeypatch.setattr(tasks.activity_gate, 'background_job', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(tasks.activity_gate, 'background_control', lambda: nullcontext())
    monkeypatch.setattr(tasks, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(tasks, 'claim_recoverable_operation', lambda **kw: None)
    monkeypatch.setattr(recovery, 'connection', failed_commit)
    monkeypatch.setattr(tasks.celery_app, 'connection_for_write', lambda **kw: pytest.fail('must not wake before commit'))
    with pytest.raises(RuntimeError, match='commit failed'):
        tasks.recover_study_operations.run()
    @contextmanager
    def successful_commit(*, background):
        yield object()
        events.append(('committed',))
    monkeypatch.setattr(recovery, 'connection', successful_commit)
    def failed_broker(**kwargs):
        assert events[-1] == ('committed',)
        raise OSError('broker unavailable')
    monkeypatch.setattr(tasks.celery_app, 'connection_for_write', failed_broker)
    assert tasks.recover_study_operations.run()
    assert events[-1] == ('committed',)
