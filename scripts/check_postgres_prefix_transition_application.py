"""Issue 80 durable application proofs, exclusively on runner-owned PostgreSQL."""
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

import chess
from fastapi import HTTPException
import psycopg
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store, prefix_evaluation_api, prefix_transition_api
from app.command_gateway import execute_command, read_operation, CommandConflict
from app.services import prefix_transition_application as application
from app.services.durable_tasks import claim_task,enqueue_task_in_transaction
from app.services.opening_graph import GraphInput, build_graph
from app.services.postgres_opening_graph import stage_graph_steps, create_graph_cards, execute_postgres_opening_graph_slice
from app.services.postgres_integrity import execute_postgres_integrity_slice
from app.services.postgres_queue_refresh import execute_postgres_queue_refresh_slice
from app.services.redis_admission_gate import BackgroundAdmissionDeferred


def idle_call(callback):
    """Retry only real foreground preemption, never an outage or stale proof."""
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        try:
            return callback()
        except (HTTPException,psycopg.errors.SerializationFailure,BackgroundAdmissionDeferred) as error:
            cause=error.__cause__ if isinstance(error,psycopg.errors.SerializationFailure) else error
            if not isinstance(error, BackgroundAdmissionDeferred) and not (isinstance(cause,HTTPException) and cause.status_code==503 and isinstance(cause.detail,dict)
                    and cause.detail.get('message')=='Study work is active. Retry the diagnostic when study is idle.'):
                raise
            from app.services.redis_admission_gate import foreground_present
            while foreground_present() and time.monotonic()<deadline:
                time.sleep(0.01)
    raise AssertionError('Foreground-idle preparation did not finish within its deadline')


def execute(payload):
    return idle_call(lambda:execute_command(payload['operation_id'],application.COMMAND,payload))


def ready_plan(repertoire_id, lines, *, selected_line_count=2):
    deadline = time.monotonic() + 10
    source = idle_call(lambda:prefix_evaluation_api.load_snapshot(repertoire_id, time.monotonic()+10))
    request = {'snapshot_id': prefix_evaluation_api.snapshot_identity(source),
               'selected_line_ids': [line['id'] for line in lines[:selected_line_count]],
               'candidate_depths': {line['id']: 1 for line in lines[:selected_line_count]}}
    plan = idle_call(lambda:prefix_transition_api.prefix_transition_plan(repertoire_id, prefix_transition_api.PrefixTransitionRequest(**request)))
    assert plan.status == 'ready', (plan.status, plan.blockers)
    request.update({field: getattr(plan, field) for field in ('plan_id', 'transition_snapshot_id', 'graph_generation', 'study_day')})
    operation_id = uuid.uuid4().hex
    return plan, {'operation_id': operation_id, 'repertoire_id': repertoire_id, 'request': request}



@contextmanager
def isolate_unrelated_publication_tasks():
    """Use real admission controls; claim only this rehearsal's publication work.

    Earlier regular proofs may deliberately retain queued work for deleted source
    fixtures. Do not consume, repair, or discard those unrelated task identities.
    """
    with postgres_store.connection(read_only=False) as database:
        paused=[row[0] for row in database.execute_native("INSERT INTO background_activity(source,work_id,paused,updated_at) SELECT 'durable',id,1,%s FROM background_tasks WHERE kind IN ('opening_graph_rebuild','integrity_scan') AND deduplication_key NOT LIKE 'issue80-%%' ON CONFLICT DO NOTHING RETURNING work_id",(datetime.now(timezone.utc).isoformat(),)).fetchall()]
    try:
        yield
    finally:
        with postgres_store.connection(read_only=False) as database:
            database.execute_native("DELETE FROM background_activity WHERE source='durable' AND work_id=ANY(%s)",(paused,))


def drain(kind, handler, *, slice_budget=300):
    count = 0
    while task := idle_call(lambda: claim_task(kind)):
        idle_call(lambda: handler(task))
        count += 1
        assert count < slice_budget, (kind, task)
    return count


def test_postgres_transition_driver_waits_only_for_foreground_admission():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.database import background_connection
    from app.services.redis_admission_gate import foreground_lease
    attempted, admitted = Event(), Event()
    def bounded_read():
        attempted.set()
        with background_connection() as database:
            admitted.set()
            assert database.execute_native('SHOW transaction_timeout').fetchone()[0] == '250ms'
            return database.execute_native('SELECT 1').fetchone()[0]
    with ThreadPoolExecutor(max_workers=1) as executor:
        with foreground_lease():
            result = executor.submit(idle_call, bounded_read)
            assert attempted.wait(5) and not admitted.is_set() and not result.done()
        assert result.result(timeout=10) == 1
    for failure in (RuntimeError('execution failed'), HTTPException(503, 'provider unavailable')):
        def reject():
            raise failure
        try:
            idle_call(reject)
        except type(failure) as observed:
            assert observed is failure
        else:
            raise AssertionError('Transition driver hid an execution failure')
    print('PASS test_postgres_transition_driver_waits_only_for_foreground_admission (real Redis, unchanged budget, execution failures retained)')


def publish(payload, after_activation=None):
    drain(application.TASK_KIND, application.execute_application_slice)
    execute(payload)
    result = read_operation(payload['operation_id'])
    assert result['state'] == 'pending' and result['transition']['state'] == 'publishing', result
    if after_activation:
        after_activation()
    drain('opening_graph_rebuild', execute_postgres_opening_graph_slice)
    drain('integrity_scan', execute_postgres_integrity_slice)
    drain('daily_queue', execute_postgres_queue_refresh_slice)
    drain(application.TASK_KIND, application.execute_application_slice)
    result = read_operation(payload['operation_id'])
    if result['state'] == 'pending' and result['transition']['queue_date'] == application.date.today().isoformat():
        # Midnight completion requests a new current-day refresh, then yields.
        drain('daily_queue', execute_postgres_queue_refresh_slice)
        drain(application.TASK_KIND, application.execute_application_slice)
        result = read_operation(payload['operation_id'])
    assert result['state'] == 'complete', result
    return result['response']


def create_fixture(label, *, source_line_count=None):
    repertoire_id = 'issue80-' + label + '-' + uuid.uuid4().hex
    other_id = repertoire_id + '-shared'
    # Separate FEN identity family from earlier durability fixtures.
    board = chess.Board()
    for move in ('a2a3', 'a7a6', 'h2h3', 'h7h6', 'a3a4', 'a6a5'):
        board.push_uci(move)
    if 'activated' in label:
        board.push_uci('h3h4'); board.push_uci('h6h5')
    board.fullmove_number = 1
    fen = board.fen()
    lines = [dict(id=repertoire_id + suffix, name=name, start_fen=fen, moves_json=json.dumps(moves),
                  trained_color='black', learner_decision_count=2)
             for suffix, name, moves in (
                 ('-caro-a', 'Caro-Kann A', ['e2e4','c7c6','d2d4','d7d5','b1c3','d5e4','c3e4','g8f6']),
                 ('-caro-b', 'Caro-Kann B', ['e2e4','c7c6','d2d4','d7d5','b1d2','d5e4','d2e4','g8f6']),
                 ('-qgd', 'QGD', ['d2d4','d7d5','c2c4','e7e6','b1c3','g8f6']))]
    if source_line_count is not None:
        import random
        from app.services.cards import card_id
        generator = random.Random(102)
        lines = []
        used_identities = set()
        used_decision_positions = set()
        while len(lines) < source_line_count:
            if board.is_game_over() or len(board.move_stack) > 60:
                board = chess.Board(fen)
            starting_fen = board.fen()
            trained_color = 'black'
            moves = []
            decision_positions = set()
            for _ in range(4):
                if board.is_game_over():
                    break
                if board.turn == chess.BLACK:
                    decision_positions.add(' '.join(board.fen().split()[:4]))
                move = generator.choice(sorted(board.legal_moves, key=lambda item: item.uci()))
                moves.append(move.uci())
                board.push(move)
            identities = {card_id(starting_fen, moves), card_id(starting_fen, moves[:2])}
            continuation = chess.Board(starting_fen)
            for move in moves[:2]:
                continuation.push_uci(move)
            identities.add(card_id(continuation.fen(), moves[2:]))
            if (len(moves) != 4 or len(identities) != 3 or identities & used_identities
                    or len(decision_positions) != 2 or decision_positions & used_decision_positions):
                continue
            used_identities.update(identities)
            used_decision_positions.update(decision_positions)
            lines.append(dict(id=f'{repertoire_id}-route-{len(lines):04d}', name='Scale route',
                              start_fen=starting_fen, moves_json=json.dumps(moves),
                              trained_color=trained_color, learner_decision_count=2))
    # Fixtures are sequential; cleanup ensures content-derived IDs remain isolated.
    steps = build_graph(GraphInput(repertoire_id, tuple(lines), 2))
    ids = sorted({step.card_id for step in steps})
    with postgres_store.connection(read_only=False) as database:
        now = datetime.now(timezone.utc).isoformat()
        for rep in (repertoire_id, other_id):
            database.execute_native('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,\'issue80.pgn\',%s)', (rep, rep, now))
        if label.startswith('backup-'):
            database.execute_native('UPDATE repertoires SET new_cards_per_day=0 WHERE id=ANY(%s)',([repertoire_id,other_id],))
        for line in lines:
            database.execute_native('INSERT INTO repertoire_lines(id,repertoire_id,name,start_fen,moves_json,trained_color,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)', (line['id'], repertoire_id, line['name'], line['start_fen'], line['moves_json'], line['trained_color'], now))
            database.execute_native('INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) VALUES(%s,2)', (line['id'],))
        assert not database.execute_native('SELECT id FROM cards WHERE id=ANY(%s)', (ids,)).fetchall(), 'Fixture overlaps retained data'
        create_graph_cards(database, tuple({step.card_id: step for step in steps}.values()), date.today().isoformat(), strict=True)
        for card_id in ids:
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)', (repertoire_id,card_id))
        stage_graph_steps(database, steps, 1)
        database.execute_native("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)", (repertoire_id,now))
        baseline=enqueue_task_in_transaction(database,'opening_graph_rebuild',repertoire_id,{'repertoire_id':repertoire_id,'local_day':date.today().isoformat()},priority=40)
        assert baseline['generation']==1
        database.execute_native("UPDATE background_tasks SET phase='finalize' WHERE id=%s",(baseline['id'],))
    # The larger fixture deliberately requires more unchanged bounded slices;
    # derive its finite test iteration budget from its actual source size.
    fixture_slice_budget = 300 if source_line_count is None else max(300, source_line_count * 32)
    drain('opening_graph_rebuild',execute_postgres_opening_graph_slice, slice_budget=fixture_slice_budget)
    drain('integrity_scan',execute_postgres_integrity_slice, slice_budget=fixture_slice_budget)
    drain('daily_queue',execute_postgres_queue_refresh_slice, slice_budget=fixture_slice_budget)
    return repertoire_id, other_id, lines, steps


