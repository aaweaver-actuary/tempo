"""Real PostgreSQL receipts and independent processes running the Celery task body.

Each named test requires a newly EMPTY local task-owned tempo_position_test_n2*
database via TEMPO_POSITION_N2_DSN. The existing N2 owner provisions/migrates it;
this module neither creates Docker resources nor targets installed user data.
Processes invoke the actual registered foreground task body directly: this proves
its claim/preparation/publication boundary, not Redis transport/acks or HTTP.
"""
from dataclasses import replace
import multiprocessing as mp
from multiprocessing.connection import wait
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


def _worker(dsn, payload, channel, entered, release, count, *, selected=None, prepared=None, start=None):
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
        if start is not None and not start.wait(TIMEOUT):
            raise RuntimeError('native initial-delivery barrier timed out')
        result = tasks.execute_foreground_command.run(operation, 'queue.issue_head', payload)
        receipt = command_gateway.read_operation(operation)
        channel.send(('done', result, receipt['state'], receipt['error'])
                     if receipt['state'] == 'failed' else ('done', result, receipt['state']))
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


def test_real_initial_missing_receipt_race_coalesces_before_first_claim():
    """Both absent-receipt selectors choose g0 before either task may claim it."""
    dsn = _dsn()
    payload, _ = _seed(dsn)
    ctx = mp.get_context('spawn')
    start, entered, release = ctx.Event(), ctx.Event(), ctx.Event()
    count = ctx.Value('i', 0)
    workers = []
    try:
        workers.extend(_spawn(ctx, dsn, payload, entered, release, count, start=start)
                       for _ in range(2))
        selections = [_receive(channel) for _, channel in workers]
        from app.contracts.queue_issuance import issuance_operation_id
        operation = issuance_operation_id(payload, 0)
        assert all(message[0] == 'selected' and message[1] == operation for message in selections)
        assert selections[0][2] != selections[1][2]
        with psycopg.connect(dsn) as raw:
            assert raw.execute('SELECT count(*) FROM operation_receipts').fetchone()[0] == 0
        assert count.value == 0
        # Release both actual task bodies together. The winning process blocks
        # at expensive preparation; the competing process must return without
        # entering it. Events, not elapsed sleeps, establish the ordering.
        start.set()
        assert entered.wait(TIMEOUT), 'neither worker reached claimed preparation'
        ready = wait([channel for _, channel in workers], timeout=TIMEOUT)
        assert ready, 'competing delivery did not observe the existing claim'
        loser = ready[0]
        assert _receive(loser) == ('done', None, 'executing')
        assert count.value == 1
        claimed = _receipt(dsn, operation)
        assert claimed['state'] == 'executing' and claimed['attempt_count'] == 1
        assert claimed['attempt_token']
        release.set()
        winner = next(channel for _, channel in workers if channel is not loser)
        done = _receive(winner)
        assert done[0] == 'done' and isinstance(done[1], dict) and done[1]['issued'] and done[2] == 'complete', done
        assert count.value == 1
        _assert_one_plan(dsn, payload)
        assert _receipt(dsn, operation)['attempt_count'] == 1
    finally:
        _stop(workers, release, start)


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
        assert done[0] == 'done' and isinstance(done[1], dict) and done[1]['issued'] and done[2] == 'complete', done
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
            assert isinstance(done[1], dict) and done[1]['position_study_hold'] == 'position_card_busy' and done[2] == 'complete', done
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
        assert isinstance(done[1], dict) and done[1]['issued'] and done[2] == 'complete', done
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
        assert done[0] == 'done' and isinstance(done[1], dict) and not done[1]['issued'] and done[1]['position_study_hold'], done
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
    cleanup_release = release
    try:
        workers.append(_spawn(ctx, dsn, payload, entered, release, count))
        first = _receive(workers[0][1])
        assert entered.wait(TIMEOUT)
        original = _receipt(dsn, first[1])
        # A terminated process can leave multiprocessing.Event's Condition
        # waiter bookkeeping unusable. Never signal or reuse that old event
        # after issuing termination, including from failure-path cleanup.
        recovered_entered, recovered_release = ctx.Event(), ctx.Event()
        recovered_release.set()
        cleanup_release = recovered_release
        workers[0][0].terminate()
        workers[0][0].join(10)
        assert not workers[0][0].is_alive()
        # Controlled expiry of this task-owned crashed lease, not production time.
        with psycopg.connect(dsn) as raw:
            raw.execute("UPDATE operation_receipts SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE operation_id=%s", (first[1],))
        workers.append(_spawn(ctx, dsn, payload, recovered_entered, recovered_release, count))
        assert _receive(workers[1][1])[1] == first[1]
        done = _receive(workers[1][1])
        assert isinstance(done[1], dict) and done[1]['issued'] and done[2] == 'complete', done
        current = _receipt(dsn, first[1])
        assert current['attempt_count'] == 2
        assert current['attempt_token'] != original['attempt_token']
        _assert_one_plan(dsn, payload)
    finally:
        _stop(workers, cleanup_release)


