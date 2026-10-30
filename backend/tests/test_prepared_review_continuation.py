"""Bounded review handoff contracts; native locking is proved by the Docker suite."""
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tempo_legacy_database import connections as database
from app import main, review_commands
from test_queue_attempt_issuance import queued_attempts


def test_legacy_review_payload_omits_absent_prepared_identity_and_keeps_request_hash():
    from app.command_gateway import request_digest
    from app.contracts.transport.study import ReviewRequest
    legacy = {"outcome": "correct", "guided": False, "queue_entry_id": 12, "recorded_at": None,
              "expected_review_id": None, "expected_revision": 1, "attempt_id": "legacy"}
    request = ReviewRequest.model_validate(legacy)
    assert request.durable_payload() == legacy
    assert request_digest("cards.review", {"card_id": "a", "review": request.durable_payload()}) == request_digest("cards.review", {"card_id": "a", "review": legacy})


class CommandConnection:
    def __init__(self, connection):
        self.connection = connection
        self.card_locks = []

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace(" FOR UPDATE", ""), parameters)

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute_native(self, statement, parameters=()):
        if "pg_advisory" in statement:
            self.card_locks.append(parameters)
            return self.connection.execute("SELECT 1")
        return self.execute(statement.replace("%s", "?"), parameters)


def payload(entries, **candidate_changes):
    candidate = {"queue_entry_id": entries["later-b"], "card_id": "later-b", "revision": 1,
                 "cycle": 0, "local_date": date.today().isoformat(), **candidate_changes}
    return {"card_id": "active-a", "review": {"outcome": "correct", "guided": False,
            "queue_entry_id": entries["active-a"], "expected_revision": 1,
            "attempt_id": "prepared-transition-a", "prepared_next": candidate}}


def test_prepared_continuation_replay_preserves_exactly_once_scheduling(queued_attempts, monkeypatch):
    monkeypatch.setattr(main.coordinator, "wake", lambda: None)
    with database.connection() as connection:
        command = CommandConnection(connection)
        result = review_commands.submit_review(command, payload(queued_attempts))
        assert result["persisted"] is True
        assert result["prepared_continuation"] == {**payload(queued_attempts)["review"]["prepared_next"], "attempt_failed": False}
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE card_id='later-b'").fetchone()[0] == 1
        replayed = review_commands.submit_review(command, payload(queued_attempts))
        assert replayed == result
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id='active-a'").fetchone()[0] == 1


def test_ready_local_feedback_declines_ordinary_successor_without_rolling_back_review(queued_attempts, monkeypatch):
    from app.services import queue_next_card
    monkeypatch.setattr(main.coordinator, 'wake', lambda: None)
    monkeypatch.setattr(queue_next_card, 'pending_next_card_preparation', lambda *_args,**_kwargs: True)
    monkeypatch.setattr(queue_next_card, '_prepare', lambda: (_ for _ in ()).throw(
        AssertionError('Review/issuance called optional preparation inside its writer')))
    with database.connection() as connection:
        accepted = review_commands.submit_review(CommandConnection(connection), payload(queued_attempts))
        assert accepted['persisted'] is True and 'prepared_continuation' not in accepted
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id='active-a'").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM review_attempt_receipts WHERE card_id='active-a'").fetchone()[0] == 1
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE card_id='later-b'").fetchone()[0] == 0
        assert review_commands.submit_review(CommandConnection(connection), payload(queued_attempts)) == accepted


def test_next_card_preparation_is_an_explicit_command_not_a_queue_read(queued_attempts, monkeypatch):
    from app.services import queue_next_card
    from app.study.interfaces.next_card import NextCardPreparation, NoNextCardWork
    prepared = []
    monkeypatch.setattr(queue_next_card, '_prepare', lambda: prepared.append(True) or NoNextCardWork(NextCardPreparation(True)))
    client = TestClient(main.app)
    assert client.get('/api/queue/window?limit=2').status_code == 200
    assert prepared == []
    result = client.post('/api/queue/prepare-next')
    assert result.status_code == 200 and result.json() == dict(progressed=True,pending=False,reason=None)
    assert prepared == [True]