def cleanup_fixture(repertoire_id,other_id,extra_repertoire_ids=()):
    owned_repertoire_ids = [repertoire_id,other_id,*extra_repertoire_ids]
    with postgres_store.connection(read_only=False) as database:
        operations = [row[0] for row in database.execute_native('SELECT operation_id FROM prefix_transition_applications WHERE repertoire_id=%s', (repertoire_id,)).fetchall()]
        for operation_id in operations:
            application.release_fences(database, operation_id)
        database.execute_native('DELETE FROM study_attempts WHERE exercise_id=%s',(repertoire_id+'-exercise',))
        database.execute_native('DELETE FROM study_exercises WHERE study_id=%s',(repertoire_id,))
        database.execute_native('DELETE FROM study_positions WHERE source_id=%s',(repertoire_id+'-source',))
        database.execute_native('DELETE FROM study_sources WHERE chapter_id=%s',(repertoire_id+'-chapter',))
        database.execute_native('DELETE FROM study_chapters WHERE study_id=%s',(repertoire_id,))
        database.execute_native('DELETE FROM studies WHERE id=%s',(repertoire_id,))
        database.execute_native('DELETE FROM opening_evidence_attempts WHERE repertoire_id=ANY(%s)', (owned_repertoire_ids,))
        database.execute_native('DELETE FROM prefix_transition_applications WHERE repertoire_id=%s', (repertoire_id,))
        owned_ids=[row[0] for row in database.execute_native('SELECT id FROM cards WHERE repertoire_id=ANY(%s)',(owned_repertoire_ids,)).fetchall()]
        database.execute_native('DELETE FROM opening_evidence_queue_contexts WHERE repertoire_id=ANY(%s)',(owned_repertoire_ids,))
        database.execute_native('DELETE FROM queue_attempt_origins WHERE card_id=ANY(%s)',(owned_ids,))
        database.execute_native('DELETE FROM review_attempt_receipts WHERE card_id=ANY(%s)',(owned_ids,))
        database.execute_native('DELETE FROM opening_evidence_presentations WHERE card_id=ANY(%s)',(owned_ids,))
        database.execute_native('DELETE FROM repertoires WHERE id=ANY(%s)', (owned_repertoire_ids,))
        database.execute_native('DELETE FROM background_tasks WHERE deduplication_key=ANY(%s)', ([repertoire_id,other_id,*operations],))
        database.execute_native('DELETE FROM operation_receipts WHERE operation_id=ANY(%s)', (operations,))


@contextmanager
def fixture(label, *, extra_repertoire_ids=(), source_line_count=None):
    created=create_fixture(label, source_line_count=source_line_count)
    try:
        yield created
    finally:
        cleanup_fixture(created[0],created[1],extra_repertoire_ids)




def test_issue80_application_rehearsal_preserves_unrelated_publication_tasks():
    unrelated='unrelated-prefix-proof-'+uuid.uuid4().hex
    with postgres_store.connection(read_only=False) as database:
        task=enqueue_task_in_transaction(database,'opening_graph_rebuild',unrelated,{'repertoire_id':unrelated,'local_day':date.today().isoformat()},priority=0)
    try:
        with isolate_unrelated_publication_tasks():
            with fixture('isolated-claims'):
                with postgres_store.connection(read_only=True) as database:
                    row=database.execute_native('SELECT state,generation,attempt_count FROM background_tasks WHERE id=%s',(task['id'],)).fetchone()
                    assert tuple(row)==('queued',task['generation'],0)
    finally:
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('DELETE FROM background_tasks WHERE id=%s',(task['id'],))
    print('PASS test_issue80_application_rehearsal_preserves_unrelated_publication_tasks')


def test_issue80_unfenced_bulk_graph_writes_use_a_constant_reservation_lock_budget():
    with fixture('bulk-locks') as (rep,other,lines,steps):
        with postgres_store.connection(read_only=False) as database:
            database.execute_native("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) SELECT %s||'-bulk-'||ordinal,%s,'prefix',%s,'[]',%s FROM generate_series(1,8000) ordinal",(rep,rep,lines[0]['start_fen'],date.today().isoformat()))
            database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id) SELECT %s,id FROM cards WHERE repertoire_id=%s AND id LIKE %s",(rep,rep,rep+'-bulk-%'))
            database.execute_native("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color) SELECT %s,99,%s,ordinal,%s||'-bulk-'||ordinal,'synthetic',%s,'[]','black' FROM generate_series(1,8000) ordinal",(rep,lines[0]['id'],rep,lines[0]['start_fen']))
            held=database.execute_native("SELECT COUNT(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'").fetchone()[0]
            assert held==1,('Unfenced writes allocated per-card locks',held)
    print('PASS test_issue80_unfenced_bulk_graph_writes_use_a_constant_reservation_lock_budget')


def test_issue80_raw_snapshot_order_matches_recording_for_multidigit_and_unicode_rows():
    import hashlib
    from app.snapshot_reads import RecordingReader, snapshot_rows
    reads = []
    query = "SELECT ordinal AS id,CASE WHEN ordinal %% 2 = 0 THEN 'éclair' ELSE 'zebra' END AS name FROM generate_series(1,128) ordinal"
    with postgres_store.connection(read_only=True) as database:
        rows = RecordingReader(database, reads).execute_native(query).fetchall()
        assert len(rows) == 128
        raw = snapshot_rows(database, query, ())
        assert reads[0].uses_sha256_evidence
        assert snapshot_rows(database, query, (), uses_sha256_evidence=True) == reads[0].rows
    assert tuple(sorted(hashlib.sha256(row.encode('utf8')).hexdigest() for row in raw)) == reads[0].rows
    print('PASS test_issue80_raw_snapshot_order_matches_recording_for_multidigit_and_unicode_rows')


def advisory_lock_measurement(database):
    rows = database.execute_native("SELECT classid,objid,objsubid,mode FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' ORDER BY classid,objid,mode").fetchall()
    relation_identities = database.execute_native("SELECT COUNT(DISTINCT (database,relation)) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='relation'").fetchone()[0]
    return {'distinct_identities': len({tuple(row)[:3] for row in rows}), 'mode_entries': len(rows),
            'relation_identities': relation_identities}


def test_issue80_bulk_evidence_keeps_native_values_and_bounded_digest_transfer():
    import hashlib
    from app.snapshot_reads import RecordingReader, snapshot_rows
    query = "SELECT ordinal,repeat('r',524288) provenance,TIMESTAMPTZ '2026-10-08T12:00:00Z' observed_at FROM generate_series(1,2) ordinal"
    reads = []
    with postgres_store.connection(read_only=True) as database:
        native = database.execute_native(query).fetchall()
    with postgres_store.connection(read_only=True) as database:
        recorded = RecordingReader(database, reads).execute_native(query).fetchall()
    with postgres_store.connection(read_only=True) as database:
        raw = snapshot_rows(database, query, ())
        replayed = snapshot_rows(database, query, (), uses_sha256_evidence=True)
    assert [dict(row) for row in recorded] == [dict(row) for row in native]
    assert isinstance(recorded[0]['observed_at'], datetime)
    assert all(len(evidence) == 64 for evidence in reads[0].rows)
    assert tuple(sorted(hashlib.sha256(row.encode('utf8')).hexdigest() for row in raw)) == reads[0].rows == replayed
    print('PASS test_issue80_bulk_evidence_keeps_native_values_and_bounded_digest_transfer')


def test_issue80_real_application_acceptance_and_activation_have_constant_lock_scaling():
    import random
    from app.services.cards import card_id
    original_lock = application.lock_and_revalidate
    original_activate = application.activate_application
    measurements = []
    largest_snapshot_bytes = None
    for requested_count in (128, 1024, None):
        print(json.dumps({'application_scaling_fixture': requested_count or 'exact_byte_ceiling'}), flush=True)
        shared_repertoire_ids = []
        with fixture('application-scaling-' + str(requested_count), extra_repertoire_ids=shared_repertoire_ids) as (rep, other, lines, steps):
            # Authored memberships outside the generated graph are legitimate,
            # unchanged inputs. Each card has a real legal presentation/identity.
            generator = random.Random(80)
            board = chess.Board(lines[0]['start_fen'])
            board.push_uci('g2g3')
            bulk_rows = []
            unique_ids = set()
            while len(bulk_rows) < 1024:
                if board.is_game_over() or len(board.move_stack) > 80:
                    board = chess.Board(lines[0]['start_fen']); board.push_uci('g2g3')
                move = generator.choice(sorted(board.legal_moves, key=lambda item: item.uci()))
                starting_fen, moves = board.fen(), [move.uci()]
                identifier = card_id(starting_fen, moves)
                if identifier not in unique_ids:
                    unique_ids.add(identifier)
                    bulk_rows.append((identifier, rep, starting_fen, json.dumps(moves), 'white' if board.turn else 'black'))
                board.push(move)
            inserted_count = 0
            def resize(count):
                nonlocal inserted_count
                with postgres_store.connection(read_only=False) as database:
                    database.execute_native('UPDATE repertoires SET new_cards_per_day=0 WHERE id=ANY(%s)', ([rep, other],))
                    if count > inserted_count:
                        with database.raw.cursor() as cursor:
                            cursor.executemany("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,canonical_route_source,state,introduced_at,due_date) VALUES(%s,%s,'prefix',%s,%s,%s,1,'learning','2000-01-01','2099-01-01')", bulk_rows[inserted_count:count])
                            cursor.executemany('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)', [(rep, row[0]) for row in bulk_rows[inserted_count:count]])
                    elif count < inserted_count:
                        database.execute_native('DELETE FROM cards WHERE id=ANY(%s)', ([row[0] for row in bulk_rows[count:inserted_count]],))
                inserted_count = count
            resize(128)
            shared_repertoire_ids.extend(rep + '-scaling-shared-' + str(index) for index in range(126))
            with postgres_store.connection(read_only=False) as database:
                with database.raw.cursor() as cursor:
                    cursor.executemany("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'scaling.pgn',%s)", [(identifier,identifier,datetime.now(timezone.utc).isoformat()) for identifier in shared_repertoire_ids])
                    cursor.executemany('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)', [(identifier,bulk_rows[0][0]) for identifier in shared_repertoire_ids])
                with database.raw.cursor() as cursor:
                    cursor.executemany('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)', [(other,row[0]) for row in bulk_rows[:32]])
                    cursor.executemany("INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES(%s,%s,0,'complete')", [(f'{2000+index}-01-01',bulk_rows[0][0]) for index in range(64)])
            def prepared_for_count(count):
                resize(count)
                plan, payload = ready_plan(rep, lines)
                return plan, payload, idle_call(lambda: application.prepare_application(payload))
            count = requested_count or 1024
            plan, payload, prepared = prepared_for_count(count)
            def raw_transition_bytes(captured):
                return sum(json.loads(read.rows[0])['bytes'] for read in captured.reads
                           if 'octet_length(row_to_json(bounded)::text)' in read.query)
            if requested_count is None:
                # Reach the exact byte ceiling with valid authored provenance,
                # while retaining 1024 identities and the same query budgets.
                # One extra byte must reject before any application writes.
                with postgres_store.connection(read_only=False) as database:
                    database.execute_native("UPDATE cards SET source_ref='' WHERE id=%s", (bulk_rows[0][0],))
                plan, payload, prepared = prepared_for_count(count)
                padding = prefix_transition_api.MAX_TRANSITION_BYTES - raw_transition_bytes(prepared)
                assert padding > 0
                with postgres_store.connection(read_only=False) as database:
                    database.execute_native("UPDATE cards SET source_ref=repeat('r',%s) WHERE id=%s", (padding+1, bulk_rows[0][0]))
                try:
                    prepared_for_count(count)
                except HTTPException as error:
                    assert error.status_code == 413, error
                else:
                    raise AssertionError('Oversized snapshot was accepted')
                with postgres_store.connection(read_only=False) as database:
                    database.execute_native("UPDATE cards SET source_ref=repeat('r',%s) WHERE id=%s", (padding, bulk_rows[0][0]))
                plan, payload, prepared = prepared_for_count(count)
                largest_snapshot_bytes = raw_transition_bytes(prepared)
                assert largest_snapshot_bytes == prefix_transition_api.MAX_TRANSITION_BYTES
            def measured_lock(database, captured):
                started = time.perf_counter()
                original_lock(database, captured)
                measurement = advisory_lock_measurement(database)
                measurement.update(phase='acceptance' if captured.staged_generation is None else 'activation',
                                   authored_cards=count, captured_identities=len(captured.snapshot.lookup_card_ids),
                                   shared_repertoires=len(captured.repertoire_ids), historical_days=64,
                                   transition_bytes=raw_transition_bytes(captured), lock_seconds=time.perf_counter()-started)
                measurements.append(measurement)
                print(json.dumps({'application_lock_measurement': measurement}), flush=True)
                assert measurement['distinct_identities'] == 2, measurement
            def measured_activation(database, captured, current):
                original_activate(database, captured, current)
                measurement = advisory_lock_measurement(database)
                assert measurement['distinct_identities'] == 2, measurement
                measurements[-1].update(after_write_mode_entries=measurement['mode_entries'],
                                        after_write_relation_identities=measurement['relation_identities'])
            application.lock_and_revalidate = measured_lock
            application.activate_application = measured_activation
            try:
                assert execute(payload) == {'status': 'pending'}, read_operation(payload['operation_id'])
                drain(application.TASK_KIND, application.execute_application_slice)
                assert execute(payload) == {'status': 'pending'}
            finally:
                application.lock_and_revalidate = original_lock
                application.activate_application = original_activate
    print(json.dumps({'regression': 'test_issue80_real_application_acceptance_and_activation_have_constant_lock_scaling', 'largest_accepted_snapshot_bytes': largest_snapshot_bytes, 'measurements': measurements}))
    print('PASS test_issue80_real_application_acceptance_and_activation_have_constant_lock_scaling')