def test_real_prepared_head_preemption_rolls_back_and_retries_original_after_burial():
    """A typed head change permits one new incarnation, retaining failed evidence."""
    dsn = _dsn()
    payload, _ = _seed(dsn)
    ctx = mp.get_context('spawn')
    entered, release, prepared, publish = (ctx.Event() for _ in range(4))
    count = ctx.Value('i', 0)
    release.set()
    workers = []
    try:
        workers.append(_spawn(ctx, dsn, payload, entered, release, count,
                              selected=publish, prepared=prepared))
        first = _receive(workers[0][1])
        assert first[0] == 'selected'
        assert prepared.wait(TIMEOUT), 'A never completed source preparation'
        claimed = _receipt(dsn, first[1])
        assert claimed['state'] == 'executing'
        # B is an eligible existing synthetic carrier, admitted ahead of A only
        # after A's real source preparation. No review or grade is manufactured.
        with psycopg.connect(dsn,row_factory=tempo_row_factory) as raw:
            card_b = raw.execute("SELECT id FROM cards WHERE id<>%s ORDER BY id LIMIT 1",
                                 (payload['card_id'],)).fetchone()[0]
            entry_b = raw.execute("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,
                status,admission_kind,admission_repertoire_id)
                VALUES(%s,%s,0,-1,'queued','review','italian') RETURNING id""",
                (payload['queue_date'],card_b)).fetchone()[0]
        publish.set()
        rejected = _receive(workers[0][1])
        assert rejected[:3] == ('done',None,'failed'), rejected
        error = rejected[3]
        assert error['status_code'] == 409 and error['code'] == 'position_head_changed', error
        assert error['retryable'] is True
        failed = _receipt(dsn, first[1])
        assert failed['payload_json'] == claimed['payload_json']
        assert failed['request_hash'] == claimed['request_hash']
        with psycopg.connect(dsn) as raw:
            assert raw.execute('SELECT count(*) FROM study_position_issued_plans').fetchone()[0] == 0
            assert raw.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s',
                               (payload['entry_id'],)).fetchone()[0] == 0
            raw.execute("UPDATE operation_receipts SET updated_at=NOW()+INTERVAL '1 minute' WHERE operation_id=%s", (first[1],))
        # Freeze the fixture cooldown so process startup cannot age it out.
        workers.append(_spawn(ctx, dsn, payload, entered, release, count))
        assert _receive(workers[1][1])[1] == first[1]
        assert _receive(workers[1][1]) == rejected
        assert count.value == 1
        # Let B be genuinely issued, then remove it via the ordinary owning bury
        # operation. Retain its immutable plan/origin; never fake a saved review.
        payload_b = {'queue_date':payload['queue_date'],'entry_id':entry_b,
                     'card_id':card_b,'expected_revision':1}
        workers.append(_spawn(ctx, dsn, payload_b, entered, release, count))
        selected_b = _receive(workers[2][1])
        assert selected_b[0] == 'selected' and selected_b[1] != first[1]
        done_b = _receive(workers[2][1])
        assert done_b[0] == 'done' and isinstance(done_b[1],dict) and done_b[1]['issued'], done_b
        from app.queue_commands import bury_queue_entry, queue_storage
        from app.product_composition.database_infrastructure import bind_database_infrastructure
        bind_database_infrastructure()
        with psycopg.connect(dsn,row_factory=tempo_row_factory) as raw:
            db = PostgresConnection(raw)
            result = bury_queue_entry(db, payload_b)
            assert result == {'buried':True,'queue_entry_id':entry_b}
            raw.commit()
            assert queue_storage.prepare_queue_head(db,'real_game_miss')(payload['queue_date'])['id'] == payload['entry_id']
            assert raw.execute('SELECT count(*) FROM reviews').fetchone()[0] == 0
            assert raw.execute('SELECT count(*) FROM study_position_grades').fetchone()[0] == 0
            assert raw.execute('SELECT count(*) FROM study_position_issued_plans WHERE queue_entry_id=%s', (entry_b,)).fetchone()[0] == 1
            raw.execute("UPDATE operation_receipts SET updated_at=NOW()-INTERVAL '2 seconds' WHERE operation_id=%s", (first[1],))
        workers.append(_spawn(ctx, dsn, payload, entered, release, count))
        retry = _receive(workers[3][1])
        from app.contracts.queue_issuance import issuance_operation_id
        assert retry[1] == issuance_operation_id(payload,1) != first[1]
        accepted = _receive(workers[3][1])
        assert accepted[0] == 'done' and isinstance(accepted[1],dict) and accepted[1]['issued'], accepted
        assert accepted[2] == 'complete' and count.value == 3
        _assert_one_plan(dsn,payload)
        retained = _receipt(dsn,first[1])
        for field in ('state','error_json','payload_json','request_hash','attempt_count'):
            assert retained[field] == failed[field], field
    finally:
        _stop(workers,release,publish)


def _rollback_fault_setup(monkeypatch, failures):
    """Inject a PostgreSQL serialization failure after the real handler writes."""
    from app import command_gateway, queue_commands  # noqa: F401
    original = command_gateway._handlers['queue.issue_head']
    prepare = command_gateway._preparers['queue.issue_head']
    observations = {'faults': 0, 'preparations': 0, 'tokens': []}
    def observed_prepare(payload):
        observations['preparations'] += 1
        return prepare(payload)
    def fault_after_handler(db, payload):
        result = original(db, payload)
        observations['tokens'].append(db.execute_native("SELECT attempt_token FROM operation_receipts WHERE command_name='queue.issue_head' AND state='executing'").fetchone()[0])
        if observations['faults'] < failures:
            observations['faults'] += 1
            # The actual connection raises the vendor exception inside the same
            # transaction as tentative issuance, before the receipt commits.
            db.execute_native("DO $$ BEGIN RAISE EXCEPTION 'injected rollback' USING ERRCODE = '40001'; END $$")
        return result
    monkeypatch.setitem(command_gateway._handlers, 'queue.issue_head', fault_after_handler)
    monkeypatch.setitem(command_gateway._preparers, 'queue.issue_head', observed_prepare)
    return observations


def _no_issued_effects(dsn, payload):
    with psycopg.connect(dsn) as raw:
        assert raw.execute('SELECT count(*) FROM study_position_issued_plans').fetchone()[0] == 0
        assert raw.execute('SELECT count(*) FROM reviews').fetchone()[0] == 0
        assert raw.execute('SELECT count(*) FROM study_position_grades').fetchone()[0] == 0
        assert raw.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s', (payload['entry_id'],)).fetchone()[0] == 0


def _set_retry_due(dsn, operation, *, due):
    with psycopg.connect(dsn) as raw:
        raw.execute("UPDATE operation_receipts SET next_retry_at=NOW() + (%s * INTERVAL '1 minute') WHERE operation_id=%s", (-1 if due else 1, operation))


def test_real_issue_head_recovers_rollback_safe_failure_without_duplicate_plan(monkeypatch):
    import pytest
    from app import tasks, command_gateway
    from app.contracts.queue_issuance import issuance_operation_id
    dsn = _dsn()
    _configure(dsn)
    try:
        payload, _ = _seed(dsn)
        observed = _rollback_fault_setup(monkeypatch, 1)
        operation = command_gateway.select_queue_issuance_operation(payload)
        assert operation == issuance_operation_id(payload, 0)
        with pytest.raises(psycopg.errors.SerializationFailure) as failure:
            tasks.execute_foreground_command.run(operation, 'queue.issue_head', payload)
        first = _receipt(dsn, operation)
        assert first['state'] == 'retrying'
        assert first['attempt_count'] == 1 and first['next_retry_at'] is not None
        assert first['request_hash'] == command_gateway.request_digest('queue.issue_head', payload)
        assert command_gateway.select_queue_issuance_operation(payload) == operation
        _no_issued_effects(dsn, payload)
        _set_retry_due(dsn, operation, due=False)
        assert tasks.execute_foreground_command.run(operation, 'queue.issue_head', payload) is None
        assert command_gateway.claim_recoverable_operation(allowed_background_commands=()) is None
        assert observed['preparations'] == 1
        _set_retry_due(dsn, operation, due=True)
        recovered = command_gateway.claim_recoverable_operation(allowed_background_commands=())
        assert recovered['operation_id'] == operation and recovered['payload'] == payload
        result = tasks.execute_foreground_command.run(operation, 'queue.issue_head', payload)
        assert result['issued']
        complete = _receipt(dsn, operation)
        assert complete['state'] == 'complete' and complete['attempt_count'] == 2
        plan = _assert_one_plan(dsn, payload)
        assert tasks.execute_foreground_command.run(operation, 'queue.issue_head', payload) is None
        assert observed['preparations'] == 2
        assert _receipt(dsn, operation) == complete
        assert _assert_one_plan(dsn, payload) == plan
        command_gateway.record_operation_retry(operation, observed['tokens'][0], failure.value, retryable=True, background=False)
        assert _receipt(dsn, operation) == complete
        assert _assert_one_plan(dsn, payload) == plan
    finally:
        from tempo_legacy_database.adapters import postgres_store
        postgres_store.close_pools()


def test_real_issue_head_blocks_at_third_rollback_safe_failure(monkeypatch):
    import pytest
    from app import tasks, command_gateway
    from app.contracts.queue_issuance import issuance_operation_id
    dsn = _dsn()
    _configure(dsn)
    try:
        payload, _ = _seed(dsn)
        observed = _rollback_fault_setup(monkeypatch, 3)
        operation = command_gateway.select_queue_issuance_operation(payload)
        assert operation == issuance_operation_id(payload, 0)
        for attempt in range(1, 4):
            with pytest.raises(psycopg.errors.SerializationFailure):
                tasks.execute_foreground_command.run(operation, 'queue.issue_head', payload)
            receipt = _receipt(dsn, operation)
            assert receipt['attempt_count'] == attempt
            assert receipt['state'] == ('blocked' if attempt == 3 else 'retrying')
            assert (receipt['next_retry_at'] is None) == (attempt == 3)
            assert receipt['request_hash'] == command_gateway.request_digest('queue.issue_head', payload)
            _no_issued_effects(dsn, payload)
            if attempt < 3:
                _set_retry_due(dsn, operation, due=True)
                recovered = command_gateway.claim_recoverable_operation(allowed_background_commands=())
                assert recovered['operation_id'] == operation and recovered['payload'] == payload
        assert tasks.execute_foreground_command.run(operation, 'queue.issue_head', payload) is None
        assert command_gateway.select_queue_issuance_operation(payload) == operation
        assert command_gateway.claim_recoverable_operation(allowed_background_commands=()) is None
        assert observed['preparations'] == 3
        assert _receipt(dsn, operation) == receipt
    finally:
        from tempo_legacy_database.adapters import postgres_store
        postgres_store.close_pools()