def test_prepared_control_precedes_pending_study_work_without_applying_during_preparation(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app.product_composition import next_card_priority as composition
    from app.study import game_feedback
    from app.study.interfaces.next_card import NoNextCardWork
    prepared, study_preparations = {}, []
    def no_apply(_writer):
        raise AssertionError('Preparation applied a control before the operation receipt fence')
    control = SimpleNamespace(priority=None,is_control=True,apply=no_apply)
    observed = SimpleNamespace(key=object(),qualification=None,
        progress=SimpleNamespace(determined_at='2026-01-01T00:00:00Z',stream='forward'))
    @contextmanager
    def read():
        yield object()
    monkeypatch.setattr(composition,'configure_next_card_work',lambda **work:prepared.update(work))
    monkeypatch.setattr(composition,'installed',lambda _db:True)
    monkeypatch.setattr(composition,'feedback_preparation_admitted',lambda _db:True)
    monkeypatch.setattr(composition,'next_feedback_preparation',lambda *_args,**_kwargs:observed)
    monkeypatch.setattr(composition,'PostgresGameFeedbackStore',lambda _db:SimpleNamespace(study_day=lambda _now:date(2026,1,1)))
    monkeypatch.setattr(game_feedback,'effect_id',lambda _key:'accepted-study')
    monkeypatch.setattr(composition,'prepare_feedback_effect',lambda *_args,**_kwargs:study_preparations.append(True) or object())
    composition.register_current_next_card_priority(read=read,local_handoff_prepare=lambda:control)
    assert prepared['prepare']() is control
    assert study_preparations == []
    assert NoNextCardWork().is_control is False


def test_current_queue_day_and_review_day_share_configured_timezone(queued_attempts, monkeypatch):
    from app.queue_commands import current_study_day
    from app.services.learning_review_adapter import LearningReviewAdapter, command_from_request
    from app.contracts.transport.study import ReviewRequest
    now = datetime.fromisoformat('2026-11-01T00:30:00+00:00')
    request = ReviewRequest(outcome='correct',queue_entry_id=queued_attempts['active-a'],expected_revision=1)
    with database.connection() as connection:
        connection.execute("UPDATE settings SET timezone='America/New_York' WHERE id=1")
    with database.connection() as connection:
        adapter = LearningReviewAdapter(connection,request,observed_at=now)
        context = adapter.context(command_from_request('active-a',request))
        assert current_study_day(connection,now=now) == context.local_day.isoformat() == context.review_day.isoformat() == '2026-10-31'


def test_locked_queue_day_is_retained_if_clock_crosses_saved_timezone_midnight(queued_attempts,monkeypatch):
    from types import SimpleNamespace
    from app.services import learning_review_adapter as adapter_module
    from app.contracts.transport.study import ReviewRequest
    with database.connection() as connection:
        connection.execute("UPDATE settings SET timezone='America/New_York' WHERE id=1")
    instants = iter((datetime.fromisoformat('2026-11-01T03:59:59.999900+00:00'),
                     datetime.fromisoformat('2026-11-01T04:00:00.000100+00:00')))
    clock_calls, locked_days = [], []
    def now(_zone):
        clock_calls.append(True)
        return next(instants)
    monkeypatch.setattr(adapter_module,'datetime',SimpleNamespace(now=now))
    monkeypatch.setattr(adapter_module,'lock_queue_date_for_position',lambda _db,day:locked_days.append(day))
    request = ReviewRequest(outcome='correct',queue_entry_id=queued_attempts['active-a'],expected_revision=1)
    with database.connection() as connection:
        adapter = adapter_module.LearningReviewAdapter(connection,request)
        adapter.lock_queue_date()
        context = adapter.context(adapter_module.command_from_request('active-a',request))
        assert locked_days == [context.local_day.isoformat()] == ['2026-10-31']
        assert context.review_day == context.local_day and len(clock_calls) == 1


def test_legacy_receipt_confirmation_retains_authoritative_refresh(queued_attempts, monkeypatch):
    from app.contracts.transport.study import ReviewRequest
    monkeypatch.setattr(main.coordinator, "wake", lambda: None)
    recorded_at = datetime.now(timezone.utc).isoformat()
    with database.connection() as connection:
        main._apply_review("active-a", ReviewRequest(outcome="correct", queue_entry_id=queued_attempts["active-a"],
            recorded_at=recorded_at, expected_revision=1), database=connection)
        request_payload = payload(queued_attempts)
        request_payload["review"]["recorded_at"] = recorded_at
        result = review_commands.submit_review(CommandConnection(connection), request_payload)
        assert result["persisted"] and result["idempotent"]
        assert "prepared_continuation" not in result
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE card_id='later-b'").fetchone()[0] == 0


@pytest.mark.parametrize("invalidation", ["removed", "revision", "cycle", "pending_validation", "superseded", "blocked"])
def test_pr229_invalidated_successor_declines_without_duplicate_review(queued_attempts, monkeypatch, invalidation):
    monkeypatch.setattr(main.coordinator, "wake", lambda: None)
    original_payload = payload(queued_attempts)
    with database.connection() as connection:
        if invalidation == "removed":
            connection.execute("DELETE FROM daily_queue WHERE id=?", (queued_attempts["later-b"],))
        elif invalidation == "revision":
            connection.execute("UPDATE cards SET revision=2 WHERE id='later-b'")
        elif invalidation == "cycle":
            connection.execute("UPDATE daily_queue SET cycle=1 WHERE id=?", (queued_attempts["later-b"],))
        elif invalidation == "pending_validation":
            connection.execute("UPDATE cards SET pending_validation=1 WHERE id='later-b'")
        elif invalidation == "superseded":
            connection.execute("UPDATE cards SET superseded_by='active-a' WHERE id='later-b'")
        else:
            connection.execute("UPDATE daily_queue SET status='blocked' WHERE id=?", (queued_attempts["later-b"],))
        command = CommandConnection(connection)
        accepted = review_commands.submit_review(command, original_payload)
        assert accepted["persisted"] is True
        assert "prepared_continuation" not in accepted
        assert review_commands.submit_review(command, original_payload) == accepted
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id='active-a'").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM review_attempt_receipts WHERE card_id='active-a'").fetchone()[0] == 1
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE card_id='later-b' AND revision=1").fetchone()[0] == 0