def test_issue80_queue_and_row_contention_yield_without_losing_operation_identity():
    from check_postgres_graph_retention import owned_admission_scope
    with owned_admission_scope('transition-contention-'+uuid.uuid4().hex):
        _assert_transition_row_contention()


def _assert_transition_row_contention():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    for producer_kind in ('queue', 'card', 'graph'):
        with fixture('foreground-' + producer_kind) as (rep, other, lines, steps):
            plan, payload = ready_plan(rep, lines)
            held, release = threading.Event(), threading.Event()
            def producer():
                with postgres_store.connection(read_only=False) as database:
                    if producer_kind == 'queue':
                        # Even a zero-row statement joins the database barrier.
                        database.execute_native('UPDATE daily_queue SET status=status WHERE card_id=%s', (steps[0].card_id,))
                    elif producer_kind == 'card':
                        database.execute_native('SELECT id FROM cards WHERE id=%s FOR UPDATE', (steps[0].card_id,)).fetchone()
                    else:
                        database.execute_native("SELECT id FROM background_tasks WHERE kind='opening_graph_rebuild' AND deduplication_key=%s FOR UPDATE", (rep,)).fetchone()
                    held.set()
                    assert release.wait(5), 'Transition did not yield to foreground ownership'
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(producer)
                try:
                    assert held.wait(5)
                    started = time.perf_counter()
                    try:
                        execute(payload)
                    except psycopg.errors.LockNotAvailable:
                        pass
                    else:
                        raise AssertionError('Transition did not yield to ' + producer_kind)
                    elapsed = time.perf_counter() - started
                    assert elapsed < 1, (producer_kind, elapsed)
                    with postgres_store.connection(read_only=True) as database:
                        assert not database.execute_native('SELECT 1 FROM prefix_transition_applications WHERE operation_id=%s', (payload['operation_id'],)).fetchone()
                        assert database.execute_native('SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s', (rep,)).fetchone()[0] == 1
                finally:
                    release.set()
                pending.result(timeout=5)
            assert execute(payload) == {'status': 'pending'}
            final = publish(payload)
            assert execute(payload) == final
            print(json.dumps({'regression': 'test_issue80_queue_and_row_contention_yield_without_losing_operation_identity', 'producer': producer_kind, 'yield_seconds': elapsed}))
    print('PASS test_issue80_queue_and_row_contention_yield_without_losing_operation_identity')


def test_issue80_distinct_applications_share_a_bounded_barrier_and_recover_after_contention():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from app import command_gateway
    original_prepare = command_gateway._preparers[application.COMMAND]
    original_lock = application.lock_and_revalidate
    with fixture('parallel-first') as (first_rep, first_other, first_lines, first_steps):
        with fixture('activated-parallel-second') as (second_rep, second_other, second_lines, second_steps):
            first_plan, first_payload = ready_plan(first_rep, first_lines)
            second_plan, second_payload = ready_plan(second_rep, second_lines)
            prepared = {payload['operation_id']: idle_call(lambda payload=payload: application.prepare_application(payload)) for payload in (first_payload, second_payload)}
            held, release = threading.Event(), threading.Event()
            def paused_lock(database, captured):
                original_lock(database, captured)
                measured = advisory_lock_measurement(database)
                assert measured['distinct_identities'] == 2, measured
                if captured.payload['operation_id'] == first_payload['operation_id']:
                    held.set()
                    assert release.wait(5), 'Independent application did not yield'
            command_gateway._preparers[application.COMMAND] = lambda payload: prepared[payload['operation_id']]
            application.lock_and_revalidate = paused_lock
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    first = executor.submit(execute, first_payload)
                    try:
                        assert held.wait(5)
                        try:
                            execute(second_payload)
                        except psycopg.errors.LockNotAvailable:
                            pass
                        else:
                            raise AssertionError('A second application did not yield to the short barrier')
                    finally:
                        release.set()
                    assert first.result(timeout=5) == {'status': 'pending'}
                assert execute(second_payload) == {'status': 'pending'}
            finally:
                application.lock_and_revalidate = original_lock
                command_gateway._preparers[application.COMMAND] = original_prepare
            # Activate both approved snapshots before global queue publication
            # can legitimately change the other application's captured queue.
            drain(application.TASK_KIND, application.execute_application_slice)
            for payload in (first_payload, second_payload):
                assert execute(payload) == {'status': 'pending'}
                assert read_operation(payload['operation_id'])['transition']['state'] == 'publishing'
            assert publish(first_payload)['operation_id'] == first_payload['operation_id']
            second_result = read_operation(second_payload['operation_id'])
            assert second_result['state'] == 'complete', second_result
            assert execute(second_payload) == second_result['response']
    print('PASS test_issue80_distinct_applications_share_a_bounded_barrier_and_recover_after_contention')


def test_issue80_permanent_deletion_exclusions_block_plans_and_fenced_absent_targets():
    from app import card_commands  # Register the actual foreground deletion command.
    from app.snapshot_reads import snapshot_rows
    with fixture('deletion-fences') as (rep, other, lines, steps):
        plan, payload = ready_plan(rep, lines)
        target = next(card.card_id for card in plan.cards if card.lifecycle == 'create')
        failed_operation = payload['operation_id']
        deletion_operation = uuid.uuid4().hex
        deleted_after_publication = None
        try:
            with postgres_store.connection(read_only=False) as database:
                database.execute_native('INSERT INTO deleted_cards(card_id,deleted_at) VALUES(%s,%s)', (target, datetime.now(timezone.utc).isoformat()))
            blocked = idle_call(lambda: prefix_transition_api.prefix_transition_plan(rep, prefix_transition_api.PrefixTransitionRequest(**{key:value for key,value in payload['request'].items() if key in ('snapshot_id','selected_line_ids','candidate_depths')})))
            assert blocked.status == 'blocked' and any(blocker.code == 'permanently_deleted_target' for blocker in blocked.blockers), blocked
            assert execute(payload) is None
            assert read_operation(failed_operation)['error']['detail']['code'] == 'blocked_plan'
            with postgres_store.connection(read_only=False) as database:
                assert not database.execute_native('SELECT 1 FROM prefix_transition_applications WHERE operation_id=%s', (failed_operation,)).fetchone()
                assert not database.execute_native('SELECT 1 FROM cards WHERE id=%s', (target,)).fetchone()
                database.execute_native('DELETE FROM deleted_cards WHERE card_id=%s', (target,))
            plan, payload = ready_plan(rep, lines)
            assert execute(payload) == {'status':'pending'}
            with postgres_store.connection(read_only=True) as database:
                before = snapshot_rows(database, 'SELECT * FROM cards WHERE repertoire_id=%s', (rep,))
            for tombstone_only in (True, False):
                try:
                    with postgres_store.connection(read_only=False) as database:
                        if tombstone_only:
                            database.execute_native('INSERT INTO deleted_cards(card_id,deleted_at) VALUES(%s,%s)', (target, datetime.now(timezone.utc).isoformat()))
                        else:
                            card_commands.delete_card(database, {'card_id':steps[0].card_id,'expected_revision':1})
                except psycopg.Error as error:
                    assert error.sqlstate == 'P0080', error
                else:
                    raise AssertionError('Permanent deletion bypassed a retained transition fence')
            with postgres_store.connection(read_only=True) as database:
                assert snapshot_rows(database, 'SELECT * FROM cards WHERE repertoire_id=%s', (rep,)) == before
                assert not database.execute_native('SELECT 1 FROM deleted_cards WHERE card_id=%s', (target,)).fetchone()
            publish(payload)
            deleted_after_publication = next(step.card_id for step in steps if step.line_id == lines[2]['id'])
            deletion_payload = {'card_id':deleted_after_publication,'expected_revision':1}
            from concurrent.futures import ThreadPoolExecutor
            import threading
            held, release = threading.Event(), threading.Event()
            def queue_writer():
                with postgres_store.connection(read_only=False) as database:
                    database.execute_native('UPDATE daily_queue SET status=status WHERE card_id=%s', (deleted_after_publication,))
                    held.set()
                    assert release.wait(5), 'Deletion command did not yield to the queue writer'
            with ThreadPoolExecutor(max_workers=1) as executor:
                writer = executor.submit(queue_writer)
                try:
                    assert held.wait(5)
                    try:
                        execute_command(deletion_operation, 'cards.delete', deletion_payload)
                    except psycopg.errors.LockNotAvailable:
                        pass
                    else:
                        raise AssertionError('Deletion command did not retry under queue contention')
                finally:
                    release.set()
                writer.result(timeout=5)
            with postgres_store.connection(read_only=True) as database:
                assert database.execute_native('SELECT 1 FROM cards WHERE id=%s', (deleted_after_publication,)).fetchone()
                assert not database.execute_native('SELECT 1 FROM deleted_cards WHERE card_id=%s', (deleted_after_publication,)).fetchone()
            response = execute_command(deletion_operation, 'cards.delete', deletion_payload)
            assert response == {'deleted':True,'card_id':deleted_after_publication}
            assert execute_command(deletion_operation, 'cards.delete', deletion_payload) == response
        finally:
            with postgres_store.connection(read_only=False) as database:
                database.execute_native('DELETE FROM deleted_cards WHERE card_id=ANY(%s)', ([target, *([deleted_after_publication] if deleted_after_publication else [])],))
                database.execute_native('DELETE FROM operation_receipts WHERE operation_id=ANY(%s)', ([failed_operation,deletion_operation],))
    print('PASS test_issue80_permanent_deletion_exclusions_block_plans_and_fenced_absent_targets')


def test_issue80_deletion_exclusion_races_use_the_bounded_reservation_barrier():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from app.card_deletion import permanent_delete_card
    with fixture('deletion-races') as (rep, other, lines, steps):
        plan, payload = ready_plan(rep, lines)
        prepared = idle_call(lambda:application.prepare_application(payload))
        creation = prepared.creations[0]
        held, release = threading.Event(), threading.Event()
        def create_and_hold():
            with postgres_store.connection(read_only=False) as database:
                create_graph_cards(database, (creation,), plan.study_day, strict=True)
                held.set()
                assert release.wait(5), 'Deletion did not yield to identity creation'
        def delete_and_hold():
            with postgres_store.connection(read_only=False) as database:
                permanent_delete_card(database, creation.card_id, 1)
                held.set()
                assert release.wait(5), 'Recreation did not yield to deletion exclusion'
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                creator = executor.submit(create_and_hold)
                try:
                    assert held.wait(5)
                    try:
                        with postgres_store.connection(read_only=False) as database:
                            database.execute_native('INSERT INTO deleted_cards(card_id,deleted_at) VALUES(%s,%s)', (creation.card_id, datetime.now(timezone.utc).isoformat()))
                    except psycopg.errors.LockNotAvailable:
                        pass
                    else:
                        raise AssertionError('Exclusion bypassed an uncommitted identity creation')
                finally:
                    release.set()
                creator.result(timeout=5)
                held.clear(); release.clear()
                deletion = executor.submit(delete_and_hold)
                try:
                    assert held.wait(5)
                    try:
                        # Use the actual bounded background writer profile;
                        # the admin fixture connection has no lock timeout.
                        with postgres_store.connection(read_only=False, background=True) as database:
                            create_graph_cards(database, (creation,), plan.study_day, strict=True)
                    except psycopg.errors.LockNotAvailable:
                        pass
                    else:
                        raise AssertionError('Recreation bypassed an uncommitted permanent deletion')
                finally:
                    release.set()
                deletion.result(timeout=5)
            try:
                with postgres_store.connection(read_only=False) as database:
                    create_graph_cards(database, (creation,), plan.study_day, strict=True)
            except psycopg.errors.CheckViolation as error:
                assert error.diag.constraint_name == 'deleted_card_content'
            else:
                raise AssertionError('Retry recreated a permanently deleted identity')
            with postgres_store.connection(read_only=True) as database:
                assert not database.execute_native('SELECT 1 FROM cards WHERE id=%s', (creation.card_id,)).fetchone()
                assert database.execute_native('SELECT 1 FROM deleted_cards WHERE card_id=%s', (creation.card_id,)).fetchone()
        finally:
            with postgres_store.connection(read_only=False) as database:
                database.execute_native('DELETE FROM deleted_cards WHERE card_id=%s', (creation.card_id,))
    print('PASS test_issue80_deletion_exclusion_races_use_the_bounded_reservation_barrier')


