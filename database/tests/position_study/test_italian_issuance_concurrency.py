"""Real PostgreSQL receipts and independent processes running the Celery task body.

Each named test requires a newly EMPTY local task-owned tempo_position_test_n2*
database via TEMPO_POSITION_N2_DSN. The existing N2 owner provisions/migrates it;
this module neither creates Docker resources nor targets installed user data.
Processes invoke the actual registered foreground task body directly: this proves
its claim/preparation/publication boundary, not Redis transport/acks or HTTP.
"""
from dataclasses import replace
import multiprocessing as mp
import os
import traceback

import psycopg
from psycopg.conninfo import make_conninfo

from database.tests.position_study.acceptance import _dsn
from database.tests.position_study.italian_acceptance import LINES, _moves, seed_disposable_source
from tempo_evidence_math.position_learning import Hold
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory


TIMEOUT = 45


def _configure(dsn):
    # Only the disposable N2 DSN accepted by _dsn is ever passed here.
    os.environ['TEMPO_DATABASE_READ_URL'] = make_conninfo(dsn, user='tempo_reader')
    os.environ['TEMPO_DATABASE_WRITE_URL'] = make_conninfo(dsn, user='tempo_writer')
    os.environ['TEMPO_TRAINING_TIMINGS'] = '0'
    os.environ.pop('TEMPO_REDIS_URL', None)
    os.environ.pop('TEMPO_FOREGROUND_ACTIVITY_URL', None)
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    from tempo_legacy_database.adapters import postgres_store
    bind_database_infrastructure()
    postgres_store.close_pools()


def _worker(dsn, payload, channel, entered, release, count, *, selected=None, prepared=None):
    """The wrapper only observes preparation; every claim/write remains production code."""
    try:
        _configure(dsn)
        from app import tasks, command_gateway
        from app import queue_commands  # actual queue command registration
        original = command_gateway._preparers['queue.issue_head']
        def observed(value):
            with count.get_lock():
                count.value += 1
            entered.set()  # the worker's durable receipt claim has already committed
            if not release.wait(TIMEOUT):
                raise RuntimeError('native preparation barrier timed out')
            result = original(value)
            if prepared is not None:
                prepared.set()
                if not selected.wait(TIMEOUT):
                    raise RuntimeError('native post-preparation barrier timed out')
            return result
        command_gateway._preparers['queue.issue_head'] = observed
        operation = command_gateway.select_queue_issuance_operation(payload)
        channel.send(('selected', operation, os.getpid()))
        result = tasks.execute_foreground_command.run(operation, 'queue.issue_head', payload)
        receipt = command_gateway.read_operation(operation)
        channel.send(('done', result, receipt['state']))
    except BaseException:
        channel.send(('error', traceback.format_exc()))
    finally:
        from tempo_legacy_database.adapters import postgres_store
        postgres_store.close_pools()
        channel.close()


def _spawn(ctx, dsn, payload, entered, release, count, **kwargs):
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker, args=(dsn, payload, child, entered, release, count), kwargs=kwargs)
    process.start()
    child.close()
    return process, parent


def _receive(channel):
    assert channel.poll(TIMEOUT), 'native worker did not respond'
    message = channel.recv()
    assert message[0] != 'error', message[1]
    return message


def _stop(workers, release, *barriers):
    release.set()
    for barrier in barriers:
        barrier.set()
    for process, channel in workers:
        process.join(TIMEOUT)
        if process.is_alive():
            process.terminate()
            process.join(10)
        channel.close()
        assert not process.is_alive(), 'task-owned worker teardown failed'


def _seed(dsn):
    from app.services.position_queue_admission import prepare_due, admit_due
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        db, source, _, day = seed_disposable_source(raw, dsn,
            (_moves(LINES['knight']), _moves(LINES['giuoco'])), ('knight', 'giuoco'))
        proposal = prepare_due(db, day, {})
        assert isinstance(proposal, dict) and 'candidate' in proposal, proposal
        assert admit_due(db, day, proposal) is None
        raw.commit()
        row = raw.execute("SELECT id,card_id FROM daily_queue WHERE status='queued' ORDER BY position,id LIMIT 1").fetchone()
        return {'queue_date':day, 'entry_id':row['id'], 'card_id':row['card_id'], 'expected_revision':1}, source