def test_pr229_no_hint_receipt_cannot_acquire_continuation_on_replay(queued_attempts, monkeypatch):
    from app.contracts.review_conflicts import ReviewConflict

    monkeypatch.setattr(main.coordinator, "wake", lambda: None)
    original_payload = payload(queued_attempts)
    del original_payload["review"]["prepared_next"]
    with database.connection() as connection:
        command = CommandConnection(connection)
        accepted = review_commands.submit_review(command, original_payload)
        assert accepted["persisted"] is True
        assert "prepared_continuation" not in accepted
        assert review_commands.submit_review(command, original_payload) == accepted
        with pytest.raises(ReviewConflict, match="different result"):
            review_commands.submit_review(command, payload(queued_attempts))
        assert review_commands.submit_review(command, original_payload) == accepted
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id='active-a'").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM review_attempt_receipts WHERE card_id='active-a'").fetchone()[0] == 1
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE card_id='later-b'").fetchone()[0] == 0


@pytest.mark.parametrize("invalidation", ["archived", "revision", "buried", "pending_validation", "deleted"])
def test_pr229_historical_continuation_does_not_authorize_invalidated_mutation(queued_attempts, monkeypatch, invalidation):
    from app.queue_commands import mark_attempt_failed
    from app.contracts.review_conflicts import ReviewConflict
    from app.contracts.transport.study import ReviewRequest

    monkeypatch.setattr(main.coordinator, "wake", lambda: None)
    original_payload = payload(queued_attempts)
    with database.connection() as connection:
        command = CommandConnection(connection)
        accepted = review_commands.submit_review(command, original_payload)
        assert accepted["prepared_continuation"]["card_id"] == "later-b"
        original_receipt = tuple(connection.execute("SELECT request_json,result_json FROM review_attempt_receipts WHERE attempt_id=?",
            (original_payload["review"]["attempt_id"],)).fetchone())
        if invalidation in {"archived", "revision", "pending_validation"}:
            connection.execute(f"UPDATE cards SET {invalidation}=? WHERE id='later-b'", (2 if invalidation == "revision" else 1,))
        elif invalidation == "buried":
            connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?", (queued_attempts["later-b"],))
        else:
            connection.execute("INSERT INTO deleted_cards VALUES('later-b',?)", (date.today().isoformat(),))
        assert review_commands.submit_review(command, original_payload) == accepted
        with pytest.raises(ReviewConflict):
            mark_attempt_failed(command, {"entry_id": queued_attempts["later-b"], "card_id": "later-b", "expected_revision": 1})
        with pytest.raises(ReviewConflict):
            main._apply_review("later-b", ReviewRequest(outcome="correct", queue_entry_id=queued_attempts["later-b"],
                expected_revision=1, attempt_id="invalidated-successor"), database=connection)
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id='later-b'").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id='active-a'").fetchone()[0] == 1
        assert review_commands.submit_review(command, original_payload) == accepted
        assert tuple(connection.execute("SELECT request_json,result_json FROM review_attempt_receipts WHERE attempt_id=?",
            (original_payload["review"]["attempt_id"],)).fetchone()) == original_receipt


@pytest.mark.parametrize("change", ["revision", "cycle", "day", "promotion", "deleted", "archived", "buried", "absent"])
def test_prepared_continuation_requires_current_head_and_matching_identity(queued_attempts, monkeypatch, change):
    monkeypatch.setattr(main.coordinator, "wake", lambda: None)
    request_payload = payload(queued_attempts)
    with database.connection() as connection:
        if change in {"revision", "cycle", "day"}:
            request_payload["review"]["prepared_next"][{"revision": "revision", "cycle": "cycle", "day": "local_date"}[change]] = "1999-01-01" if change == "day" else 2
        elif change == "promotion":
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) SELECT 'promoted-c',repertoire_id,kind,start_fen,moves_json,state,due_date,content_type FROM cards WHERE id='later-b'")
            connection.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'promoted-c',-1)", (date.today().isoformat(),))
        elif change == "deleted":
            connection.execute("INSERT INTO deleted_cards VALUES('later-b',?)", (date.today().isoformat(),))
        elif change == "archived":
            connection.execute("UPDATE cards SET archived=1 WHERE id='later-b'")
        elif change == "buried":
            connection.execute("UPDATE daily_queue SET status='buried' WHERE card_id='later-b'")
        else:
            del request_payload["review"]["prepared_next"]
        result = review_commands.submit_review(CommandConnection(connection), request_payload)
        assert result["persisted"] is True
        assert "prepared_continuation" not in result
        assert connection.execute("SELECT count(*) FROM reviews WHERE card_id='active-a'").fetchone()[0] == 1
        assert connection.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE card_id='later-b'").fetchone()[0] == 0