@contextmanager
def historical_transition_database():
    """Rehearse 038->current without punching a hole in the current ledger.

    Migration 039 is reconstructed only inside this fresh database, so later
    migrations can run normally and the parent workload keeps all its evidence.
    """
    from unittest.mock import patch
    from psycopg import sql
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Historical transition proof requires a disposable database')
    source_dsn = os.environ['TEMPO_DATABASE_WRITE_URL']
    with postgres_store.connection(read_only=True) as database:
        current_ledger = [row[0] for row in database.execute_native('SELECT version FROM tempo_schema_migrations ORDER BY version').fetchall()]
        source_settings = database.execute_native('SELECT row_to_json(settings)::text FROM settings WHERE id=1').fetchone()[0]
    administrator_dsn = psycopg.conninfo.make_conninfo(source_dsn, dbname='postgres')
    database_name = 'tempo_transition_upgrade_' + uuid.uuid4().hex[:12]
    historical_dsn = psycopg.conninfo.make_conninfo(source_dsn, dbname=database_name)
    with psycopg.connect(administrator_dsn, autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    try:
        with psycopg.connect(historical_dsn) as database:
            for migration in sorted((Path(__file__).resolve().parents[1] / 'backend/migrations').glob('[0-9][0-9][0-9]_*.sql')):
                if int(migration.name[:3]) > 39:
                    break
                database.execute(migration.read_text(), prepare=False)
                database.commit()
            database.execute('INSERT INTO settings SELECT * FROM json_populate_record(NULL::settings,%s::json)', (source_settings,))
        postgres_store.close_pools()
        with patch.dict(os.environ, {'TEMPO_DATABASE_WRITE_URL': historical_dsn, 'TEMPO_DATABASE_READ_URL': historical_dsn}):
            try:
                yield
            finally:
                postgres_store.close_pools()
    finally:
        with psycopg.connect(administrator_dsn, autocommit=True) as administrator:
            administrator.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))
    with postgres_store.connection(read_only=True) as database:
        assert [row[0] for row in database.execute_native('SELECT version FROM tempo_schema_migrations ORDER BY version').fetchall()] == current_ledger
    print('PASS test_issue107_historical_transition_rehearsal_preserves_current_migration_ledger')


def test_issue80_schema38_transition_upgrade_preserves_original_recovery_identity():
    from app.snapshot_reads import snapshot_rows
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from scripts.apply_postgres_migrations import apply_migrations
    from app.schema_version import POSTGRES_SCHEMA_VERSION
    with historical_transition_database(), fixture('upgrade-reservation') as (rep, other, lines, steps):
        plan, payload = ready_plan(rep, lines)
        assert execute(payload) == {'status': 'pending'}
        # All workload consumers are stopped by the owning disposable runner.
        # Recreate only migration039's predecessor, retaining actual application
        # and reservation data. No published migration is edited or skipped.
        migration38 = (root / 'backend/migrations/038_prefix_transition_application.sql').read_text()
        guard = migration38[migration38.index('CREATE FUNCTION guard_prefix_transition_scope('):migration38.index('CREATE FUNCTION guard_prefix_transition_write()')].replace('CREATE FUNCTION', 'CREATE OR REPLACE FUNCTION', 1)
        deletion37 = (root / 'backend/migrations/037_card_deletion.sql').read_text()
        deletion_guard = deletion37[deletion37.index('CREATE FUNCTION prevent_deleted_card_recreation()'):deletion37.index('CREATE TRIGGER deleted_card_recreation_guard')].replace('CREATE FUNCTION', 'CREATE OR REPLACE FUNCTION', 1)
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('DROP FUNCTION reserve_prefix_transition_queue_write() CASCADE')
            database.execute_native('DROP FUNCTION reserve_permanent_deletion_write() CASCADE')
            database.execute_native('DROP FUNCTION guard_permanent_deletion_scope() CASCADE')
            database.raw.execute(guard, prepare=False)
            database.raw.execute(deletion_guard, prepare=False)
        queries = [('SELECT * FROM prefix_transition_applications WHERE operation_id=%s', (payload['operation_id'],)),
                   ('SELECT * FROM prefix_transition_card_fences WHERE operation_id=%s', (payload['operation_id'],)),
                   ('SELECT * FROM prefix_transition_repertoire_fences WHERE operation_id=%s', (payload['operation_id'],)),
                   ('SELECT * FROM cards WHERE repertoire_id=ANY(%s)', ([rep, other],)),
                   ('SELECT review.* FROM reviews review JOIN cards card ON card.id=review.card_id WHERE card.repertoire_id=ANY(%s)', ([rep, other],))]
        with postgres_store.connection(read_only=True) as database:
            before = [snapshot_rows(database, query, parameters) for query, parameters in queries]
            versions_before = [row[0] for row in database.execute_native(
                'SELECT version FROM tempo_schema_migrations ORDER BY version')]
        # Replay this specific immutable guard upgrade atomically. Removing only
        # its receipt outside the transaction leaves a gap when later versions
        # exist. Later schema objects/receipts remain intact, and the normal
        # migration driver still validates the complete contiguous history.
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('DELETE FROM tempo_schema_migrations WHERE version=39')
            database.raw.execute((root / 'backend/migrations/039_prefix_transition_lock_budget.sql').read_text(),
                                 prepare=False)
        apply_migrations(os.environ['TEMPO_DATABASE_WRITE_URL'])
        apply_migrations(os.environ['TEMPO_DATABASE_WRITE_URL'])
        with postgres_store.connection(read_only=True) as database:
            assert before == [snapshot_rows(database, query, parameters) for query, parameters in queries]
            from app.schema_version import POSTGRES_SCHEMA_VERSION
            assert database.execute_native('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0] == POSTGRES_SCHEMA_VERSION
            assert [row[0] for row in database.execute_native(
                'SELECT version FROM tempo_schema_migrations ORDER BY version')] == versions_before
        final = publish(payload)
        assert final['operation_id'] == payload['operation_id'] and execute(payload) == final
    print('PASS test_issue80_schema38_transition_upgrade_preserves_original_recovery_identity')


def test_issue80_selected_caro_shortening_publishes_exact_graph_and_qgd_steady_state():
    with fixture('success') as (rep, other, lines, steps):
        plan, payload = ready_plan(rep, lines)
        before_qgd = [step for step in steps if step.line_id == lines[2]['id']]
        result = execute(payload)
        assert result == {'status': 'pending'},read_operation(payload['operation_id'])
        with postgres_store.connection(read_only=True) as database:
            assert database.execute_native('SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s', (rep,)).fetchone()[0] == 1
            assert all(row[0] == 2 for row in database.execute_native('SELECT learner_decision_count FROM repertoire_line_training_depths WHERE line_id=ANY(%s)', ([line['id'] for line in lines],)).fetchall())
        def fresh_defaults():
            with postgres_store.connection(read_only=True) as database:
                for card in plan.cards:
                    if card.lifecycle == 'create':
                        row = database.execute_native('SELECT * FROM cards WHERE id=%s', (card.card_id,)).fetchone()
                        assert row['state'] == json.loads(card.schedule_json)['state'] and row['due_date'] == plan.study_day, dict(row)
                        assert row['introduced_at'] is None and row['fsrs_card_json'] is None
        final = publish(payload, fresh_defaults)
        assert execute(payload) == final
        with postgres_store.connection(read_only=True) as database:
            current = prefix_evaluation_api.load_snapshot(rep, time.monotonic()+10)
            assert current.published_steps == plan.proposed_steps
            assert [step for step in current.published_steps if step.line_id == lines[2]['id']] == before_qgd
            depths = {row[0]: row[1] for row in database.execute_native('SELECT line_id,learner_decision_count FROM repertoire_line_training_depths WHERE line_id=ANY(%s)', ([line['id'] for line in lines],)).fetchall()}
            assert depths == {lines[0]['id']:1, lines[1]['id']:1, lines[2]['id']:2}
            for card in plan.cards:
                row = database.execute_native('SELECT * FROM cards WHERE id=%s', (card.card_id,)).fetchone()
                if card.lifecycle == 'create':
                    assert row['due_date'] == plan.study_day
                    assert row['fsrs_card_json'] is None and row['recent_attempts_json'] == '[]'
                    assert not database.execute_native('SELECT 1 FROM reviews WHERE card_id=%s', (card.card_id,)).fetchone()
                    assert not database.execute_native('SELECT 1 FROM opening_evidence_attempts WHERE card_id=%s', (card.card_id,)).fetchone()
                if card.lifecycle == 'archive':
                    assert row['archived'] == 1 and row['superseded_by'] is None
            assert not database.execute_native('SELECT 1 FROM prefix_transition_repertoire_fences WHERE operation_id=%s', (payload['operation_id'],)).fetchone()
        print('PASS test_issue80_selected_caro_shortening_publishes_exact_graph_and_qgd_steady_state')



def test_issue80_structural_fences_target_creation_source_edits_and_duplicate_plan_delivery():
    from fastapi import HTTPException
    with fixture('fences') as (rep, other, lines, steps):
        plan, payload = ready_plan(rep, lines)
        execute(payload)
        target = next(card for card in plan.cards if card.lifecycle == 'create')
        target_step = next(step for step in plan.proposed_steps if step.card_id == target.card_id)
        attempts = (
            ("UPDATE repertoire_lines SET name=name||' changed' WHERE id=%s", (lines[0]['id'],)),
            ("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,trained_color) VALUES(%s,%s,'prefix',%s,%s,%s,'black')",
             (target.card_id,other,target_step.starting_fen,json.dumps(target_step.moves),plan.study_day)),
            ('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)',(other,steps[0].card_id)),
            ("UPDATE repertoire_line_training_depths SET learner_decision_count=1 WHERE line_id=%s", (lines[0]['id'],)),
        )
        for statement, parameters in attempts:
            try:
                with postgres_store.connection(read_only=False) as database:
                    database.execute_native(statement,parameters)
            except psycopg.Error as error:
                assert error.sqlstate == 'P0080', error
            else:
                raise AssertionError('A structural producer bypassed the active transition fence')
        duplicate = dict(payload,operation_id=uuid.uuid4().hex)
        assert execute(duplicate) is None
        assert read_operation(duplicate['operation_id'])['error']['detail']['code'] == 'prefix_transition_in_progress'
        final = publish(payload)
        assert execute(payload) == final
        changed = dict(payload,request=dict(payload['request'],candidate_depths={line['id']:2 for line in lines[:2]}))
        try:
            execute_command(payload['operation_id'], application.COMMAND, changed)
        except CommandConflict:
            pass
        else:
            raise AssertionError('Changed command payload reused a completed receipt')
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('DELETE FROM operation_receipts WHERE operation_id=%s',(duplicate['operation_id'],))
        print('PASS test_issue80_structural_fences_target_creation_source_edits_and_duplicate_plan_delivery')


def test_issue80_authoritative_revalidation_rejects_source_and_absent_target_races_without_partial_mutation():
    from app import command_gateway
    original_prepare = application.prepare_application
    for mutation in ('source','target','history','membership','graph'):
        with fixture('stale-'+mutation) as (rep, other, lines, steps):
            plan, payload = ready_plan(rep, lines)
            def raced_prepare(command_payload):
                prepared = original_prepare(command_payload)
                with postgres_store.connection(read_only=False) as database:
                    if mutation == 'source':
                        database.execute_native("UPDATE repertoire_lines SET name=name||' concurrent' WHERE id=%s",(lines[0]['id'],))
                    elif mutation == 'target':
                        step = next(step for step in plan.proposed_steps if next(card for card in plan.cards if card.card_id==step.card_id).lifecycle=='create')
                        create_graph_cards(database,(step,),plan.study_day,strict=True)
                    elif mutation == 'history':
                        database.execute_native("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(%s,'correct',%s,0,1)",(steps[0].card_id,datetime.now(timezone.utc).isoformat()))
                    elif mutation == 'membership':
                        database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)',(other,steps[0].card_id))
                    else:
                        database.execute_native('UPDATE opening_graph_publications SET generation=2 WHERE repertoire_id=%s',(rep,))
                return prepared
            command_gateway._preparers[application.COMMAND] = raced_prepare
            try:
                assert execute(payload) is None
            finally:
                command_gateway._preparers[application.COMMAND] = original_prepare
            result = read_operation(payload['operation_id'])
            assert result['state']=='failed' and result['error']['detail']['code']=='stale_plan', result
            with postgres_store.connection(read_only=True) as database:
                assert not database.execute_native('SELECT 1 FROM prefix_transition_applications WHERE operation_id=%s',(payload['operation_id'],)).fetchone()
                assert all(row[0]==2 for row in database.execute_native('SELECT learner_decision_count FROM repertoire_line_training_depths WHERE line_id=ANY(%s)',([line['id'] for line in lines],)).fetchall())
                assert not database.execute_native('SELECT 1 FROM cards WHERE repertoire_id=%s AND archived=1',(rep,)).fetchone()
            with postgres_store.connection(read_only=False) as database:
                database.execute_native('DELETE FROM operation_receipts WHERE operation_id=%s',(payload['operation_id'],))
    print('PASS test_issue80_authoritative_revalidation_rejects_source_and_absent_target_races_without_partial_mutation')


