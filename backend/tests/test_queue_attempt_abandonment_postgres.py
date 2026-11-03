"""Exact abandonment against the existing isolated PostgreSQL owner fixture."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest

from test_product_game_lapse_postgres import owned_postgres, case, NOW
from tempo_legacy_database.adapters.lifecycle import queue_attempt_abandonment as abandonment
from tempo_legacy_database.adapters.study.issuance import stamp_issued_head
from tempo_legacy_database.adapters.study import position_owner_operator


def _new_attempt(case, *, buried=False):
    day = (NOW + timedelta(days=7)).date().isoformat()
    with case.transaction() as db:
        entry = db.execute_native('''INSERT INTO daily_queue(queue_date,card_id,position,status)
            VALUES(%s,%s,0,%s) RETURNING id''', (day, case.card_id, 'buried' if buried else 'queued')).fetchone()[0]
        stamp_issued_head(db, entry, case.card_id, 1, now=NOW)
    return {'queue_entry_id': entry, 'card_id': case.card_id, 'revision': 1,
            'operation_id': 'abandon-' + case.card_id, 'actor': 'fixture-user', 'reason': 'user abandoned unfinished exercise'}


def _apply_payload(case, plan):
    with case.transaction() as db:
        fingerprint = abandonment.plan_in_writer(db, plan)['origin_sha256']
    return {**plan, 'expected_origin_sha256': fingerprint}


def _review(db, case, payload):
    from app.contracts.transport.study import ReviewRequest
    from app.services.learning_review_adapter import LearningReviewAdapter, command_from_request
    from tempo_learning_review import ReviewPolicy, submit_review
    request = ReviewRequest(outcome='correct', queue_entry_id=payload['queue_entry_id'],
                            expected_revision=1, attempt_id='review-' + case.card_id)
    adapter = LearningReviewAdapter(db, request, observed_at=NOW + timedelta(days=7),
                                    policy_provider=lambda: ReviewPolicy('UTC', 7))
    command = command_from_request(case.card_id, request)
    return submit_review(adapter, command, lambda: adapter.context(command)).as_dict()


def test_native_abandonment_exact_retry_preservation_and_queue_cleanup(case):
    from app.services.study_attempt_guard import guard_current_attempt
    from app.contracts.review_conflicts import ReviewConflict
    plan = _new_attempt(case)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        before = dict(db.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s', (plan['queue_entry_id'],)).fetchone())
        cards = tuple(db.execute_native('SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s', (case.card_id,)).fetchone())
        reviews = db.execute_native('SELECT count(*) FROM reviews WHERE card_id=%s', (case.card_id,)).fetchone()[0]
        assert abandonment.abandon_in_writer(db, payload, now=NOW)['idempotent'] is False
    with case.transaction() as db:
        assert abandonment.abandon_in_writer(db, payload, now=NOW)['idempotent'] is True
        with pytest.raises(ValueError, match='receipt_conflict'):
            abandonment.abandon_in_writer(db, {**payload, 'reason': 'different'}, now=NOW)
        with pytest.raises(ReviewConflict, match='abandoned'):
            guard_current_attempt(db, plan['queue_entry_id'], case.card_id, 1)
        db.execute_native("UPDATE daily_queue SET status='skipped' WHERE id=%s", (plan['queue_entry_id'],))
        db.execute_native('DELETE FROM daily_queue WHERE id=%s', (plan['queue_entry_id'],))
        assert dict(db.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s', (plan['queue_entry_id'],)).fetchone()) == before
        assert tuple(db.execute_native('SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s', (case.card_id,)).fetchone()) == cards
        assert db.execute_native('SELECT count(*) FROM reviews WHERE card_id=%s', (case.card_id,)).fetchone()[0] == reviews
        assert db.execute_native('SELECT count(*) FROM study_position_grades').fetchone()[0] == 0


def test_native_changed_wrong_and_outer_rollback(case):
    plan = _new_attempt(case, buried=True)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        with pytest.raises(ValueError, match='origin_changed'):
            abandonment.abandon_in_writer(db, {**payload, 'expected_origin_sha256': '0' * 64}, now=NOW)
        with pytest.raises(ValueError, match='missing_or_ambiguous'):
            abandonment.abandon_in_writer(db, {**payload, 'revision': 2}, now=NOW)
    with pytest.raises(RuntimeError, match='outer rollback'):
        with case.transaction() as db:
            abandonment.abandon_in_writer(db, payload, now=NOW)
            raise RuntimeError('outer rollback')
    with case.transaction() as db:
        assert not abandonment.is_abandoned(db, plan['queue_entry_id'], case.card_id, 1)


@pytest.mark.parametrize('review_first', [True, False])
def test_native_review_and_abandonment_serialize_both_orders(case, monkeypatch, review_first):
    from tempo_legacy_database.adapters import postgres_store
    from app.contracts.review_conflicts import ReviewConflict
    from tempo_legacy_database.adapters.learning_bridge import review as storage
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    plan = _new_attempt(case)
    payload = _apply_payload(case, plan)
    locked, release = Event(), Event()
    original = storage.lock_current_card
    if review_first:
        def hold_review(db, card_id):
            original(db, card_id)
            locked.set()
            assert release.wait(5)
        monkeypatch.setattr(storage, 'lock_current_card', hold_review)
    def review():
        with case.transaction() as db:
            return _review(db, case, payload)
    def retire():
        with case.transaction() as db:
            result = abandonment.abandon_in_writer(db, payload, now=NOW)
            if not review_first:
                locked.set()
                assert release.wait(5)
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(review if review_first else retire)
        assert locked.wait(5)
        second = pool.submit(retire if review_first else review)
        release.set()
        assert first.result(timeout=10)
        with pytest.raises((ValueError, ReviewConflict)):
            second.result(timeout=10)
    with case.transaction() as db:
        if review_first:
            assert not abandonment.is_abandoned(db, plan['queue_entry_id'], case.card_id, 1)
            assert _review(db, case, payload)['persisted']
        else:
            assert abandonment.is_abandoned(db, plan['queue_entry_id'], case.card_id, 1)
            assert db.execute_native('SELECT count(*) FROM review_attempt_receipts WHERE queue_entry_id=%s', (plan['queue_entry_id'],)).fetchone()[0] == 0


def test_native_late_opening_evidence_rejected_before_writes(case):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.services.postgres_opening_evidence import _validate_checkpoint_scope
    plan = _new_attempt(case)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        abandonment.abandon_in_writer(db, payload, now=NOW)
    manifest = SimpleNamespace(card_id=case.card_id, card_revision=1)
    request = SimpleNamespace(manifest=manifest, attempt_id='late-' + case.card_id,
        origin_queue_entry_id=plan['queue_entry_id'], queue_entry_id=plan['queue_entry_id'])
    with pytest.raises(HTTPException) as error:
        with case.transaction() as db:
            # Any preceding work in a failed evidence transaction rolls back too.
            db.execute_native('UPDATE cards SET interval_days=interval_days+1 WHERE id=%s', (case.card_id,))
            _validate_checkpoint_scope(db, request, completing_review=False)
    assert error.value.detail['code'] == 'queue_attempt_retired'
    with case.transaction() as db:
        assert tuple(db.execute_native('SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s', (case.card_id,)).fetchone()) == case.initial
        assert db.execute_native('SELECT count(*) FROM opening_evidence_attempts WHERE attempt_id=%s', (request.attempt_id,)).fetchone()[0] == 0


def test_native_other_unfinished_origin_still_holds_frontier(case):
    plan = _new_attempt(case, buried=True)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        second = db.execute_native('''INSERT INTO daily_queue(queue_date,card_id,cycle,position,status)
            VALUES(%s,%s,1,1,'buried') RETURNING id''',
            ((NOW + timedelta(days=7)).date().isoformat(), case.card_id)).fetchone()[0]
        stamp_issued_head(db, second, case.card_id, 1, now=NOW)
        abandonment.abandon_in_writer(db, payload, now=NOW)
        with pytest.raises(position_owner_operator.OperatorHold, match='position_old_issued_attempt_unfinished'):
            position_owner_operator.fence_old_attempt_frontier(db, 'white')
        assert not abandonment.is_abandoned(db, second, case.card_id, 1)


def test_native_exact_orphan_requires_no_repertoire_or_live_projection(case):
    template = _new_attempt(case, buried=True)
    orphan_id = template['queue_entry_id'] + 1000000000
    orphan_card = 'orphan-' + case.card_id
    with case.transaction() as db:
        db.execute_native('''INSERT INTO queue_attempt_origins
            (queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,
             attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type,
             issued_as_head,issued_at,issuance_sequence)
            SELECT %s,%s,revision,queue_date,cycle,admission_kind,NULL,attempt_failed,last_status,
                   review_result_json,legacy,start_fen,moves_json,trained_color,content_type,1,NULL,NULL
            FROM queue_attempt_origins WHERE queue_entry_id=%s''',
            (orphan_id, orphan_card, template['queue_entry_id']))
    plan = {**template, 'queue_entry_id': orphan_id, 'card_id': orphan_card,
            'operation_id': 'orphan-' + template['operation_id']}
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        origin = dict(db.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s', (orphan_id,)).fetchone())
        assert abandonment.abandon_in_writer(db, payload, now=NOW)['abandoned']
        assert dict(db.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s', (orphan_id,)).fetchone()) == origin
        assert abandonment.is_abandoned(db, orphan_id, orphan_card, 1)


def test_native_receipt_and_origin_are_immutable(case):
    import psycopg
    plan = _new_attempt(case, buried=True)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        abandonment.abandon_in_writer(db, payload, now=NOW)
    for sql in ('UPDATE queue_attempt_abandonments SET reason=reason || \'changed\' WHERE queue_entry_id=%s',
                'DELETE FROM queue_attempt_abandonments WHERE queue_entry_id=%s',
                "UPDATE queue_attempt_origins SET last_status='queued' WHERE queue_entry_id=%s",
                'DELETE FROM queue_attempt_origins WHERE queue_entry_id=%s'):
        with pytest.raises(psycopg.errors.CheckViolation):
            with case.transaction() as db:
                db.execute_native(sql, (plan['queue_entry_id'],))
    with case.transaction() as db:
        db.execute_native('UPDATE queue_attempt_origins SET last_status=last_status WHERE queue_entry_id=%s',
                          (plan['queue_entry_id'],))