def _receipt(dsn, operation):
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        return dict(raw.execute('SELECT * FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone())


def _assert_one_plan(dsn, payload):
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        plans = raw.execute('SELECT * FROM study_position_issued_plans WHERE queue_entry_id=%s', (payload['entry_id'],)).fetchall()
        assert len(plans) == 1
        assert raw.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s', (payload['entry_id'],)).fetchone()[0] == 1
        assert raw.execute('SELECT count(*) FROM study_position_grades').fetchone()[0] == 0
        assert raw.execute('SELECT count(*) FROM reviews').fetchone()[0] == 0
        return dict(plans[0])


def test_real_two_foreground_processes_share_preparation_and_immutable_plan():
    dsn = _dsn()
    payload, source = _seed(dsn)
    ctx = mp.get_context('spawn')
    entered, release, count = ctx.Event(), ctx.Event(), ctx.Value('i', 0)
    workers = []
    try:
        workers.append(_spawn(ctx, dsn, payload, entered, release, count))
        first = _receive(workers[0][1])
        assert first[0] == 'selected'
        assert entered.wait(TIMEOUT), 'first worker never reached preparation'
        receipt = _receipt(dsn, first[1])
        assert receipt['state'] == 'executing' and receipt['attempt_token']
        workers.append(_spawn(ctx, dsn, payload, entered, release, count))
        second = _receive(workers[1][1])
        assert second[0] == 'selected' and second[1] == first[1] and second[2] != first[2]
        assert _receive(workers[1][1]) == ('done', None, 'executing')
        assert count.value == 1
        release.set()
        done = _receive(workers[0][1])
        assert done[0] == 'done' and done[1]['issued'] and done[2] == 'complete', done
        original = _assert_one_plan(dsn, payload)
        assert _receipt(dsn, first[1])['attempt_count'] == 1
        # An actual duplicate envelope after completion remains the same receipt
        # and does not even enter expensive preparation.
        workers.append(_spawn(ctx, dsn, payload, entered, release, count))
        assert _receive(workers[2][1])[1] == first[1]
        assert _receive(workers[2][1]) == ('done', None, 'complete')
        assert count.value == 1
        assert _assert_one_plan(dsn, payload) == original
        # A real incompatible proposal under the saved immutable attempt still
        # conflicts, independently of the delivery single-flight mechanism.
        from app.study.position_learning.issued_plan_contracts import SelectionRequest, IssuedPlan
        from app.study.position_learning.issued_plan_service import prepare_position_issuance
        from tempo_legacy_database.adapters.study.position_issued_plans import issue_in_borrowed_transaction
        import json
        record = original['plan_json']
        plan = IssuedPlan.from_record(json.loads(record) if isinstance(record, str) else record)
        assert not isinstance(plan, Hold), plan
        request = SelectionRequest(plan.target, source.routes,
            tuple(replace(w, due=not w.due) for w in plan.due_positions), (),
            plan.original_card_id, plan.original_card_revision, plan.queue_entry_id, plan.attempt_id)
        incompatible = prepare_position_issuance(source, request)
        assert not isinstance(incompatible, Hold), incompatible
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
            raw.execute('BEGIN')
            conflict = issue_in_borrowed_transaction(PostgresConnection(raw), incompatible)
            assert isinstance(conflict, Hold) and conflict.reason == 'position_attempt_payload_conflict'
        assert _assert_one_plan(dsn, payload) == original
    finally:
        _stop(workers, release)


def test_real_completed_card_busy_hold_gets_fresh_bounded_incarnation():
    dsn = _dsn()
    payload, _ = _seed(dsn)
    ctx = mp.get_context('spawn')
    entered, release, count = ctx.Event(), ctx.Event(), ctx.Value('i', 0)
    release.set()
    workers = []
    try:
        with psycopg.connect(dsn) as lock:
            lock.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tempo:card-edit:'+payload['card_id'],))
            workers.append(_spawn(ctx, dsn, payload, entered, release, count))
            first = _receive(workers[0][1])
            done = _receive(workers[0][1])
            assert done[1]['position_study_hold'] == 'position_card_busy' and done[2] == 'complete', done
            held_receipt = _receipt(dsn, first[1])
            # Freeze this task-owned cooldown in the future so process startup
            # speed cannot accidentally age it out. No sleeps prove ordering.
            with psycopg.connect(dsn) as raw:
                raw.execute("UPDATE operation_receipts SET updated_at=NOW()+INTERVAL '1 minute' WHERE operation_id=%s", (first[1],))
            # A second delivery replays the cooldown receipt without preparation.
            workers.append(_spawn(ctx, dsn, payload, entered, release, count))
            assert _receive(workers[1][1])[1] == first[1]
            assert _receive(workers[1][1]) == ('done', None, 'complete')
            assert count.value == 1
        # Only test-owned clock evidence is advanced; no timing-dependent sleep.
        with psycopg.connect(dsn) as raw:
            raw.execute("UPDATE operation_receipts SET updated_at=NOW()-INTERVAL '2 seconds' WHERE operation_id=%s", (first[1],))
        workers.append(_spawn(ctx, dsn, payload, entered, release, count))
        second = _receive(workers[2][1])
        from app.contracts.queue_issuance import issuance_operation_id
        assert second[1] == issuance_operation_id(payload, 1) != first[1]
        done = _receive(workers[2][1])
        assert done[1]['issued'] and done[2] == 'complete', done
        assert count.value == 2
        saved = _receipt(dsn, first[1])
        assert (saved['response_json'], saved['attempt_count'], saved['state']) == (held_receipt['response_json'], 1, 'complete')
        _assert_one_plan(dsn, payload)
    finally:
        _stop(workers, release)


def test_real_owner_change_after_preparation_cannot_publish_plan():
    dsn = _dsn()
    payload, _ = _seed(dsn)
    ctx = mp.get_context('spawn')
    entered, release, count = ctx.Event(), ctx.Event(), ctx.Value('i', 0)
    prepared, publish = ctx.Event(), ctx.Event()
    release.set()
    workers = []
    try:
        workers.append(_spawn(ctx, dsn, payload, entered, release, count, selected=publish, prepared=prepared))
        _receive(workers[0][1])
        assert prepared.wait(TIMEOUT), 'real source preparation did not complete'
        with psycopg.connect(dsn) as raw:
            raw.execute("UPDATE study_position_owners SET mode='paused' WHERE repertoire_id='italian'")
        publish.set()
        done = _receive(workers[0][1])
        assert done[0] == 'done' and not done[1]['issued'] and done[1]['position_study_hold'], done
        with psycopg.connect(dsn) as raw:
            assert raw.execute('SELECT count(*) FROM study_position_issued_plans').fetchone()[0] == 0
            assert raw.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s', (payload['entry_id'],)).fetchone()[0] == 0
    finally:
        _stop(workers, release, publish)


def test_real_abandoned_preparation_resumes_original_operation():
    dsn = _dsn()
    payload, _ = _seed(dsn)
    ctx = mp.get_context('spawn')
    entered, release, count = ctx.Event(), ctx.Event(), ctx.Value('i', 0)
    workers = []
    try:
        workers.append(_spawn(ctx, dsn, payload, entered, release, count))
        first = _receive(workers[0][1])
        assert entered.wait(TIMEOUT)
        original = _receipt(dsn, first[1])
        workers[0][0].terminate()
        workers[0][0].join(10)
        assert not workers[0][0].is_alive()
        # Controlled expiry of this task-owned crashed lease, not production time.
        with psycopg.connect(dsn) as raw:
            raw.execute("UPDATE operation_receipts SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE operation_id=%s", (first[1],))
        release.set()
        workers.append(_spawn(ctx, dsn, payload, entered, release, count))
        assert _receive(workers[1][1])[1] == first[1]
        done = _receive(workers[1][1])
        assert done[1]['issued'] and done[2] == 'complete', done
        current = _receipt(dsn, first[1])
        assert current['attempt_count'] == 2
        assert current['attempt_token'] != original['attempt_token']
        _assert_one_plan(dsn, payload)
    finally:
        _stop(workers, release)