def test_issue80_review_during_staging_rejects_activation_and_releases_fences():
    with fixture('staging-review') as (rep,other,lines,steps):
        plan,payload=ready_plan(rep,lines)
        execute(payload)
        drain(application.TASK_KIND,application.execute_application_slice)
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('UPDATE cards SET stability=stability+1 WHERE id=%s',(steps[0].card_id,))
        assert execute(payload) is None
        result=read_operation(payload['operation_id'])
        assert result['state']=='failed' and result['transition']['state']=='rejected',result
        with postgres_store.connection(read_only=False) as database:
            assert database.execute_native('SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s',(rep,)).fetchone()[0]==1
            database.execute_native("UPDATE repertoire_lines SET name=name||' usable' WHERE id=%s",(lines[0]['id'],))
    print('PASS test_issue80_review_during_staging_rejects_activation_and_releases_fences')

def test_issue80_concurrent_target_source_and_membership_writes_wait_then_conflict():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    original_lock=application.lock_and_revalidate
    for producer_kind in ('target','source','membership'):
        with fixture('concurrent-'+producer_kind) as (rep,other,lines,steps):
            plan,payload=ready_plan(rep,lines)
            step=next(step for step in plan.proposed_steps if next(card for card in plan.cards if card.card_id==step.card_id).lifecycle=='create')
            locked=threading.Event(); release=threading.Event(); producer_started=threading.Event(); producer_pids=[]
            def paused_lock(database,prepared):
                original_lock(database,prepared)
                locked.set()
                assert release.wait(5),'Structural producer did not reach its authoritative lock'
            def producer():
                try:
                    with postgres_store.connection(read_only=False) as database:
                        producer_pids.append(database.execute_native('SELECT pg_backend_pid()').fetchone()[0])
                        producer_started.set()
                        if producer_kind=='target':
                            # The destination repertoire has no repertoire fence:
                            # only the globally absent card-ID reservation protects it.
                            create_graph_cards(database,(replace(step,repertoire_id=other),),plan.study_day,strict=True)
                        elif producer_kind=='source':
                            database.execute_native("UPDATE repertoire_lines SET name=name||' concurrent' WHERE id=%s",(lines[0]['id'],))
                        else:
                            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)',(other,steps[0].card_id))
                except psycopg.Error as error:
                    return error.sqlstate
                return 'mutated'
            application.lock_and_revalidate=paused_lock
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    accepted=executor.submit(execute,payload)
                    assert locked.wait(5)
                    created=executor.submit(producer)
                    assert producer_started.wait(5)
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        with postgres_store.connection(read_only=True) as observer:
                            waiting=observer.execute_native("SELECT 1 FROM pg_stat_activity WHERE pid=%s AND wait_event_type='Lock'",(producer_pids[0],)).fetchone()
                        if waiting: break
                        time.sleep(0.01)
                    assert waiting,producer_kind+' never contended on its real PostgreSQL structural lock'
                    release.set()
                    assert accepted.result(timeout=5)=={'status':'pending'}
                    assert created.result(timeout=5)=='P0080',producer_kind
            finally:
                release.set(); application.lock_and_revalidate=original_lock
            publish(payload)
    print('PASS test_issue80_concurrent_target_source_and_membership_writes_wait_then_conflict')


def test_issue80_same_operation_concurrency_stale_slice_lost_response_and_activation_rollback():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from app import command_gateway
    original_prepare=application.prepare_application
    original_create=application.create_graph_cards
    with fixture('replay') as (rep,other,lines,steps):
        plan,payload=ready_plan(rep,lines)
        prepared=idle_call(lambda:original_prepare(payload))
        barrier=threading.Barrier(2)
        command_gateway._preparers[application.COMMAND]=lambda _: (barrier.wait(timeout=5),prepared)[1]
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results=list(executor.map(lambda _:execute(payload),range(2)))
            assert results==[{'status':'pending'},{'status':'pending'}]
        finally:
            command_gateway._preparers[application.COMMAND]=original_prepare
        first=idle_call(lambda:claim_task(application.TASK_KIND))
        assert first
        idle_call(lambda:application.execute_application_slice(first))
        assert idle_call(lambda:application.execute_application_slice(first)) is False,'Expired stage lease published twice'
        drain(application.TASK_KIND,application.execute_application_slice)
        def interrupted_create(database,batch,day,**options):
            original_create(database,batch,day,**options)
            raise psycopg.errors.SerializationFailure('Injected interruption during activation')
        application.create_graph_cards=interrupted_create
        try:
            try: execute(payload)
            except psycopg.errors.SerializationFailure: pass
            else: raise AssertionError('Activation interruption was not observed')
        finally:
            application.create_graph_cards=original_create
        with postgres_store.connection(read_only=True) as database:
            assert database.execute_native('SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s',(rep,)).fetchone()[0]==1
            assert not database.execute_native('SELECT id FROM cards WHERE id=ANY(%s)',([card.card_id for card in plan.cards if card.lifecycle=='create'],)).fetchall()
        # Commit activation but discard its transport result. Restart all pools;
        # the retained operation resumes publication without repeating mutation.
        execute(payload)
        postgres_store.close_pools()
        assert execute(payload)=={'status':'pending'},read_operation(payload['operation_id'])
        final=publish(payload)
        assert execute(payload)==final
        with postgres_store.connection(read_only=True) as database:
            assert database.execute_native('SELECT COUNT(*) FROM prefix_transition_applications WHERE operation_id=%s',(payload['operation_id'],)).fetchone()[0]==1
    print('PASS test_issue80_same_operation_concurrency_stale_slice_lost_response_and_activation_rollback')

def test_issue80_shared_history_seed_implicit_owner_and_authored_checkpoint_reuse_are_preserved():
    from app.services.review_service import apply_scheduling_review
    from app.services.prefix_transition import SCHEDULE_COLUMNS
    with fixture('history') as (rep,other,lines,steps):
        root=steps[0].card_id
        shortened=build_graph(GraphInput(rep,tuple(dict(line,learner_decision_count=1) for line in lines[:2]),1))[0]
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)',(other,root))
            apply_scheduling_review(database,root,'correct',guided=False,source_kind='study',source_ref=rep,light_first_interval_days=7,reviewed_at=datetime.now(timezone.utc),review_day=date.today())
            create_graph_cards(database,(shortened,),date.today().isoformat(),strict=True)
            database.execute_native("UPDATE cards SET repertoire_id=%s,canonical_route_source=1,kind='checkpoint' WHERE id=%s",(other,shortened.card_id))
            database.execute_native("UPDATE cards SET state='learning',introduced_at=%s WHERE id=%s",(date.today().isoformat(),shortened.card_id))
            # No explicit other membership: preserve the authored implicit owner.
            database.execute_native('INSERT INTO opening_card_schedule_seeds(card_id,source_card_id,baseline_successful_days,baseline_recent_clean,verification_due,created_at) VALUES(%s,%s,2,1,%s,%s)',(shortened.card_id,root,date.today().isoformat(),datetime.now(timezone.utc).isoformat()))
        def retained():
            with postgres_store.connection(read_only=True) as database:
                return {table:[dict(row) for row in database.execute_native(query,([root,shortened.card_id],)).fetchall()] for table,query in {
                    'reviews':'SELECT * FROM reviews WHERE card_id=ANY(%s) ORDER BY id',
                    'seeds':'SELECT * FROM opening_card_schedule_seeds WHERE card_id=ANY(%s) ORDER BY card_id',
                    'schedules':'SELECT '+','.join(('id',*SCHEDULE_COLUMNS))+' FROM cards WHERE id=ANY(%s) ORDER BY id',
                }.items()}
        before=retained()
        plan,payload=ready_plan(rep,lines)
        assert next(card for card in plan.cards if card.card_id==root).classification=='retained_shared'
        assert next(card for card in plan.cards if card.card_id==shortened.card_id).kind_after=='checkpoint'
        execute(payload);publish(payload)
        assert retained()==before,{'before':before,'after':retained()}
        with postgres_store.connection(read_only=True) as database:
            row=database.execute_native('SELECT repertoire_id,archived FROM cards WHERE id=%s',(root,)).fetchone()
            assert tuple(row)==(other,0)
            assert database.execute_native('SELECT canonical_route_source FROM repertoire_cards WHERE repertoire_id=%s AND card_id=%s',(other,root)).fetchone()[0]==1
            assert database.execute_native('SELECT kind,canonical_route_source,repertoire_id FROM cards WHERE id=%s',(shortened.card_id,)).fetchone()[0]=='checkpoint'
    print('PASS test_issue80_shared_history_seed_implicit_owner_and_authored_checkpoint_reuse_are_preserved')


