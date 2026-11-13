"""Prepared native cases for a parent-provisioned, disposable fixture only.

Collection is safe without infrastructure. Execution requires the existing
Learning runner's controlled fixture environment; this module provisions nothing.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
import os
from threading import Barrier
import uuid

import pytest

from tempo_storage import postgres
from tempo_storage.queue_position_lock import lock_queue_date_for_position
from app.services import queue_admission, queue_application
from app.services.queue_admission_adapter import QueueAdmissionAdapter, _PRIORITY_OPENING_CANDIDATE_BODY
from app.study.openings.capacity_frontier import plan_prioritized_opening_admissions
from queue_fixture_guard import owned_target, verify_owned_target

FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'


@pytest.fixture
def fixture_database():
    # Validate all DSNs against the parent's exact target receipt before even
    # opening the read-only identity probe. No configured-pool write can precede
    # the current_database/cluster/OID check or reuse an earlier pool.
    target = owned_target(os.environ)
    postgres.close_pools()
    verify_owned_target(target)
    if owned_target(os.environ) != target:
        raise RuntimeError('Fixture target changed during identity verification')
    try:
        yield target
    finally:
        postgres.close_pools()


@pytest.fixture
def case(fixture_database):
    identity, day = 'queue-port-' + uuid.uuid4().hex, date.today().isoformat()
    with postgres.connection() as database:
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(%s,%s,'fixture',%s,1)", (identity, identity, day))
        for suffix in ('a', 'b', 'head'):
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,content_type) VALUES(%s,%s,'prefix',%s,'[\"e2e4\"]','white','new',%s,'opening')", (identity+'-'+suffix, identity, FEN, day))
            database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (identity, identity+'-'+suffix))
        database.execute("UPDATE cards SET state='learning',introduced_at=%s,due_date=%s WHERE id=%s",
                         ((date.today()-timedelta(days=1)).isoformat(), (date.today()+timedelta(days=7)).isoformat(), identity+'-head'))
        head = database.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,0) RETURNING id', (day, identity+'-head')).fetchone()[0]
        database.execute('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s', (head,))
    yield dict(identity=identity, day=day, head=head)


def lock_candidates(database, case):
    # Match the established card-before-day order. The application/adapter do
    # not invent a competing lock or own the caller's transaction.
    for suffix in ('a', 'b'):
        database.execute('SELECT id FROM cards WHERE id=%s FOR UPDATE', (case['identity']+'-'+suffix,))
    lock_queue_date_for_position(database, case['day'])


def test_native_adapter_failure_rolls_back_card_queue_and_unissued_origin(case, monkeypatch):
    observed = []
    with pytest.raises(RuntimeError, match='after native admission effects'):
        with postgres.connection() as database:
            lock_candidates(database, case)
            adapter = QueueAdmissionAdapter(database)
            original = adapter.retain_unseen
            def fail(day, card_id):
                original(day, card_id)
                observed.append(database.execute(
                    'SELECT c.state,origin.issued_as_head FROM cards c '
                    'JOIN queue_attempt_origins origin ON origin.card_id=c.id WHERE c.id=%s', (card_id,),
                ).fetchone())
                raise RuntimeError('after native admission effects')
            monkeypatch.setattr(adapter, 'retain_unseen', fail)
            queue_admission.admit_prioritized_opening_cards(adapter, case['day'], {case['identity']: 1}, 0)
    assert [tuple(row) for row in observed] == [('learning', 0)]
    with postgres.connection(read_only=True) as database:
        assert tuple(database.execute('SELECT state,introduced_at FROM cards WHERE id=%s', (case['identity']+'-a',)).fetchone()) == ('new', None)
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id IN (%s,%s)', (case['identity']+'-a', case['identity']+'-b')).fetchone()[0] == 0
        assert database.execute('SELECT COUNT(*) FROM queue_attempt_origins WHERE card_id IN (%s,%s)', (case['identity']+'-a', case['identity']+'-b')).fetchone()[0] == 0


def test_native_existing_card_day_locks_serialize_admission_without_moving_issued_head(case, monkeypatch):
    monkeypatch.setenv('OPENING_CAPACITY_FRONTIER_EXPANSION', '0')
    # Characterize enumeration on the actual engine before asserting parity.
    # The legacy complete query has no ORDER BY; keyset reads must not silently
    # change which repertoire consumes a globally shared card's allowance.
    identity, day = case['identity'], case['day']
    with postgres.connection() as database:
        for repertoire_id in (identity+'-owner-z', identity+'-owner-a'):
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'fixture',%s)", (repertoire_id, repertoire_id, day))
        rows = [(identity+'-cross-z-shared', identity+'-owner-z', '["e2e4"]', 'new'),
                (identity+'-cross-a-private', identity+'-owner-a', '["e2e4","e7e5","g1f3"]', 'new'),
                *[(identity+f'-cross-m-rejected-{index:02}', identity+'-owner-z', '["e2e4"]', 'locked') for index in range(18)]]
        for card_id, repertoire_id, moves, state in rows:
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES(%s,%s,'prefix',%s,%s,%s,%s)", (card_id, repertoire_id, FEN, moves, state, day))
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (identity+'-owner-a', identity+'-cross-z-shared'))
        adapter = QueueAdmissionAdapter(database)
        legacy_rows = database.execute(
            f'WITH active_miss AS ({queue_application.outstanding_miss_query(database)}) '
            + _PRIORITY_OPENING_CANDIDATE_BODY,
            (adapter.miss_reason, day, day, day, day),
        ).fetchall()
        limits = {identity:1, identity+'-owner-a':1, identity+'-owner-z':1}
        legacy = plan_prioritized_opening_admissions(adapter.annotate_openings(legacy_rows),
            queue_admission.read_introduction_counts(adapter, day), day, limits, miss_reason=adapter.miss_reason)
        paged = queue_admission.plan_opening_admissions(adapter, day, limits)
        assert sorted((rep, row['id']) for rep, row in paged) == sorted((rep, row['id']) for rep, row in legacy)
    barrier = Barrier(2)
    def admit():
        barrier.wait(timeout=10)
        with postgres.connection() as database:
            lock_candidates(database, case)
            maximum = database.execute('SELECT MAX(position) FROM daily_queue WHERE queue_date=%s', (case['day'],)).fetchone()[0]
            return queue_admission.admit_prioritized_opening_cards(QueueAdmissionAdapter(database), case['day'], {case['identity']: 1}, maximum)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=20) for future in [executor.submit(admit), executor.submit(admit)]]
    assert len(set(results)) == 1 and results[0] > 0
    with postgres.connection(read_only=True) as database:
        assert QueueAdmissionAdapter(database).introduced_count(case['day'], case['identity']) == 1
        assert database.execute('SELECT position FROM daily_queue WHERE id=%s', (case['head'],)).fetchone()[0] == 0
        assert [tuple(row) for row in database.execute('SELECT card_id,issued_as_head FROM queue_attempt_origins WHERE card_id IN (%s,%s) ORDER BY card_id', (case['identity']+'-a', case['identity']+'-head'))] == [(case['identity']+'-a', 0), (case['identity']+'-head', 1)]


def test_native_capacity_witness_is_rechecked_after_publication_change(case, monkeypatch):
    monkeypatch.setenv('OPENING_CAPACITY_FRONTIER_EXPANSION', '1')
    identity, day = case['identity'], case['day']
    with postgres.connection() as database:
        database.execute('UPDATE settings SET study_only_mode=0 WHERE id=1')
        database.execute("UPDATE cards SET state='learning',introduced_at=%s WHERE id=%s", (day, identity+'-a'))
        database.execute("UPDATE cards SET state='locked' WHERE id=%s", (identity+'-b',))
        database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,'Route','white',%s,'[\"e2e4\",\"e7e5\",\"g1f3\"]',%s)", (identity+'-line', identity, FEN, day))
        database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)", (identity, day))
        for index, suffix in enumerate(('a', 'b')):
            database.execute('INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) VALUES(%s,1,%s,%s,%s,%s,%s,%s,%s,\'white\',%s,%s,%s,%s)',
                (identity, identity+'-line', index, identity+'-'+suffix, identity+'-a' if index else None,
                 FEN, FEN, json.dumps(['e2e4'] if not index else ['g1f3']),
                 'decision' if index else 'prefix', index, index, json.dumps([FEN])))
        lock_candidates(database, case)
        adapter = QueueAdmissionAdapter(database)
        routes = adapter.capacity_routes(identity, ())
        assert len(routes) == 1 and routes[0][0] == identity+'-b'
        from dataclasses import asdict
        proposal = dict(card_id=identity+'-b', repertoire_id=identity, reason=None, capacity_route=asdict(routes[0][1]))
        assert adapter.capacity_route_valid(proposal, day)
        database.execute('UPDATE opening_graph_publications SET generation=2 WHERE repertoire_id=%s', (identity,))
        assert not adapter.capacity_route_valid(proposal, day)
        assert not database.execute('SELECT 1 FROM daily_queue WHERE card_id=%s', (identity+'-b',)).fetchone()


def test_native_requeue_retains_historical_origin_cycle_and_caller_rollback(case):
    card_id, day = case['identity']+'-head', case['day']
    with postgres.connection() as database:
        database.execute('SELECT id FROM cards WHERE id=%s FOR UPDATE', (card_id,))
        lock_queue_date_for_position(database, day)
        database.execute('DELETE FROM daily_queue WHERE id=%s', (case['head'],))
        first = queue_application.requeue(database, day, card_id, None, 'failed')
        second = queue_application.requeue(database, day, card_id, None, 'fresh')
        assert tuple(database.execute('SELECT cycle,admission_kind FROM daily_queue WHERE id=%s', (second,)).fetchone()) == (2, 'review')
        # Native transaction control belongs to the native fixture, not the
        # legacy SQLite-statement translator (which rewrites SAVEPOINT as an alias).
        database.execute_native('SAVEPOINT caller_repeat')
        rolled_back = queue_application.requeue(database, day, card_id, None, 'fresh')
        database.execute_native('ROLLBACK TO SAVEPOINT caller_repeat')
        database.execute_native('RELEASE SAVEPOINT caller_repeat')
        assert not database.execute('SELECT 1 FROM queue_attempt_origins WHERE queue_entry_id=%s', (rolled_back,)).fetchone()
        assert [tuple(row) for row in database.execute('SELECT queue_entry_id,issued_as_head FROM queue_attempt_origins WHERE card_id=%s ORDER BY cycle', (card_id,))] == [(case['head'], 1), (first, 0), (second, 0)]


def test_active_slice_admission_rolls_back_queue_card_and_origin(case, monkeypatch):
    """The phased owner retains the legacy atomicity assertion on its own writer."""
    from app.services import postgres_queue_refresh as refresh
    from tempo_storage.queue_refresh import admissions
    identity, day = case['identity'], case['day']
    planned = {'card_id': identity+'-a', 'repertoire_id': identity, 'reason': None}
    observed = []
    original = admissions.keep_unseen_introduction
    def fail_after_admission(database, queue_date, card_id):
        original(database, queue_date, card_id)
        observed.append(tuple(database.execute(
            'SELECT c.state, origin.issued_as_head FROM cards c '
            'JOIN queue_attempt_origins origin ON origin.card_id=c.id WHERE c.id=%s', (card_id,),
        ).fetchone()))
        raise RuntimeError('after active admission effects')
    with pytest.raises(RuntimeError, match='after active admission effects'):
        with postgres.connection() as database, monkeypatch.context() as patch:
            patch.setattr(admissions, 'keep_unseen_introduction', fail_after_admission)
            refresh._admit_one_prioritized_opening(database, day, planned)
    assert observed == [('learning', 0)]
    with postgres.connection(read_only=True) as database:
        assert tuple(database.execute('SELECT state,introduced_at FROM cards WHERE id=%s', (identity+'-a',)).fetchone()) == ('new', None)
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=%s', (identity+'-a',)).fetchone()[0] == 0
        assert database.execute('SELECT COUNT(*) FROM queue_attempt_origins WHERE card_id=%s', (identity+'-a',)).fetchone()[0] == 0
        assert tuple(database.execute('SELECT position,issued_as_head FROM daily_queue JOIN queue_attempt_origins ON queue_entry_id=daily_queue.id WHERE daily_queue.id=%s', (case['head'],)).fetchone()) == (0, 1)


def test_active_slice_rechecks_limit_and_preserves_issued_head(case):
    from app.services import postgres_queue_refresh as refresh
    identity, day = case['identity'], case['day']
    first = {'card_id': identity+'-a', 'repertoire_id': identity, 'reason': None}
    second = {'card_id': identity+'-b', 'repertoire_id': identity, 'reason': None}
    with postgres.connection() as database:
        assert refresh._admit_one_prioritized_opening(database, day, first) is True
        assert refresh._admit_one_prioritized_opening(database, day, first) is False
        assert refresh._admit_one_prioritized_opening(database, day, second) is False
    with postgres.connection(read_only=True) as database:
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id IN (%s,%s)', (identity+'-a', identity+'-b')).fetchone()[0] == 1
        assert tuple(database.execute('SELECT position,issued_as_head FROM daily_queue JOIN queue_attempt_origins ON queue_entry_id=daily_queue.id WHERE daily_queue.id=%s', (case['head'],)).fetchone()) == (0, 1)
        assert tuple(database.execute('SELECT state,introduced_at FROM cards WHERE id=%s', (identity+'-b',)).fetchone()) == ('new', None)
