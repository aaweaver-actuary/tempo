"""Exact abandonment against the existing isolated PostgreSQL owner fixture."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest

from test_product_game_lapse_postgres import owned_postgres, case, NOW
from tempo_legacy_database.adapters.lifecycle import queue_attempt_abandonment as abandonment
from tempo_legacy_database.adapters.study.issuance import stamp_issued_head
from tempo_legacy_database.adapters.study import position_owner_operator
from tempo_legacy_database.adapters.study.position_owner_epoch import owner_lock_key


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
        position_grades = db.execute_native('SELECT count(*) FROM study_position_grades').fetchone()[0]
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
        assert db.execute_native('SELECT count(*) FROM study_position_grades').fetchone()[0] == position_grades


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
        first_result = first.result(timeout=10)
        assert first_result
        with pytest.raises((ValueError, ReviewConflict)):
            second.result(timeout=10)
    with case.transaction() as db:
        if review_first:
            assert not abandonment.is_abandoned(db, plan['queue_entry_id'], case.card_id, 1)
            before_receipt = tuple(db.execute_native(
                'SELECT * FROM review_attempt_receipts WHERE attempt_id=%s', ('review-' + case.card_id,)).fetchone())
            before_schedule = tuple(db.execute_native(
                'SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s', (case.card_id,)).fetchone())
            before_count = db.execute_native('SELECT count(*) FROM reviews WHERE card_id=%s', (case.card_id,)).fetchone()[0]
            before_position_grades = db.execute_native('SELECT count(*) FROM study_position_grades').fetchone()[0]
            before_queue = [tuple(row) for row in db.execute_native('SELECT * FROM daily_queue WHERE card_id=%s ORDER BY id', (case.card_id,)).fetchall()]
            replay = _review(db, case, payload)
            assert replay == first_result and replay['persisted'] and replay['review_id'] == first_result['review_id']
            assert replay['idempotent'] is False  # Canonical receipt replay returns the saved wire result.
            assert tuple(db.execute_native('SELECT * FROM review_attempt_receipts WHERE attempt_id=%s', ('review-' + case.card_id,)).fetchone()) == before_receipt
            assert tuple(db.execute_native('SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s', (case.card_id,)).fetchone()) == before_schedule
            assert db.execute_native('SELECT count(*) FROM reviews WHERE card_id=%s', (case.card_id,)).fetchone()[0] == before_count
            assert db.execute_native('SELECT count(*) FROM study_position_grades').fetchone()[0] == before_position_grades
            assert [tuple(row) for row in db.execute_native('SELECT * FROM daily_queue WHERE card_id=%s ORDER BY id', (case.card_id,)).fetchall()] == before_queue
            assert not abandonment.is_abandoned(db, plan['queue_entry_id'], case.card_id, 1)
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
        assert db.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (owner_lock_key('white'),)).fetchone()[0]
        with pytest.raises(position_owner_operator.OperatorHold, match='position_old_issued_attempt_unfinished'):
            position_owner_operator.fence_old_attempt_frontier(db, 'white')
        assert not abandonment.is_abandoned(db, second, case.card_id, 1)


def test_native_exact_orphan_requires_no_repertoire_or_live_projection(case):
    import hashlib
    import json
    import chess

    moves = ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1c4', 'g8f6', 'd2d3', 'f8c5', 'c2c3']
    board = chess.Board()
    for move in moves:
        board.push_uci(move)
    orphan_card = 'orphan-' + case.card_id
    scope = 'orphan-scope-' + case.card_id
    with case.transaction() as db:
        db.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Synthetic','fixture',%s)", (scope, NOW.isoformat()))
        orphan_id = db.execute_native("SELECT nextval(pg_get_serial_sequence('daily_queue','id'))").fetchone()[0]
        db.execute_native('''INSERT INTO queue_attempt_origins
            (queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,
             attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type,
             issued_as_head,issued_at,issuance_sequence)
            VALUES(%s,%s,1,'2026-10-12',0,NULL,NULL,1,'buried',NULL,0,%s,%s,NULL,'opening',1,NULL,NULL)''',
            (orphan_id, orphan_card, chess.STARTING_FEN, json.dumps(moves)))
        origin = dict(db.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s', (orphan_id,)).fetchone())
        assert (origin['queue_date'], origin['cycle'], origin['admission_kind'], origin['admission_repertoire_id'],
                origin['attempt_failed'], origin['last_status'], origin['review_result_json'], origin['legacy'],
                origin['trained_color'], origin['content_type'], origin['issued_as_head'], origin['issued_at'],
                origin['issuance_sequence']) == ('2026-10-12', 0, None, None, 1, 'buried', None, 0, None, 'opening', 1, None, None)
        assert len(json.loads(origin['moves_json'])) == 9 and origin['start_fen']
        for table, column, value in [('cards','id',orphan_card), ('repertoire_cards','card_id',orphan_card),
                                     ('daily_queue','id',orphan_id), ('opening_evidence_presentations','card_id',orphan_card),
                                     ('opening_evidence_queue_contexts','queue_entry_id',orphan_id)]:
            assert db.execute_native('SELECT count(*) FROM ' + table + ' WHERE ' + column + '=%s', (value,)).fetchone()[0] == 0
        assert db.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (owner_lock_key(scope),)).fetchone()[0]
        with pytest.raises(position_owner_operator.OperatorHold, match='position_old_issued_attempt_unfinished'):
            position_owner_operator.fence_old_attempt_frontier(db, scope)
    plan = {'queue_entry_id': orphan_id, 'card_id': orphan_card, 'revision': 1,
            'operation_id': 'orphan-' + case.card_id, 'actor': 'fixture-user', 'reason': 'user abandoned unfinished exercise'}
    payload = _apply_payload(case, plan)
    original_hash = hashlib.sha256(json.dumps(origin, sort_keys=True, default=str).encode()).hexdigest()
    with case.transaction() as db:
        assert abandonment.abandon_in_writer(db, payload, now=NOW)['abandoned']
    with case.transaction() as db:
        current = dict(db.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s', (orphan_id,)).fetchone())
        assert current == origin and current['attempt_failed'] == 1
        assert hashlib.sha256(json.dumps(current, sort_keys=True, default=str).encode()).hexdigest() == original_hash
        assert abandonment.is_abandoned(db, orphan_id, orphan_card, 1)
        assert db.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (owner_lock_key(scope),)).fetchone()[0]
        position_owner_operator.fence_old_attempt_frontier(db, scope)
        other_id = db.execute_native("SELECT nextval(pg_get_serial_sequence('daily_queue','id'))").fetchone()[0]
        db.execute_native('''INSERT INTO queue_attempt_origins
            (queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,
             attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type,
             issued_as_head,issued_at,issuance_sequence)
            SELECT %s,%s,revision,queue_date,cycle,admission_kind,admission_repertoire_id,attempt_failed,
                   last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type,
                   issued_as_head,issued_at,issuance_sequence FROM queue_attempt_origins WHERE queue_entry_id=%s''',
            (other_id, 'other-' + orphan_card, orphan_id))
        with pytest.raises(position_owner_operator.OperatorHold, match='position_old_issued_attempt_unfinished'):
            position_owner_operator.fence_old_attempt_frontier(db, scope)
        assert not abandonment.is_abandoned(db, other_id, 'other-' + orphan_card, 1)
        db.execute_native('DELETE FROM queue_attempt_origins WHERE queue_entry_id=%s', (other_id,))


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



def test_native_late_failure_marker_conflict_rolls_back_prior_work(case, monkeypatch):
    from app.contracts.review_conflicts import ReviewConflict
    from app.queue_commands import mark_attempt_failed
    from tempo_legacy_database.adapters import postgres_store
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    plan = _new_attempt(case)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        abandonment.abandon_in_writer(db, payload, now=NOW)
        original = dict(db.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s',
                                           (plan['queue_entry_id'],)).fetchone())
        queue = tuple(db.execute_native('SELECT * FROM daily_queue WHERE id=%s', (plan['queue_entry_id'],)).fetchone())
    with pytest.raises(ReviewConflict) as error:
        with case.transaction() as db:
            db.execute_native('UPDATE cards SET interval_days=interval_days+1 WHERE id=%s', (case.card_id,))
            mark_attempt_failed(db, {'entry_id': plan['queue_entry_id'], 'card_id': case.card_id,
                                     'expected_revision': 1})
    assert error.value.code == 'queue_attempt_retired'
    with case.transaction() as db:
        assert tuple(db.execute_native('SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s', (case.card_id,)).fetchone()) == case.initial
        assert tuple(db.execute_native('SELECT * FROM daily_queue WHERE id=%s', (plan['queue_entry_id'],)).fetchone()) == queue
        assert dict(db.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s', (plan['queue_entry_id'],)).fetchone()) == original


def test_native_schema56_without_abandonment_table(owned_postgres):
    """Apply 001–056 in a fresh child database on the already-owned cluster."""
    import hashlib
    import importlib.util
    from pathlib import Path
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('abandonment_test_migrations', root / 'database/tooling/apply_postgres_migrations.py')
    migrations = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrations)
    options = dict(owned_postgres.connection_options)
    assert options['host'] == '127.0.0.1' and options['dbname'].startswith('tempo_game_feedback_')
    child = 'tempo_abandonment_56_' + uuid4().hex
    with psycopg.connect(**options, autocommit=True) as admin:
        assert admin.execute('SELECT current_database()').fetchone()[0] == options['dbname']
        assert admin.execute('SELECT 1 FROM pg_database WHERE datname=%s', (child,)).fetchone() is None
        admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(child)))
        try:
            child_options = {**options, 'dbname': child}
            with psycopg.connect(**child_options, row_factory=tempo_row_factory) as raw:
                assert raw.execute('SELECT current_database()').fetchone()[0] == child
                assert raw.execute("SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','S')").fetchone()[0] == 0
                paths = sorted(migrations.MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
                for path in paths:
                    if int(path.name[:3]) > 56:
                        break
                    registration = migrations.MODULE_MIGRATIONS.get(path.name)
                    if registration:
                        source = (root / 'database' / registration[2]).read_bytes()
                        assert hashlib.sha256(source).hexdigest() == registration[3]
                        assert '-- sha256: ' + registration[3] in path.read_text()
                        raw.execute(source.decode(), prepare=False)
                    raw.execute(path.read_text(), prepare=False)
                assert [row[0] for row in raw.execute('SELECT version FROM tempo_schema_migrations ORDER BY version').fetchall()] == list(range(1, 57))
                database = PostgresConnection(raw)
                assert not abandonment.installed(database)
                assert not abandonment.is_abandoned(database, 13624, 'unknown', 1)
                with pytest.raises(ValueError, match='schema_unavailable'):
                    abandonment.plan_in_writer(database, {'queue_entry_id': 13624, 'card_id': 'unknown',
                        'revision': 1, 'operation_id': 'test', 'actor': 'test', 'reason': 'test'})
        finally:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(child)))