def test_issue80_retired_queued_active_partial_pending_and_offline_presentations_never_grade_replacements():
    from app.services.opening_decision_evidence import decision_manifest
    from app.services.postgres_opening_evidence import persist_checkpoint,prepare_standalone_checkpoint,commit_standalone_checkpoint
    from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
    from app.review_commands import submit_review
    from app.command_gateway import request_digest
    with fixture('attempts') as (rep,other,lines,steps):
        root=steps[0].card_id
        now=datetime.now(timezone.utc).isoformat()
        with postgres_store.connection(read_only=False) as database:
            queue=database.execute_native("SELECT id FROM daily_queue WHERE queue_date=%s AND card_id=%s AND status='queued' ORDER BY id LIMIT 1",(date.today().isoformat(),root)).fetchone()[0]
            snapshot=database.execute_native('SELECT * FROM opening_evidence_presentations WHERE card_id=%s ORDER BY id DESC LIMIT 1',(root,)).fetchone()
        manifest=decision_manifest(dict(snapshot),rep)
        def checkpoint(label,queue_id=queue):
            return {'attempt_id':rep+'-'+label,'manifest':manifest,'origin_queue_entry_id':queue_id,'queue_entry_id':queue_id,'parent_attempt_id':None,'started_at':now,'study_timezone':'America/New_York','source':'offline','events':[],'terminal':None}
        done=checkpoint('completed');decision=manifest['decisions'][0]
        done['events']=[{'sequence':1,'decision_index':0,'decision_id':decision['decision_id'],'expected_uci':decision['expected_uci'],'kind':'first_response','observed_at':now,'response_uci':decision['expected_uci'],'disposition':'expected','assistance':None}]
        done['terminal']={'state':'complete','final_sequence':1,'ended_at':now}
        review={'card_id':root,'review':{'outcome':'correct','queue_entry_id':queue,'attempt_id':done['attempt_id'],'recorded_at':now,'opening_evidence_completion':done},'prepared_manifest':manifest}
        with postgres_store.connection(read_only=False) as database:
            completed=submit_review(database,review)
            second=database.execute_native("INSERT INTO daily_queue(queue_date,card_id,cycle,position,card_bucket,admission_repertoire_id) VALUES(%s,%s,99,1000,'opening',%s) RETURNING id",(date.today().isoformat(),root,rep)).fetchone()[0]
            active=checkpoint('active',second);partial=checkpoint('partial',second)
            partial['terminal']={'state':'partial','final_sequence':0,'ended_at':now}
            persist_checkpoint(database,{'checkpoint':active,'prepared_manifest':manifest})
            persist_checkpoint(database,{'checkpoint':partial,'prepared_manifest':manifest})
            pending_id=rep+'-pending-review';pending={'card_id':root,'review':{'outcome':'correct','queue_entry_id':second,'attempt_id':rep+'-pending'}}
            database.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,background) VALUES(%s,'cards.review',%s,'blocked',%s,false)",(pending_id,request_digest('cards.review',pending),json.dumps(pending)))
        # Persist an unfinished study self-assessment linked to the same original
        # card, so retirement covers both durable attempt formats in PostgreSQL.
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('INSERT INTO studies(id,title,created_at,updated_at) VALUES(%s,%s,%s,%s)',(rep,rep,now,now))
            database.execute_native('INSERT INTO study_chapters(id,study_id,title,position) VALUES(%s,%s,%s,0)',(rep+'-chapter',rep,rep))
            database.execute_native("INSERT INTO study_sources(id,chapter_id,source_group_id,version,raw_pgn,sha256,filename,record_index,headers_json,diagnostics_json,valid,created_at) VALUES(%s,%s,%s,1,'','fixture','fixture.pgn',0,'{}','[]',1,%s)",(rep+'-source',rep+'-chapter',rep,now))
            database.execute_native("INSERT INTO study_positions(id,source_id,child_index,fen,history_json,node_path) VALUES(%s,%s,0,%s,'[]','root')",(rep+'-position',rep+'-source',lines[0]['start_fen']))
            database.execute_native("INSERT INTO study_exercises(id,study_id,position_id,status,created_at,updated_at) VALUES(%s,%s,%s,'published',%s,%s)",(rep+'-exercise',rep,rep+'-position',now,now))
            database.execute_native("INSERT INTO study_attempts(id,exercise_id,revision,card_id,queue_entry_id,cycle,context,answer_json,answer_hash,assessment_json,assessment_method,grader_version,started_at,committed_at) VALUES(%s,%s,1,%s,%s,99,'review','{}','fixture','{\"outcome\":\"needs_self_assessment\"}','self',1,%s,%s)",(rep+'-study-attempt',rep+'-exercise',root,second,now,now))
        prepared=prepare_standalone_checkpoint({'checkpoint':active})
        plan,payload=ready_plan(rep,lines)
        assert next(card for card in plan.cards if card.card_id==root).lifecycle=='archive'
        execute(payload);publish(payload)
        with postgres_store.connection(read_only=False) as database:
            assert submit_review(database,review)==completed,'Matching completed receipt did not replay its old result'
            for attempt_id in (active['attempt_id'],partial['attempt_id']):
                row=database.execute_native('SELECT retired_operation_id,completed_at FROM opening_evidence_attempts WHERE attempt_id=%s',(attempt_id,)).fetchone()
                assert tuple(row)==(payload['operation_id'],None)
            assert database.execute_native('SELECT retired_operation_id FROM opening_evidence_attempts WHERE attempt_id=%s',(done['attempt_id'],)).fetchone()[0] is None
            study_attempt=database.execute_native('SELECT retired_operation_id,finalized_at,result_json FROM study_attempts WHERE id=%s',(rep+'-study-attempt',)).fetchone()
            assert tuple(study_attempt)==(payload['operation_id'],None,None)
            from app.study_attempt_commands import self_assess_study_attempt
            try: self_assess_study_attempt(database,{'attempt_id':rep+'-study-attempt','exercise_id':rep+'-exercise','study_id':rep,'assessment':{'rating':'correct'}})
            except HTTPException as error: assert error.status_code==409 and 'retired' in error.detail
            else: raise AssertionError('Retired study self-assessment graded replacement')

            assert database.execute_native('SELECT status FROM daily_queue WHERE id=%s',(second,)).fetchone()[0]=='superseded'
            assert database.execute_native('SELECT 1 FROM queue_attempt_origins WHERE queue_entry_id=%s',(second,)).fetchone()
            try: commit_standalone_checkpoint(database,prepared)
            except HTTPException as error: assert error.detail['code']=='card_archived' and not error.detail['aggregate_review_allowed']
            else: raise AssertionError('Old checkpoint wrote replacement evidence')
            try: submit_review(database,pending)
            except HTTPException as error: assert error.status_code==409
            else: raise AssertionError('Offline old-card submission graded a replacement')
            database.execute_native('DELETE FROM operation_receipts WHERE operation_id=%s',(pending_id,))
        for card in plan.cards:
            if card.lifecycle=='create':
                with postgres_store.connection(read_only=True) as database:
                    assert not database.execute_native('SELECT 1 FROM reviews WHERE card_id=%s',(card.card_id,)).fetchone()
        assert read_operation(pending_id)['state']=='unknown'
    print('PASS test_issue80_retired_queued_active_partial_pending_and_offline_presentations_never_grade_replacements')

def test_issue80_publication_failure_keeps_activation_and_retry_resumes_linked_tasks():
    from app.services.durable_tasks import fail_task
    from app.command_gateway import record_operation_attempt
    with fixture('publication-failure') as (rep,other,lines,steps):
        plan,payload=ready_plan(rep,lines)
        execute(payload);drain(application.TASK_KIND,application.execute_application_slice);execute(payload)
        graph=idle_call(lambda:claim_task('opening_graph_rebuild'))
        assert graph and graph['payload']['repertoire_id']==rep
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('UPDATE background_tasks SET attempt_count=max_attempts WHERE id=%s',(graph['id'],))
        assert fail_task(graph['id'],graph['generation'],graph['lease_token'],RuntimeError('Injected publication interruption'))['state']=='failed'
        state=read_operation(payload['operation_id'])
        assert state['state']=='blocked' and state['transition']['state']=='recovery_required' and state['transition']['recovery']
        with postgres_store.connection(read_only=True) as database:
            assert database.execute_native('SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s',(rep,)).fetchone()[0]==2
            assert database.execute_native('SELECT 1 FROM prefix_transition_card_fences WHERE operation_id=%s',(payload['operation_id'],)).fetchone()
        should_execute,saved,token,_=record_operation_attempt(payload['operation_id'],application.COMMAND,payload,background=False,expected_retry_cycle=state['retry_cycle'])
        assert should_execute
        idle_call(lambda:execute_command(payload['operation_id'],application.COMMAND,saved,attempt_token=token))
        final=publish(payload)
        assert final['graph_generation']==2
    print('PASS test_issue80_publication_failure_keeps_activation_and_retry_resumes_linked_tasks')


def test_issue80_midnight_recovery_keeps_approved_due_dates_and_publishes_current_queue():
    from datetime import timedelta
    original_date=application.date
    class Tomorrow(date):
        @classmethod
        def today(cls): return original_date.today()+timedelta(days=1)
    with fixture('midnight') as (rep,other,lines,steps):
        plan,payload=ready_plan(rep,lines)
        execute(payload);drain(application.TASK_KIND,application.execute_application_slice);execute(payload)
        application.date=Tomorrow
        try:
            final=publish(payload)
            assert final['queue_date']==Tomorrow.today().isoformat()
            with postgres_store.connection(read_only=True) as database:
                for card in plan.cards:
                    if card.lifecycle=='create':
                        assert database.execute_native('SELECT due_date FROM cards WHERE id=%s',(card.card_id,)).fetchone()[0]==plan.study_day
        finally:
            application.date=original_date
    print('PASS test_issue80_midnight_recovery_keeps_approved_due_dates_and_publishes_current_queue')


def seed_retained_applications():
    from app.command_gateway import record_operation_attempt
    for phase in ('before','activated'):
        rep,other,lines,steps=create_fixture('backup-'+phase)
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('UPDATE repertoires SET new_cards_per_day=0 WHERE id=ANY(%s)',([rep,other],))
        plan,payload=ready_plan(rep,lines)
        should_execute,saved,token,_=record_operation_attempt(payload['operation_id'],application.COMMAND,payload,background=False)
        assert should_execute
        idle_call(lambda:execute_command(payload['operation_id'],application.COMMAND,saved,attempt_token=token))
        if phase=='activated':
            drain(application.TASK_KIND,application.execute_application_slice)
            execute(payload)
        print(json.dumps({'retained_prefix_application':payload['operation_id'],'phase':phase,'repertoire_id':rep}))


def recover_retained_applications(cleanup=False,verify_only=False):
    with postgres_store.connection(read_only=True) as database:
        rows=database.execute_native("SELECT receipt.payload_json FROM operation_receipts receipt JOIN prefix_transition_applications application USING(operation_id) WHERE application.repertoire_id LIKE %s ORDER BY application.repertoire_id",('issue80-backup-%',)).fetchall()
    assert len(rows)==2,'Both pre-activation and post-activation durable fixtures must survive'
    for row in rows:
        payload=json.loads(row[0])
        result=read_operation(payload['operation_id'])
        if verify_only:
            deadline=time.monotonic()+90
            while result['state'] not in {'complete','failed','blocked'} and time.monotonic()<deadline:
                time.sleep(0.05)
                result=read_operation(payload['operation_id'])
            assert result['state']=='complete',result
        if result['state']!='complete':
            final=publish(payload)
        else:
            final=result['response']
        assert execute(payload)==final
        assert final['graph_generation']==2
        if cleanup:
            cleanup_fixture(payload['repertoire_id'],payload['repertoire_id']+'-shared')
    print('PASS test_issue80_retained_pre_and_post_activation_applications_recover_after_recreation_or_restore')

def test_issue80_staging_failure_releases_fences_without_product_activation_and_noops_replay():
    from app.services.durable_tasks import fail_task
    with fixture('stage-failure') as (rep,other,lines,steps):
        plan,payload=ready_plan(rep,lines)
        execute(payload)
        task=idle_call(lambda:claim_task(application.TASK_KIND))
        assert task
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('UPDATE background_tasks SET attempt_count=max_attempts WHERE id=%s',(task['id'],))
        assert fail_task(task['id'],task['generation'],task['lease_token'],RuntimeError('Injected interruption before staging'))['state']=='failed'
        state=read_operation(payload['operation_id'])
        assert state['state']=='failed' and state['transition']['state']=='rejected',state
        with postgres_store.connection(read_only=False) as database:
            assert database.execute_native('SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s',(rep,)).fetchone()[0]==1
            assert not database.execute_native('SELECT 1 FROM prefix_transition_card_fences WHERE operation_id=%s',(payload['operation_id'],)).fetchone()
            database.execute_native("UPDATE repertoire_lines SET name=name||' usable' WHERE id=%s",(lines[0]['id'],))
        source=idle_call(lambda:prefix_evaluation_api.load_snapshot(rep,time.monotonic()+10))
        for selection,candidates in (([],{}),([lines[0]['id']],{lines[0]['id']:2})):
            request={'snapshot_id':prefix_evaluation_api.snapshot_identity(source),'selected_line_ids':selection,'candidate_depths':candidates}
            noop=idle_call(lambda:prefix_transition_api.prefix_transition_plan(rep,prefix_transition_api.PrefixTransitionRequest(**request)))
            request.update({field:getattr(noop,field) for field in ('plan_id','transition_snapshot_id','graph_generation','study_day')})
            envelope={'operation_id':uuid.uuid4().hex,'repertoire_id':rep,'request':request}
            final=execute(envelope)
            assert final['no_op'] and execute(envelope)==final
            original_source=prefix_evaluation_api.load_snapshot
            prefix_evaluation_api.load_snapshot=lambda *_args,**_options: (_ for _ in ()).throw(AssertionError('Completed no-op replay replanned'))
            try: assert execute(envelope)==final
            finally: prefix_evaluation_api.load_snapshot=original_source
            with postgres_store.connection(read_only=False) as database:
                assert not database.execute_native('SELECT 1 FROM prefix_transition_applications WHERE operation_id=%s',(envelope['operation_id'],)).fetchone()
                database.execute_native('DELETE FROM operation_receipts WHERE operation_id=%s',(envelope['operation_id'],))
    print('PASS test_issue80_staging_failure_releases_fences_without_product_activation_and_noops_replay')

def test_issue80_unclean_source_integrity_rejects_before_acceptance():
    with fixture('unchecked-source') as (rep,other,lines,steps):
        plan,payload=ready_plan(rep,lines)
        with postgres_store.connection(read_only=False) as database:
            database.execute_native("UPDATE repertoire_integrity_state SET status='unchecked' WHERE repertoire_id=%s",(rep,))
        assert execute(payload) is None
        result=read_operation(payload['operation_id'])
        assert result['error']['detail']['code']=='source_integrity_not_ready',result
        with postgres_store.connection(read_only=False) as database:
            assert not database.execute_native('SELECT 1 FROM prefix_transition_applications WHERE operation_id=%s',(payload['operation_id'],)).fetchone()
            database.execute_native('DELETE FROM operation_receipts WHERE operation_id=%s',(payload['operation_id'],))
    print('PASS test_issue80_unclean_source_integrity_rejects_before_acceptance')


def wait_for_staged_http_application(operation_id, redeliver, deadline):
    """Drive this receipt's eligible retry while the proof's scheduler is stopped."""
    redelivered_retry_at = None
    result = read_operation(operation_id)
    while result['state'] in {'unknown', 'queued', 'executing', 'retrying'} and time.monotonic() < deadline:
        if result['state'] == 'retrying':
            assert not result.get('last_error') and result['attempt_count'] == result['cycle_attempt_count'] == 0, result
            eligible_at = datetime.fromisoformat(result['next_retry_at'])
            if (eligible_at <= datetime.now(timezone.utc)
                    and result['next_retry_at'] != redelivered_retry_at):
                redeliver()
                redelivered_retry_at = result['next_retry_at']
        time.sleep(0.01)
        result = read_operation(operation_id)
    assert result['state'] == 'pending' and result['transition']['state'] == 'staging', result
    return result


def test_issue80_reader_only_deployed_api_dispatches_pending_apply_and_replays_final_result(*, force_foreground_yield=False):
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from app.services.redis_admission_gate import foreground_lease
    with fixture('http') as (rep,other,lines,steps):
        plan,payload=ready_plan(rep,lines)
        path=f'http://api:8000/api/repertoires/{rep}/prefix-transition/apply'
        def request(body):
            return Request(path,method='POST',headers={'Content-Type':'application/json','Idempotency-Key':payload['operation_id']},data=json.dumps(body).encode())
        def dispatch_original():
            with urlopen(request(payload['request']),timeout=10) as response:
                assert response.status==202 and response.headers['Location']==f"/api/operations/{payload['operation_id']}"
                assert json.load(response)['operation_id']==payload['operation_id']
        deadline=time.monotonic()+10
        with foreground_lease() if force_foreground_yield else nullcontext():
            dispatch_original()
            if force_foreground_yield:
                result=read_operation(payload['operation_id'])
                while result['state'] in {'unknown','queued','executing'} and time.monotonic()<deadline:
                    time.sleep(0.01);result=read_operation(payload['operation_id'])
                assert result['state']=='retrying' and result['attempt_count']==result['cycle_attempt_count']==0,result
        staged=wait_for_staged_http_application(payload['operation_id'],dispatch_original,deadline)
        if force_foreground_yield:
            assert staged['attempt_count']==staged['cycle_attempt_count']==1,staged
        final=publish(payload)
        with urlopen(request(payload['request']),timeout=10) as response:
            assert response.status==200 and json.load(response)==final
        changed=dict(payload['request'],candidate_depths={line['id']:2 for line in lines[:2]})
        try: urlopen(request(changed),timeout=10)
        except HTTPError as error: assert error.code==409
        else: raise AssertionError('Reader API accepted changed command identity after completion')
    if not force_foreground_yield:
        print('PASS test_issue80_reader_only_deployed_api_dispatches_pending_apply_and_replays_final_result')


def test_issue80_deployed_apply_recovers_original_identity_after_foreground_preemption():
    test_issue80_reader_only_deployed_api_dispatches_pending_apply_and_replays_final_result(force_foreground_yield=True)
    print('PASS test_issue80_deployed_apply_recovers_original_identity_after_foreground_preemption')

@contextmanager
def measure_application_transactions():
    """Count real client SQL, including pipelined executemany, through commit."""
    from app import command_gateway
    original_writer_connection = command_gateway._writer_connection
    measurements = []

    class MeasuredCursor:
        def __init__(self, cursor, measurement):
            self.cursor = cursor
            self.measurement = measurement

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *arguments):
            return self.cursor.__exit__(*arguments)

        def execute(self, statement, parameters=()):
            self.measurement['client_calls'] += 1
            self.measurement['sql_statements'] += 1
            return self.cursor.execute(statement, parameters)

        def executemany(self, statement, parameters):
            self.measurement['client_calls'] += 1
            self.measurement['sql_statements'] += len(parameters)
            return self.cursor.executemany(statement, parameters)

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class MeasuredDatabase:
        def __init__(self, database, measurement):
            self.database = database
            self.measurement = measurement

        @property
        def raw(self):
            from types import SimpleNamespace
            return SimpleNamespace(cursor=lambda: MeasuredCursor(self.database.raw.cursor(), self.measurement),
                                   execute=self.execute_native)

        def execute_native(self, statement, parameters=()):
            self.measurement['client_calls'] += 1
            self.measurement['sql_statements'] += 1
            result = self.database.execute_native(statement, parameters)
            if "pg_try_advisory_xact_lock(hashtextextended('tempo:prefix-transition:reservations'" in statement:
                self.measurement['barrier_acquired_at'] = time.perf_counter()
            if statement.startswith('INSERT INTO prefix_transition_applications'):
                self.measurement['phase'] = 'acceptance'
            if "UPDATE prefix_transition_applications SET state='publishing'" in statement:
                self.measurement['phase'] = 'activation'
            return result

        def execute(self, statement, parameters=()):
            self.measurement['client_calls'] += 1
            self.measurement['sql_statements'] += 1
            return self.database.execute(statement, parameters)

        def __getattr__(self, name):
            return getattr(self.database, name)

    @contextmanager
    def measured_writer_connection(background):
        measurement = dict(sql_statements=0, client_calls=0)
        with original_writer_connection(background) as database:
            started = time.perf_counter()
            yield MeasuredDatabase(database, measurement)
        completed = time.perf_counter()
        if 'phase' in measurement:
            measurement['transaction_seconds'] = completed - started
            measurement['barrier_seconds'] = completed - measurement.pop('barrier_acquired_at')
            measurements.append(measurement)

    command_gateway._writer_connection = measured_writer_connection
    try:
        yield measurements
    finally:
        command_gateway._writer_connection = original_writer_connection


def test_pr102_activation_sql_statement_count_is_independent_of_transition_size():
    measurements = []
    for obsolete_count in (128, 512):
        with fixture('activation-scale-' + str(obsolete_count), source_line_count=obsolete_count) as (rep, other, lines, steps):
            plan, payload = ready_plan(rep, lines, selected_line_count=obsolete_count)
            obsolete_ids = [membership.card_id for membership in plan.memberships if membership.action == 'obsolete']
            created_ids = [card.card_id for card in plan.cards if card.lifecycle == 'create']
            assert len(set(obsolete_ids)) == obsolete_count, 'Scaling fixture must actually retire generated memberships'
            assert len(set(created_ids)) == 2 * obsolete_count, 'Scaling fixture must create substantial replacements'
            with measure_application_transactions() as recorded:
                assert execute(payload) == {'status': 'pending'}
                drain(application.TASK_KIND, application.execute_application_slice)
                assert execute(payload) == {'status': 'pending'}
            assert [row['phase'] for row in recorded] == ['acceptance', 'activation'], recorded
            for row in recorded:
                row.update(obsolete_memberships=obsolete_count, replacement_cards=len(created_ids))
                print(json.dumps({'pr102_activation_measurement': row}), flush=True)
            measurements.extend(recorded)
            with postgres_store.connection(read_only=True) as database:
                assert database.execute_native('SELECT COUNT(*) FROM repertoire_cards WHERE repertoire_id=%s AND card_id=ANY(%s)', (rep, obsolete_ids)).fetchone()[0] == 0
                assert database.execute_native('SELECT COUNT(*) FROM cards WHERE id=ANY(%s) AND archived=1', (obsolete_ids,)).fetchone()[0] == obsolete_count
                assert database.execute_native('SELECT COUNT(*) FROM cards WHERE id=ANY(%s) AND archived=0', (created_ids,)).fetchone()[0] == len(created_ids)
                assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=ANY(%s)', (created_ids,)).fetchone()[0] == 0
                assert database.execute_native('SELECT COUNT(*) FROM opening_card_schedule_seeds WHERE card_id=ANY(%s)', (created_ids,)).fetchone()[0] == 0
                expected_schedules = {card.card_id: json.loads(card.schedule_json)['state'] for card in plan.cards if card.lifecycle == 'create'}
                assert {row[0]: row[1] for row in database.execute_native('SELECT id,state FROM cards WHERE id=ANY(%s)', (created_ids,)).fetchall()} == expected_schedules
            # A separate real transaction immediately obtains the exclusive
            # counterpart and performs an ordinary queue statement after commit.
            with postgres_store.connection(read_only=False) as database:
                assert database.execute_native("SELECT pg_try_advisory_xact_lock(hashtextextended('tempo:prefix-transition:reservations',0))").fetchone()[0]
                database.execute_native("UPDATE daily_queue SET status=status WHERE card_id=ANY(%s)", (created_ids,))
    for phase in ('acceptance', 'activation'):
        phase_measurements = [row for row in measurements if row['phase'] == phase]
        assert len({row['sql_statements'] for row in phase_measurements}) == 1, phase_measurements
        assert len({row['client_calls'] for row in phase_measurements}) == 1, phase_measurements
    print('PASS test_pr102_activation_sql_statement_count_is_independent_of_transition_size')


def test_pr102_bulk_cleanup_preserves_authored_shared_history_and_owner_semantics():
    from app.services.postgres_opening_graph import remove_obsolete_graph_memberships
    from app.services.review_service import apply_scheduling_review
    from app.services.prefix_transition import SCHEDULE_COLUMNS
    extra_repertoire_ids = []
    with fixture('bulk-cleanup', source_line_count=8, extra_repertoire_ids=extra_repertoire_ids) as (rep, other, lines, steps):
        card_ids = [step.card_id for step in steps]
        ordinary, second_ordinary, shared, authored_link, implicit_owner, current, authored_orphan, shared_tail = card_ids
        with postgres_store.connection(read_only=False) as database:
            first_retained_owner = rep + '-aaa'
            extra_repertoire_ids.append(first_retained_owner)
            database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'cleanup.pgn',%s)", (first_retained_owner, first_retained_owner, datetime.now(timezone.utc).isoformat()))
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) SELECT %s,card_id,1 FROM unnest(%s::text[]) card_id', (other, [shared, shared_tail]))
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)', (first_retained_owner, shared))
            database.execute_native('UPDATE repertoire_cards SET canonical_route_source=1 WHERE repertoire_id=%s AND card_id=%s', (rep, authored_link))
            database.execute_native('UPDATE cards SET canonical_route_source=1,repertoire_id=%s WHERE id=%s', (other, implicit_owner))
            database.execute_native('UPDATE cards SET canonical_route_source=1 WHERE id=%s', (authored_orphan,))
            database.execute_native('INSERT INTO opening_graph_steps SELECT repertoire_id,2,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json FROM opening_graph_steps WHERE repertoire_id=%s AND generation=1 AND card_id=%s', (rep, current))
            for identifier in card_ids:
                apply_scheduling_review(database, identifier, 'correct', guided=False, source_kind='study',
                                        source_ref=rep, light_first_interval_days=7,
                                        reviewed_at=datetime.now(timezone.utc), review_day=date.today())
            database.execute_native('INSERT INTO opening_card_schedule_seeds(card_id,source_card_id,baseline_successful_days,baseline_recent_clean,verification_due,created_at) VALUES(%s,%s,2,1,%s,%s)', (shared, ordinary, date.today().isoformat(), datetime.now(timezone.utc).isoformat()))
            database.execute_native("UPDATE daily_queue SET status='queued' WHERE card_id=ANY(%s)", (card_ids,))
            database.execute_native("INSERT INTO daily_queue(queue_date,card_id,position,status) SELECT %s,card_id,0,'queued' FROM unnest(%s::text[]) card_id ON CONFLICT(queue_date,card_id,cycle) DO UPDATE SET status='queued'", (date.today().isoformat(), card_ids))
        def history():
            with postgres_store.connection(read_only=True) as database:
                return {table: [dict(row) for row in database.execute_native(query, (card_ids,)).fetchall()]
                        for table, query in {
                            'reviews': 'SELECT * FROM reviews WHERE card_id=ANY(%s) ORDER BY id',
                            'seeds': 'SELECT * FROM opening_card_schedule_seeds WHERE card_id=ANY(%s) ORDER BY card_id',
                            'schedules': 'SELECT ' + ','.join(('id', *SCHEDULE_COLUMNS)) + ' FROM cards WHERE id=ANY(%s) ORDER BY id',
                        }.items()}
        before = history()
        with postgres_store.connection(read_only=False) as database:
            remove_obsolete_graph_memberships(database, rep, 2, card_ids)
        assert history() == before
        with postgres_store.connection(read_only=True) as database:
            retained_links = {row[0] for row in database.execute_native('SELECT card_id FROM repertoire_cards WHERE repertoire_id=%s', (rep,)).fetchall()}
            assert retained_links == {authored_link, current}
            cards = {row['id']: dict(row) for row in database.execute_native('SELECT id,repertoire_id,archived FROM cards WHERE id=ANY(%s)', (card_ids,)).fetchall()}
            assert {identifier for identifier, row in cards.items() if row['archived']} == {ordinary, second_ordinary, authored_orphan}
            assert cards[shared]['repertoire_id'] == first_retained_owner
            assert all(cards[identifier]['repertoire_id'] == other for identifier in (shared_tail, implicit_owner))
            superseded = {row[0] for row in database.execute_native("SELECT card_id FROM daily_queue WHERE card_id=ANY(%s) AND status='superseded'", (card_ids,)).fetchall()}
            assert superseded == {ordinary, second_ordinary, authored_orphan}
    print('PASS test_pr102_bulk_cleanup_preserves_authored_shared_history_and_owner_semantics')


def test_pr102_bulk_activation_rolls_back_and_releases_reservation_barrier():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    original_cleanup = application.remove_obsolete_graph_memberships
    original_publish = application.publish_graph_generation
    with fixture('bulk-rollback') as (rep, other, lines, steps):
        plan, payload = ready_plan(rep, lines)
        execute(payload)
        drain(application.TASK_KIND, application.execute_application_slice)
        def product_state():
            with postgres_store.connection(read_only=True) as database:
                return {table: [dict(row) for row in database.execute_native(query, (rep,)).fetchall()]
                        for table, query in {
                            'cards': 'SELECT * FROM cards WHERE repertoire_id=%s ORDER BY id',
                            'memberships': 'SELECT * FROM repertoire_cards WHERE repertoire_id=%s ORDER BY card_id',
                            'depths': 'SELECT depth.* FROM repertoire_line_training_depths depth JOIN repertoire_lines line ON line.id=depth.line_id WHERE line.repertoire_id=%s ORDER BY line_id',
                            'queue': 'SELECT queue.* FROM daily_queue queue JOIN cards card ON card.id=queue.card_id WHERE card.repertoire_id=%s ORDER BY queue.id',
                            'publication': 'SELECT * FROM opening_graph_publications WHERE repertoire_id=%s',
                        }.items()}
        before = product_state()
        for failure_boundary in ('after_cleanup', 'before_publication'):
            def interrupted_cleanup(*arguments):
                original_cleanup(*arguments)
                raise psycopg.errors.SerializationFailure('Injected interruption after bulk cleanup')
            def interrupted_publish(*_arguments):
                raise psycopg.errors.SerializationFailure('Injected interruption before publication')
            application.remove_obsolete_graph_memberships = interrupted_cleanup if failure_boundary == 'after_cleanup' else original_cleanup
            application.publish_graph_generation = interrupted_publish if failure_boundary == 'before_publication' else original_publish
            try:
                try:
                    execute(payload)
                except psycopg.errors.SerializationFailure:
                    pass
                else:
                    raise AssertionError('Injected bulk activation interruption was not observed')
            finally:
                application.remove_obsolete_graph_memberships = original_cleanup
                application.publish_graph_generation = original_publish
            assert product_state() == before, failure_boundary
            with postgres_store.connection(read_only=False) as database:
                assert database.execute_native("SELECT pg_try_advisory_xact_lock(hashtextextended('tempo:prefix-transition:reservations',0))").fetchone()[0]
                database.execute_native('UPDATE daily_queue SET status=status WHERE card_id=%s', (steps[0].card_id,))

        activation_reached = threading.Event()
        allow_commit = threading.Event()
        def held_cleanup(*arguments):
            original_cleanup(*arguments)
            activation_reached.set()
            assert allow_commit.wait(timeout=5), 'Activation concurrency proof was not released'
        application.remove_obsolete_graph_memberships = held_cleanup
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                activation = executor.submit(execute, payload)
                try:
                    assert activation_reached.wait(timeout=5)
                    with postgres_store.connection(read_only=False) as database:
                        assert not database.execute_native("SELECT pg_try_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))").fetchone()[0]
                finally:
                    allow_commit.set()
                assert activation.result(timeout=5) == {'status': 'pending'}
        finally:
            application.remove_obsolete_graph_memberships = original_cleanup
        with postgres_store.connection(read_only=False) as database:
            assert database.execute_native("SELECT pg_try_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))").fetchone()[0]
            database.execute_native('UPDATE daily_queue SET status=status WHERE card_id=%s', (steps[0].card_id,))
        assert publish(payload)['operation_id'] == payload['operation_id']
    print('PASS test_pr102_bulk_activation_rolls_back_and_releases_reservation_barrier')


def main():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Prefix application proofs require the disposable runner')
    os.environ['TZ']='America/New_York'
    time.tzset()
    os.environ['TEMPO_DATABASE_WRITE_URL'] = os.getenv('TEMPO_PREFIX_APPLICATION_PROOF_URL','postgresql://postgres@postgres:5432/tempo')
    os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
    test_postgres_transition_driver_waits_only_for_foreground_admission()
    if '--schema-upgrade-only' in sys.argv:
        test_issue80_schema38_transition_upgrade_preserves_original_recovery_identity()
        return
    if '--activation-scaling' in sys.argv:
        with isolate_unrelated_publication_tasks():
            test_pr102_activation_sql_statement_count_is_independent_of_transition_size()
            test_pr102_bulk_cleanup_preserves_authored_shared_history_and_owner_semantics()
            test_pr102_bulk_activation_rolls_back_and_releases_reservation_barrier()
        return
    if '--seed-retained' in sys.argv:
        with isolate_unrelated_publication_tasks():
            seed_retained_applications()
        return
    if '--recover-retained' in sys.argv or '--verify-retained' in sys.argv:
        with isolate_unrelated_publication_tasks():
            recover_retained_applications(cleanup='--cleanup-retained' in sys.argv,verify_only='--verify-retained' in sys.argv)
        return
    if '--lock-scaling' in sys.argv:
        with isolate_unrelated_publication_tasks():
            test_issue80_unfenced_bulk_graph_writes_use_a_constant_reservation_lock_budget()
            test_issue80_raw_snapshot_order_matches_recording_for_multidigit_and_unicode_rows()
            test_issue80_bulk_evidence_keeps_native_values_and_bounded_digest_transfer()
            test_issue80_real_application_acceptance_and_activation_have_constant_lock_scaling()
            test_issue80_queue_and_row_contention_yield_without_losing_operation_identity()
            test_issue80_distinct_applications_share_a_bounded_barrier_and_recover_after_contention()
            test_issue80_permanent_deletion_exclusions_block_plans_and_fenced_absent_targets()
            test_issue80_deletion_exclusion_races_use_the_bounded_reservation_barrier()
            test_issue80_schema38_transition_upgrade_preserves_original_recovery_identity()
        return
    with isolate_unrelated_publication_tasks():
        test_pr102_activation_sql_statement_count_is_independent_of_transition_size()
        test_pr102_bulk_cleanup_preserves_authored_shared_history_and_owner_semantics()
        test_pr102_bulk_activation_rolls_back_and_releases_reservation_barrier()
        test_issue80_application_rehearsal_preserves_unrelated_publication_tasks()
        test_issue80_raw_snapshot_order_matches_recording_for_multidigit_and_unicode_rows()
        test_issue80_bulk_evidence_keeps_native_values_and_bounded_digest_transfer()
        test_issue80_unfenced_bulk_graph_writes_use_a_constant_reservation_lock_budget()
        test_issue80_real_application_acceptance_and_activation_have_constant_lock_scaling()
        test_issue80_queue_and_row_contention_yield_without_losing_operation_identity()
        test_issue80_distinct_applications_share_a_bounded_barrier_and_recover_after_contention()
        test_issue80_permanent_deletion_exclusions_block_plans_and_fenced_absent_targets()
        test_issue80_deletion_exclusion_races_use_the_bounded_reservation_barrier()
        test_issue80_schema38_transition_upgrade_preserves_original_recovery_identity()
        test_issue80_selected_caro_shortening_publishes_exact_graph_and_qgd_steady_state()
        test_issue80_structural_fences_target_creation_source_edits_and_duplicate_plan_delivery()
        test_issue80_authoritative_revalidation_rejects_source_and_absent_target_races_without_partial_mutation()
        test_issue80_review_during_staging_rejects_activation_and_releases_fences()
        test_issue80_concurrent_target_source_and_membership_writes_wait_then_conflict()
        test_issue80_same_operation_concurrency_stale_slice_lost_response_and_activation_rollback()
        test_issue80_shared_history_seed_implicit_owner_and_authored_checkpoint_reuse_are_preserved()
        test_issue80_retired_queued_active_partial_pending_and_offline_presentations_never_grade_replacements()
        test_issue80_publication_failure_keeps_activation_and_retry_resumes_linked_tasks()
        test_issue80_midnight_recovery_keeps_approved_due_dates_and_publishes_current_queue()
        test_issue80_staging_failure_releases_fences_without_product_activation_and_noops_replay()
        test_issue80_unclean_source_integrity_rejects_before_acceptance()
        test_issue80_reader_only_deployed_api_dispatches_pending_apply_and_replays_final_result()
        test_issue80_deployed_apply_recovers_original_identity_after_foreground_preemption()


if __name__ == '__main__':
    main()
