"""AS-03/08/09/10/11/15/16/19 against runner-owned PostgreSQL transactions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import copy
import json
import os
import sys
import subprocess
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fastapi import HTTPException
from fastapi.testclient import TestClient
from celery.exceptions import TimeoutError as CeleryTimeout
import psycopg
from app import postgres_store
from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
from app.services.postgres_opening_evidence import prepare_checkpoint, persist_checkpoint, decision_evidence, canonical_json, queue_manifests
from app.review_commands import submit_review


def shadow_digest(database, repertoire_id):
    tables = {
      'presentations': ('SELECT * FROM opening_evidence_presentations WHERE card_id=%s ORDER BY id', repertoire_id),
      'contexts': ('SELECT context.* FROM opening_evidence_queue_contexts context JOIN opening_evidence_presentations snapshot ON snapshot.id=context.presentation_snapshot_id WHERE snapshot.card_id=%s ORDER BY queue_entry_id,presentation_snapshot_id,repertoire_id', repertoire_id),
      'attempts': ('SELECT * FROM opening_evidence_attempts WHERE repertoire_id=%s ORDER BY attempt_id', repertoire_id),
      'events': ('SELECT event.* FROM opening_evidence_events event JOIN opening_evidence_attempts attempt USING(attempt_id) WHERE repertoire_id=%s ORDER BY event.attempt_id,sequence', repertoire_id),
      'observations': ('SELECT observation.* FROM opening_evidence_observations observation JOIN opening_evidence_attempts attempt USING(attempt_id) WHERE repertoire_id=%s ORDER BY observation.attempt_id,decision_index', repertoire_id),
      'summaries': ('SELECT * FROM opening_evidence_summaries WHERE decision_id IN (SELECT decision_id FROM opening_evidence_observations observation JOIN opening_evidence_attempts attempt USING(attempt_id) WHERE repertoire_id=%s) ORDER BY decision_id', repertoire_id),
      'days': ('SELECT * FROM opening_evidence_clean_days WHERE decision_id IN (SELECT decision_id FROM opening_evidence_observations observation JOIN opening_evidence_attempts attempt USING(attempt_id) WHERE repertoire_id=%s) ORDER BY decision_id,study_day', repertoire_id),
    }
    contents = {name:[dict(row) for row in database.execute_native(statement,(parameter,)).fetchall()] for name,(statement,parameter) in tables.items()}
    return hashlib.sha256(json.dumps(contents,sort_keys=True,default=str).encode()).hexdigest()


def verify_persisted_shadow():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Shadow verification requires a disposable PostgreSQL instance')
    os.environ['TEMPO_DATABASE_WRITE_URL']=os.getenv('TEMPO_SHADOW_REHEARSAL_URL','postgresql://postgres@postgres:5432/tempo')
    os.environ['TEMPO_DATABASE_READ_URL']=os.environ['TEMPO_DATABASE_WRITE_URL']
    with postgres_store.connection(read_only=True) as database:
        receipts=database.execute_native("SELECT operation_id,response_json FROM operation_receipts WHERE command_name='shadow.evidence.fixture' ORDER BY operation_id").fetchall()
        assert receipts,'Shadow data was absent after service recreation'
        for receipt in receipts:
            assert shadow_digest(database,receipt[0]) == json.loads(receipt[1])['digest'],'Service recreation changed shadow provenance or summaries'
    print('PASS exact PostgreSQL shadow event/provenance/summary digest survives service recreation')
    postgres_store.close_pools()


def test_offline_repeat_reconciles_parent_aggregate_only_fallback(database, card_id, queue_entry_id, checkpoint, event, manifest, observed_at):
    database.execute_native('SAVEPOINT parent_fallback')
    parent_attempt_id=card_id+'-aggregate-only-parent'
    parent=submit_review(database,{'card_id':card_id,'review':{'outcome':'correct','queue_entry_id':queue_entry_id,
      'attempt_id':parent_attempt_id,'recorded_at':observed_at}})
    assert parent['persisted'] and parent['requeue_entry_id']
    repeat=checkpoint('repeat-after-fallback')
    repeat['parent_attempt_id']=parent_attempt_id
    repeat['queue_entry_id']=parent['requeue_entry_id']
    repeat['events']=[event(0,1)]
    repeat['terminal']={'state':'complete','final_sequence':1,'ended_at':observed_at}
    wrong_parent=copy.deepcopy(repeat);wrong_parent['parent_attempt_id']=card_id+'-unknown-parent'
    try:
        persist_checkpoint(database,{'checkpoint':wrong_parent,'prepared_manifest':manifest},completing_review=True)
    except HTTPException as error:assert 'parent aggregate review' in error.detail['message']
    else:raise AssertionError('An unreconciled parent was accepted')
    # A parent can resolve only its original queue or confirmed repeat, never a
    # separate queue cycle even when its immutable presentation happens to match.
    other_queue=database.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,card_bucket,admission_repertoire_id) "
      "VALUES(?,?,99,1001,'opening',?) RETURNING id",(date.today().isoformat(),card_id,manifest['repertoire_id'])).fetchone()[0]
    wrong_binding=copy.deepcopy(repeat);wrong_binding['queue_entry_id']=other_queue
    try:persist_checkpoint(database,{'checkpoint':wrong_binding,'prepared_manifest':manifest},completing_review=True)
    except HTTPException as error:assert 'parent' in error.detail['message']
    else:raise AssertionError('A different queue cycle was accepted for the parent')
    result=submit_review(database,{'card_id':card_id,'review':{'outcome':'correct','queue_entry_id':repeat['queue_entry_id'],
      'attempt_id':repeat['attempt_id'],'expected_review_id':parent['review_id'],'recorded_at':observed_at,
      'opening_evidence_completion':repeat},'prepared_manifest':manifest})
    assert result['persisted'],'A confirmed aggregate-only parent blocked its valid repeat evidence'
    assert database.execute_native('SELECT state FROM opening_evidence_attempts WHERE attempt_id=%s',(repeat['attempt_id'],)).fetchone()[0]=='complete'
    database.execute_native('ROLLBACK TO SAVEPOINT parent_fallback')
    print('PASS test_offline_repeat_reconciles_parent_aggregate_only_fallback')


def _create_color_fixture(line_color, *, card_color=None, shared=False, tied_lines=False):
    prefix = 'legacy-color-' + uuid.uuid4().hex
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    learner_color = card_color or line_color
    moves = ['e2e4', 'e7e5'] if learner_color == 'black' else ['e2e4']
    alternate = prefix+'-alternate' if shared else None
    with postgres_store.connection() as database:
        database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)',
                         (prefix, 'Legacy color', 'test', now.isoformat()))
        if tied_lines:
            # Insert the later ID first: binding must use the deterministic tie-break.
            database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)',
                             (prefix+'-z', prefix, 'Tied opposite line', 'black' if line_color=='white' else 'white', fen, json.dumps(moves), now.isoformat()))
        if line_color is not None:
            database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)',
                             (prefix+'-a', prefix, 'Legacy line', line_color, fen, json.dumps(moves), now.isoformat()))
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,due_date) VALUES(?,?,'prefix',?,?,?,?)",
                         (prefix, prefix, fen, json.dumps(moves), card_color, date.today().isoformat()))
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)', (prefix, prefix))
        if alternate:
            opposite = 'black' if line_color=='white' else 'white'
            database.execute('INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES(?,?,?,?,1)',
                             (alternate, 'Opposite display scope', 'test', now.isoformat()))
            database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)',
                             (alternate+'-line', alternate, 'Other line', opposite, fen, json.dumps(moves), now.isoformat()))
            database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)', (alternate, prefix))
        queue = database.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_repertoire_id) VALUES(?,?,1999,'opening',?) RETURNING id",
                                 (date.today().isoformat(), prefix, prefix)).fetchone()[0]
    return {'card_id':prefix, 'queue_id':queue, 'repertoire_id':prefix, 'alternate':alternate, 'now':now}


def _transport_color_fixture(fixture, *, evidence=True, queue_id=None):
    from app import main
    return next(card for card in main._queue_payload(include_opening_evidence=evidence)['cards']
                if card['queue_entry_id'] == (queue_id or fixture['queue_id']))


def _color_checkpoint(fixture, manifest):
    decision = manifest['decisions'][0]
    return OpeningEvidenceCheckpoint(attempt_id=fixture['card_id']+'-attempt', manifest=manifest,
        origin_queue_entry_id=fixture['queue_id'], queue_entry_id=fixture['queue_id'],
        started_at=fixture['now'].isoformat(), study_timezone='UTC',
        events=[{'sequence':1,'decision_index':0,'decision_id':decision['decision_id'],
                 'expected_uci':decision['expected_uci'],'kind':'first_response',
                 'observed_at':fixture['now'].isoformat(),'response_uci':decision['expected_uci'],
                 'disposition':'expected'}],
        terminal={'state':'partial','final_sequence':1,'ended_at':fixture['now'].isoformat()})


def _fixture_scheduling(database, fixture):
    return {table:[dict(row) for row in database.execute(query,(fixture['card_id'],)).fetchall()]
            for table,query in {'cards':'SELECT * FROM cards WHERE id=?',
              'queue':'SELECT * FROM daily_queue WHERE card_id=? ORDER BY id',
              'reviews':'SELECT * FROM reviews WHERE card_id=? ORDER BY id',
              'splits':'SELECT * FROM prefix_splits WHERE source_card_id=?'}.items()}


def _retain_color_provenance(fixture):
    with postgres_store.connection() as database:
        database.execute('DELETE FROM cards WHERE id=?', (fixture['card_id'],))
        database.execute('DELETE FROM repertoires WHERE id=?', (fixture['repertoire_id'],))
        if fixture['alternate']:
            database.execute('DELETE FROM repertoires WHERE id=?', (fixture['alternate'],))
        digest = shadow_digest(database, fixture['card_id'])
        database.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES(%s,'shadow.evidence.fixture',%s,'complete',%s)",
                                (fixture['card_id'], digest, json.dumps({'digest':digest})))


def _large_checkpoint_fixture():
    fixture = _create_color_fixture('white')
    # Twenty distinct learner decisions exercise the maximum projection/day writes.
    moves = ('e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7 '
             'f1e1 b7b5 a4b3 d7d6 c2c3 e8g8 h2h3 c6b8 d2d4 b8d7 '
             'b1d2 c7c5 d4d5 c5c4 b3c2 d7c5 d2f1 f6d7 c1e3 c5b7 '
             'f1g3 d7c5 b2b4 c5d7 a2a4 d7b6 a4b5 b6d7 g3f5').split()
    with postgres_store.connection() as database:
        database.execute('UPDATE cards SET moves_json=?,revision=2 WHERE id=?', (json.dumps(moves), fixture['card_id']))
        # Admit the edited fixture as a new attempt; the original origin must retain revision 1.
        fixture['queue_id'] = database.execute(
            "INSERT INTO daily_queue(queue_date,card_id,cycle,position,card_bucket,admission_repertoire_id) "
            "VALUES(?,?,1,2000,'opening',?) RETURNING id",
            (date.today().isoformat(), fixture['card_id'], fixture['repertoire_id'])).fetchone()[0]
    manifest = _transport_color_fixture(fixture)['opening_decision_manifest']
    assert len(manifest['decisions']) == 20
    assert len({decision['decision_id'] for decision in manifest['decisions']}) == 20
    events = []
    for index, decision in enumerate(manifest['decisions']):
        common = {'decision_index':index, 'decision_id':decision['decision_id'],
                  'expected_uci':decision['expected_uci'], 'observed_at':fixture['now'].isoformat()}
        events.append({**common, 'sequence':len(events)+1, 'kind':'first_response',
                       'response_uci':decision['expected_uci'], 'disposition':'expected'})
        # Repeated exposure after the response is valid, deduplicated assistance;
        # the twenty initial responses remain clean and exercise clean-day writes.
        for _ in range(12 if index < 16 else 11):
            events.append({**common, 'sequence':len(events)+1, 'kind':'assistance', 'assistance':'hint'})
    assert len(events) == 256
    checkpoint = _color_checkpoint(fixture, manifest).model_dump(mode='json')
    checkpoint.update(events=events, terminal=None)
    return fixture, {'checkpoint':checkpoint, 'prepared_manifest':manifest}


def _checkpoint_operation_receipt(operation_id):
    with postgres_store.connection(read_only=True) as database:
        return dict(database.execute_native('SELECT * FROM operation_receipts WHERE operation_id=%s', (operation_id,)).fetchone())


def admitted_checkpoint_delivery(operation_id, command_name, payload, *, prior_attempt_count=0):
    """Wait in the proof driver only for explicit admission deferrals.

    A refused command retains its source receipt and spends no attempt. SQL
    retries and stale-publication failures must remain visible to the caller.
    """
    from app import tasks
    admission_deadline = time.monotonic() + 10
    while True:
        result = tasks.execute_background_command.run(operation_id, command_name, payload)
        if result is not None:
            return result
        receipt = _checkpoint_operation_receipt(operation_id)
        if receipt['state'] != 'retrying' or receipt['last_error_json'] is not None or receipt['attempt_count'] != prior_attempt_count:
            return None
        assert time.monotonic() < admission_deadline, 'Checkpoint never obtained foreground-idle admission within 10 seconds'
        time.sleep(0.01)


def admitted_checkpoint_replay(operation_id, payload):
    """Exercise the completed command path after observable idle admission."""
    from app import command_gateway
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    completed_receipt = _checkpoint_operation_receipt(operation_id)
    assert completed_receipt['state'] == 'complete', 'Checkpoint replay requires a completed receipt'
    admission_deadline = time.monotonic() + 10
    while True:
        try:
            return command_gateway.execute_command(operation_id, 'opening_evidence.checkpoint', payload, background=True)
        except BackgroundAdmissionDeferred:
            assert _checkpoint_operation_receipt(operation_id) == completed_receipt, 'Denied checkpoint replay changed the completed receipt'
            assert time.monotonic() < admission_deadline, 'Checkpoint replay never obtained foreground-idle admission within 10 seconds'
            time.sleep(0.01)


def _recover_checkpoint_operation(operation_id):
    from app import command_gateway
    from app.services.activity_gate import activity_gate
    prior_attempt_count = _checkpoint_operation_receipt(operation_id)['attempt_count']
    with postgres_store.connection() as database:
        database.execute_native("UPDATE operation_receipts SET next_retry_at=NOW()-INTERVAL '1 minute',"
                                "lease_expires_at=NOW()-INTERVAL '1 minute',updated_at='1970-01-01' WHERE operation_id=%s", (operation_id,))
    # Match the worker's short receipt-bookkeeping boundary. The actual
    # preparation/publication below still requires normal idle admission.
    with activity_gate.background_control():
        recovered = command_gateway.claim_recoverable_operation()
    assert recovered and recovered['operation_id'] == operation_id and recovered['background'] is True
    return admitted_checkpoint_delivery(operation_id, recovered['command_name'], recovered['payload'],
                                        prior_attempt_count=prior_attempt_count)


def test_postgres_checkpoint_driver_retries_only_foreground_deferral():
    from app.services import redis_admission_gate
    fixture = _create_color_fixture('white')
    manifest = _transport_color_fixture(fixture)['opening_decision_manifest']
    payload = {'checkpoint': _color_checkpoint(fixture, manifest).model_dump(mode='json')}
    operation_id = fixture['card_id'] + '-admission-driver'
    with ThreadPoolExecutor(max_workers=1) as workers:
        with redis_admission_gate.foreground_lease():
            future = workers.submit(admitted_checkpoint_delivery, operation_id, 'opening_evidence.checkpoint', payload)
            deadline = time.monotonic() + 5
            while True:
                with postgres_store.connection(read_only=True) as database:
                    receipt = database.execute_native('SELECT state,attempt_count,payload_json FROM operation_receipts WHERE operation_id=%s', (operation_id,)).fetchone()
                if receipt and receipt['state'] == 'retrying':
                    assert receipt['attempt_count'] == 0 and json.loads(receipt['payload_json']) == payload
                    assert not future.done()
                    break
                assert time.monotonic() < deadline, 'Checkpoint did not retain refused admission'
                time.sleep(0.01)
        assert future.result(timeout=10)['persisted']
    assert _checkpoint_operation_receipt(operation_id)['attempt_count'] == 1
    _retain_color_provenance(fixture)
    print('PASS test_postgres_checkpoint_driver_retries_only_foreground_deferral (retained source, zero denied attempts, real Redis release)')


def _assert_large_checkpoint(database, payload, *, state='active'):
    request = payload['checkpoint']
    attempt = database.execute_native('SELECT * FROM opening_evidence_attempts WHERE attempt_id=%s', (request['attempt_id'],)).fetchone()
    assert attempt['state'] == state and attempt['contiguous_sequence'] == 256
    events = database.execute_native('SELECT event_json FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence', (request['attempt_id'],)).fetchall()
    assert [json.loads(row[0]) for row in events] == OpeningEvidenceCheckpoint.model_validate(request).model_dump(mode='json')['events']
    observations = database.execute_native('SELECT observation_json FROM opening_evidence_observations WHERE attempt_id=%s ORDER BY decision_index', (request['attempt_id'],)).fetchall()
    from app.services.opening_decision_evidence import reduce_observations
    expected = reduce_observations(request['events'], request['study_timezone'])
    assert [json.loads(row[0]) for row in observations] == expected
    assert len(observations) == 20 and all(item['clean'] for item in expected)
    summaries = database.execute_native('SELECT * FROM opening_evidence_summaries WHERE decision_id=ANY(%s::text[])',
                                       ([decision['decision_id'] for decision in request['manifest']['decisions']],)).fetchall()
    assert sum(row['first_responses'] for row in summaries) == sum(row['clean_successes'] for row in summaries) == 20
    assert all(row['distinct_clean_days'] == 1 for row in summaries)


def _paused_checkpoint_review(*, complete_same_attempt):
    from check_postgres_graph_retention import owned_admission_scope
    with owned_admission_scope('checkpoint-review-'+uuid.uuid4().hex):
        _assert_paused_checkpoint_review(complete_same_attempt=complete_same_attempt)


def _assert_paused_checkpoint_review(*, complete_same_attempt):
    from app import command_gateway, database as database_module, tasks
    from app.services import postgres_opening_evidence as evidence
    from app.services.activity_gate import activity_gate
    fixture, payload = _large_checkpoint_fixture()
    operation_id = fixture['card_id']+'-checkpoint'
    # Persist one initial event so the test probes a real attempt row, not an absent key.
    initial = copy.deepcopy(payload)
    initial['checkpoint']['events'] = initial['checkpoint']['events'][:1]
    assert admitted_checkpoint_delivery(operation_id+'-initial', 'opening_evidence.checkpoint', initial)['persisted']
    entered, release = Event(), Event()
    source_pids, publication_ms, transaction_ms = [], [], []
    original_read = database_module.background_read_connection
    original_reduce = evidence.reduce_observations
    original_commit = command_gateway._handlers['opening_evidence.checkpoint']
    original_writer = command_gateway._writer_connection
    reducer_thread = None

    @contextmanager
    def observe_read(**options):
        with original_read(**options) as connection:
            source_pids.append(connection.raw.info.backend_pid)
            yield connection

    def pause_reduction(events, study_timezone):
        nonlocal reducer_thread
        if len(events) == 256 and reducer_thread is None:
            reducer_thread = get_ident()
            assert activity_gate.active_background_sections == 0
            entered.set()
            assert release.wait(15), 'Foreground review failed to complete while reduction was paused'
        return original_reduce(events, study_timezone)

    def measured_publication(connection, prepared):
        budget = connection.raw.execute('SHOW transaction_timeout').fetchone()[0]
        assert budget == '250ms', 'The regression must retain the default background transaction budget'
        started = time.perf_counter()
        try:
            return original_commit(connection, prepared)
        finally:
            publication_ms.append((time.perf_counter()-started)*1000)

    @contextmanager
    def measured_writer(background):
        with original_writer(background) as connection:
            started = time.perf_counter()
            try:
                yield connection
            finally:
                # Include receipt SQL and the connection's commit, below.
                transaction_started = started
        if background:
            transaction_ms.append((time.perf_counter()-transaction_started)*1000)

    with patch.object(database_module, 'background_read_connection', observe_read), \
            patch.object(evidence, 'reduce_observations', pause_reduction), \
            patch.object(command_gateway, '_writer_connection', measured_writer), \
            patch.dict(command_gateway._handlers, {'opening_evidence.checkpoint':measured_publication}), \
            ThreadPoolExecutor(max_workers=2) as workers:
        checkpoint_future = workers.submit(admitted_checkpoint_delivery, operation_id, 'opening_evidence.checkpoint', payload)
        try:
            assert entered.wait(10), 'Standalone checkpoint did not reach outside-transaction reduction'
            assert source_pids and activity_gate.active_background_sections == 0
            with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as probe:
                # Inspect the exact preparation connection before acquiring this attempt's
                # row lock ourselves. Its read transaction must already be over.
                sessions = probe.execute('SELECT pid,state,xact_start FROM pg_stat_activity WHERE pid=ANY(%s::int[])', (source_pids,)).fetchall()
                assert sessions and all(row[1] == 'idle' and row[2] is None for row in sessions)
                probe.execute('SELECT attempt_id FROM opening_evidence_attempts WHERE attempt_id=%s FOR UPDATE NOWAIT', (payload['checkpoint']['attempt_id'],))
            review = {'outcome':'correct', 'queue_entry_id':fixture['queue_id'], 'recorded_at':fixture['now'].isoformat(),
                      'attempt_id':payload['checkpoint']['attempt_id'] if complete_same_attempt else fixture['card_id']+'-aggregate'}
            review_payload = {'card_id':fixture['card_id'], 'review':review}
            if complete_same_attempt:
                completion = copy.deepcopy(payload['checkpoint'])
                completion['terminal'] = {'state':'complete', 'final_sequence':256, 'ended_at':fixture['now'].isoformat()}
                review.update(opening_evidence_completion=completion)
                review_payload['prepared_manifest'] = prepare_checkpoint(OpeningEvidenceCheckpoint.model_validate(completion))
            started = time.perf_counter()
            review_future = workers.submit(tasks.execute_foreground_command.run, fixture['card_id']+'-review', 'cards.review', review_payload)
            review_result = review_future.result(timeout=10)
            foreground_ms = (time.perf_counter()-started)*1000
            assert review_result is not None, _checkpoint_operation_receipt(fixture['card_id']+'-review')
            assert review_result['persisted'] and not release.is_set() and not checkpoint_future.done()
            with postgres_store.connection(read_only=True) as database:
                scheduling = _fixture_scheduling(database, fixture)
                if complete_same_attempt:
                    _assert_large_checkpoint(database, payload, state='complete')
                    before_stale = shadow_digest(database, fixture['repertoire_id'])
        finally:
            release.set()
        checkpoint_result = checkpoint_future.result(timeout=10)
    if complete_same_attempt:
        assert checkpoint_result is None
        receipt = _checkpoint_operation_receipt(operation_id)
        assert receipt['state'] == 'retrying' and json.loads(receipt['last_error_json'])['class'] == 'SerializationFailure'
        with postgres_store.connection(read_only=True) as database:
            assert shadow_digest(database, fixture['repertoire_id']) == before_stale, 'Stale preparation changed completed evidence'
        checkpoint_result = _recover_checkpoint_operation(operation_id)
    assert checkpoint_result['persisted'] and checkpoint_result['contiguous_sequence'] == 256
    assert checkpoint_result['state'] == ('complete' if complete_same_attempt else 'active')
    with postgres_store.connection(read_only=True) as database:
        _assert_large_checkpoint(database, payload, state=checkpoint_result['state'])
        assert _fixture_scheduling(database, fixture) == scheduling, 'Checkpoint publication changed scheduling'
        digest = shadow_digest(database, fixture['repertoire_id'])
    # Same receipt and a distinct delivery key both preserve events, counters and days.
    assert admitted_checkpoint_replay(operation_id, payload) == checkpoint_result
    assert admitted_checkpoint_delivery(operation_id+'-replay', 'opening_evidence.checkpoint', payload) == checkpoint_result
    with postgres_store.connection(read_only=True) as database:
        assert shadow_digest(database, fixture['repertoire_id']) == digest and _fixture_scheduling(database, fixture) == scheduling
    receipt = _checkpoint_operation_receipt(operation_id)
    assert json.loads(receipt['payload_json']) == payload
    assert receipt['request_hash'] == command_gateway.request_digest('opening_evidence.checkpoint', payload)
    assert publication_ms and max(publication_ms) < 250
    assert transaction_ms and max(transaction_ms) < 250
    _retain_color_provenance(fixture)
    print(json.dumps({'test':'test_postgres_opening_checkpoint_stale_preparation_preserves_foreground_completion' if complete_same_attempt else
                      'test_postgres_opening_checkpoint_reduction_yields_to_foreground_review', 'decisions':20, 'events':256,
                      'foreground_completed_before_release':True, 'foreground_ms':round(foreground_ms,3),
                      'publication_ms':[round(value,3) for value in publication_ms],
                      'background_transaction_ms':[round(value,3) for value in transaction_ms],
                      'budget_ms':250, 'source_connections_idle':True}))


def test_postgres_opening_checkpoint_reduction_yields_to_foreground_review():
    _paused_checkpoint_review(complete_same_attempt=False)


def test_postgres_opening_checkpoint_stale_preparation_preserves_foreground_completion():
    _paused_checkpoint_review(complete_same_attempt=True)


def _crash_worker_after_checkpoint_preparation():
    from app import command_gateway, tasks
    supplied = json.load(sys.stdin)
    original_prepare = command_gateway._preparers['opening_evidence.checkpoint']
    def crash_after_prepare(payload):
        prepared = original_prepare(payload)
        assert len(prepared.observations) == 20 and prepared.result['contiguous_sequence'] == 256
        print('Prepared 256 events / 20 decisions; exiting before publication', flush=True)
        os._exit(73)
    command_gateway._preparers['opening_evidence.checkpoint'] = crash_after_prepare
    admitted_checkpoint_delivery(supplied['operation_id'], 'opening_evidence.checkpoint', supplied['payload'])
    raise AssertionError('Crash worker unexpectedly reached publication')


def test_postgres_opening_checkpoint_restart_recomputes_original_receipt():
    from app import command_gateway
    fixture, payload = _large_checkpoint_fixture()
    operation_id = fixture['card_id']+'-restart'
    with postgres_store.connection(read_only=True) as database:
        scheduling = _fixture_scheduling(database, fixture)
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--crash-after-prepare'],
                           input=json.dumps({'operation_id':operation_id, 'payload':payload}), text=True,
                           capture_output=True, timeout=20)
    assert child.returncode == 73 and 'Prepared 256 events' in child.stdout, child.stdout+child.stderr
    receipt = _checkpoint_operation_receipt(operation_id)
    assert receipt['state'] == 'executing' and json.loads(receipt['payload_json']) == payload
    assert receipt['request_hash'] == command_gateway.request_digest('opening_evidence.checkpoint', payload)
    with postgres_store.connection(read_only=True) as database:
        assert not database.execute_native('SELECT 1 FROM opening_evidence_attempts WHERE attempt_id=%s', (payload['checkpoint']['attempt_id'],)).fetchone()
    result = _recover_checkpoint_operation(operation_id)
    assert result['persisted'] and result['contiguous_sequence'] == 256
    with postgres_store.connection(read_only=True) as database:
        _assert_large_checkpoint(database, payload)
        assert _fixture_scheduling(database, fixture) == scheduling
        digest = shadow_digest(database, fixture['repertoire_id'])
    assert admitted_checkpoint_replay(operation_id, payload) == result
    with postgres_store.connection(read_only=True) as database:
        assert shadow_digest(database, fixture['repertoire_id']) == digest
    assert _checkpoint_operation_receipt(operation_id)['attempt_count'] == 2
    _retain_color_provenance(fixture)
    print('PASS test_postgres_opening_checkpoint_restart_recomputes_original_receipt (process exit 73, lease recovery, exact replay)')


@contextmanager
def _assert_owned_admission_cleanup():
    """Check proof-owned leases without racing the deployed API health probe."""
    from app.services import redis_admission_gate
    server = redis_admission_gate.client()
    original_eval = server.eval
    owned_leases = set()

    def record_owned_lease(script, *arguments):
        result = original_eval(script, *arguments)
        if script == redis_admission_gate._REGISTER_FOREGROUND:
            owned_leases.add((arguments[1], arguments[3]))
        elif script == redis_admission_gate._CLAIM_BACKGROUND and result:
            owned_leases.add((arguments[2], arguments[4]))
        return result

    with patch.object(server, 'eval', record_owned_lease):
        yield
    # Every lease created by these local HTTP/worker threads must be removed.
    # Other processes retain their own legitimate foreground/background leases.
    unreleased = [(key, token) for key, token in owned_leases
                  if server.zscore(key, token) is not None]
    assert not unreleased, f'Admission proof leaked {len(unreleased)} owned leases'


@_assert_owned_admission_cleanup()
def test_postgres_opening_checkpoint_http_admission_preserves_saved_payload_replay():
    from app import main, command_dispatch, command_gateway, database as database_module, tasks
    from app.services import redis_admission_gate
    fixture = _create_color_fixture('white')
    manifest = _transport_color_fixture(fixture)['opening_decision_manifest']
    checkpoint = _color_checkpoint(fixture, manifest).model_dump(mode='json')
    operation_id = fixture['card_id'] + '-http-checkpoint'
    historical_payload = {'checkpoint': checkpoint, 'prepared_manifest': {'obsolete': True}}
    claimed, saved, _, _ = command_gateway.record_operation_attempt(operation_id, 'opening_evidence.checkpoint', historical_payload, background=True)
    assert claimed and saved == historical_payload
    with postgres_store.connection() as connection:
        connection.execute_native("UPDATE operation_receipts SET state='queued',attempt_token=NULL,lease_expires_at=NULL WHERE operation_id=%s", (operation_id,))
        scheduling = _fixture_scheduling(connection, fixture)
    preparation_ready, allow_preparation, admission_denied, source_sql_started = Event(), Event(), Event(), Event()
    preparing_worker_thread_id = None
    source_connection_pids = []
    original_prepare = command_gateway._preparers['opening_evidence.checkpoint']
    original_read = database_module.background_read_connection
    original_sql = postgres_store.PostgresConnection.execute_native
    server = redis_admission_gate.client()
    original_eval = server.eval
    def observe_eval(script, *arguments):
        result = original_eval(script, *arguments)
        if script == redis_admission_gate._CLAIM_BACKGROUND and result == 0 and get_ident() == preparing_worker_thread_id:
            admission_denied.set()
        return result
    def prepare(saved_payload):
        nonlocal preparing_worker_thread_id
        preparing_worker_thread_id = get_ident()
        assert saved_payload == historical_payload, 'The worker replaced its historical durable payload'
        preparation_ready.set()
        assert allow_preparation.wait(10), 'HTTP checkpoint admission proof did not release preparation'
        return original_prepare(saved_payload)
    @contextmanager
    def observe_read(**options):
        if get_ident() == preparing_worker_thread_id:
            assert options == {'authoritative': True}
        with original_read(**options) as connection:
            if get_ident() == preparing_worker_thread_id:
                assert connection.raw.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
                assert connection.raw.execute('SHOW transaction_timeout').fetchone()[0] == '250ms'
                assert connection.raw.execute('SHOW lock_timeout').fetchone()[0] == '25ms'
                source_connection_pids.append(connection.raw.info.backend_pid)
            yield connection
    def observe_sql(connection, statement, parameters=()):
        if statement.startswith('SELECT snapshot.*'):
            assert get_ident() == preparing_worker_thread_id, 'Checkpoint HTTP performed API-side evidence preparation'
            source_sql_started.set()
        return original_sql(connection, statement, parameters)
    with ThreadPoolExecutor(max_workers=2) as requests, \
            patch.object(server, 'eval', observe_eval), \
            patch.object(database_module, 'background_read_connection', observe_read), \
            patch.object(postgres_store.PostgresConnection, 'execute_native', observe_sql), \
            patch.dict(command_gateway._preparers, {'opening_evidence.checkpoint': prepare}):
        def send_task(task_name, *, args, queue, **options):
            assert task_name == 'app.tasks.execute_background_command' and queue == 'background'
            assert set(args[2]) == {'checkpoint'}, 'New HTTP dispatch persisted derived preparation'
            delivery = requests.submit(tasks.execute_background_command.run, *args)
            def get(**options):
                assert options['propagate'] is False
                try:
                    return delivery.result(timeout=options['timeout'])
                except TimeoutError as error:
                    raise CeleryTimeout() from error
                except Exception as error:
                    return error
            return SimpleNamespace(get=get)
        with patch.object(command_dispatch.celery_app, 'send_task', send_task):
            client = TestClient(main.app)
            posted = requests.submit(client.post, '/api/opening-evidence/checkpoints', json=checkpoint,
                                     headers={'Idempotency-Key': operation_id})
            try:
                assert preparation_ready.wait(10), 'The HTTP checkpoint did not reach the genuine worker preparer'
                with redis_admission_gate.foreground_lease():
                    allow_preparation.set()
                    assert admission_denied.wait(5), 'Worker source preparation never waited on shared foreground admission'
                    assert not source_sql_started.is_set() and not source_connection_pids, 'Worker source read opened before admission'
                    response = posted.result(timeout=5)
                    assert response.status_code == 202 and response.json()['state'] == 'retrying', response.text
                    receipt = _checkpoint_operation_receipt(operation_id)
                    assert receipt['state'] == 'retrying' and receipt['attempt_count'] == 1
                    assert json.loads(receipt['payload_json']) == historical_payload
            finally:
                allow_preparation.set()
            # Make this owned receipt's saved retry deadline due, then use the
            # genuine recovery claim. Never wait inside the analysis worker.
            recovered_result = _recover_checkpoint_operation(operation_id)
            assert recovered_result['persisted']
            response = client.post('/api/opening-evidence/checkpoints', json=checkpoint,
                                   headers={'Idempotency-Key': operation_id})
            assert response.status_code == 200 and response.json()['persisted']
            result = response.json()
            assert client.post('/api/opening-evidence/checkpoints', json=checkpoint,
                               headers={'Idempotency-Key': operation_id}).json() == result
            changed = {**checkpoint, 'study_timezone': 'America/New_York'}
            assert client.post('/api/opening-evidence/checkpoints', json=changed,
                               headers={'Idempotency-Key': operation_id}).status_code == 409
    assert source_sql_started.is_set() and source_connection_pids
    receipt = _checkpoint_operation_receipt(operation_id)
    assert receipt['state'] == 'complete' and json.loads(receipt['payload_json']) == historical_payload
    assert receipt['request_hash'] == command_gateway.request_digest('opening_evidence.checkpoint', {'checkpoint': checkpoint})
    with postgres_store.connection(read_only=True) as connection:
        assert _fixture_scheduling(connection, fixture) == scheduling
        digest = shadow_digest(connection, fixture['repertoire_id'])
    with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as probe:
        sessions = probe.execute('SELECT state,xact_start FROM pg_stat_activity WHERE pid=ANY(%s::int[])', (source_connection_pids,)).fetchall()
        assert sessions and all(row[0] == 'idle' and row[1] is None for row in sessions)
    for state in ('queued', 'retrying'):
        recovery_id = operation_id + '-' + state
        assert command_gateway.record_operation_attempt(recovery_id, 'opening_evidence.checkpoint', historical_payload, background=True)[0]
        with postgres_store.connection() as connection:
            connection.execute_native('UPDATE operation_receipts SET state=%s WHERE operation_id=%s', (state, recovery_id))
        assert _recover_checkpoint_operation(recovery_id) == result
        assert json.loads(_checkpoint_operation_receipt(recovery_id)['payload_json']) == historical_payload
        with postgres_store.connection(read_only=True) as connection:
            assert shadow_digest(connection, fixture['repertoire_id']) == digest
            assert _fixture_scheduling(connection, fixture) == scheduling
    _retain_color_provenance(fixture)
    print('PASS test_postgres_opening_checkpoint_http_admission_preserves_saved_payload_replay (real Redis denial, authoritative read-only 250ms/25ms, old/new identity, queued/retrying recovery)')


@_assert_owned_admission_cleanup()
def test_postgres_opening_attempt_http_admission_preserves_foreground_diagnostics():
    from check_postgres_graph_retention import owned_admission_scope
    with owned_admission_scope('attempt-http-' + uuid.uuid4().hex):
        _prove_opening_attempt_http_admission_preserves_foreground_diagnostics()


def _prove_opening_attempt_http_admission_preserves_foreground_diagnostics():
    from app import main, opening_evidence_api, tasks
    from app.services import redis_admission_gate
    from app.services.activity_gate import activity_gate
    fixture, payload = _large_checkpoint_fixture()
    checkpoint = OpeningEvidenceCheckpoint.model_validate(payload['checkpoint']).model_dump(mode='json')
    assert admitted_checkpoint_delivery(fixture['card_id'] + '-get-seed', 'opening_evidence.checkpoint', payload)['persisted']
    # The maximum valid journal must round-trip; PostgreSQL also rejects an
    # out-of-bounds sequence without changing the persisted evidence.
    with postgres_store.connection() as connection:
        connection.execute_native('SAVEPOINT event_sequence_boundary')
        try:
            connection.execute_native('INSERT INTO opening_evidence_events(attempt_id,sequence,event_json) VALUES(%s,257,%s)',
                                      (checkpoint['attempt_id'], canonical_json({'sequence': 257})))
        except psycopg.errors.CheckViolation as error:
            assert error.diag.constraint_name == 'opening_evidence_events_sequence_check'
        else:
            raise AssertionError('PostgreSQL accepted an event beyond the 256-event journal bound')
        finally:
            connection.execute_native('ROLLBACK TO SAVEPOINT event_sequence_boundary')
        connection.execute_native('RELEASE SAVEPOINT event_sequence_boundary')
        digest = shadow_digest(connection, fixture['repertoire_id'])
        scheduling = _fixture_scheduling(connection, fixture)
    original_read = opening_evidence_api.background_read_connection
    original_sql = postgres_store.PostgresConnection.execute_native
    server = redis_admission_gate.client()
    original_eval = server.eval
    client = TestClient(main.app)
    source_connection_pids = []
    for outcome in ('found', 'missing', 'error'):
        admission_denied, evidence_sql_started = Event(), Event()
        def observe_eval(script, *arguments):
            result = original_eval(script, *arguments)
            if script == redis_admission_gate._CLAIM_BACKGROUND and result == 0:
                admission_denied.set()
            return result
        @contextmanager
        def observe_read(**options):
            assert options == {'authoritative': True}
            with original_read(**options) as connection:
                assert connection.raw.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
                assert connection.raw.execute('SHOW transaction_timeout').fetchone()[0] == '250ms'
                assert connection.raw.execute('SHOW lock_timeout').fetchone()[0] == '25ms'
                source_connection_pids.append(connection.raw.info.backend_pid)
                yield connection
        def observe_sql(connection, statement, parameters=()):
            if statement.startswith('SELECT * FROM opening_evidence_attempts'):
                evidence_sql_started.set()
                if outcome == 'error':
                    raise RuntimeError('HTTP evidence read failed')
            return original_sql(connection, statement, parameters)
        with _assert_owned_admission_cleanup(), \
                ThreadPoolExecutor(max_workers=1) as requests, \
                patch.object(server, 'eval', observe_eval), \
                patch.object(opening_evidence_api, 'background_read_connection', observe_read), \
                patch.object(postgres_store.PostgresConnection, 'execute_native', observe_sql):
            with redis_admission_gate.foreground_lease():
                attempt_id = checkpoint['attempt_id'] if outcome != 'missing' else fixture['card_id'] + '-missing'
                attempt_request = requests.submit(client.get, '/api/opening-evidence/attempts/' + attempt_id,
                                          headers={'X-Tempo-Work-Class': 'background'})
                assert admission_denied.wait(5), 'Background HTTP attempt did not wait on real shared admission'
                deferred = attempt_request.result(timeout=5)
                assert deferred.status_code == 503 and deferred.headers['Retry-After'] == '1', deferred.text
                assert deferred.json() == {'detail': 'Waiting for foreground activity'}
                assert not evidence_sql_started.is_set(), 'HTTP attempt evidence SQL bypassed foreground admission'
            attempt_request = requests.submit(client.get, '/api/opening-evidence/attempts/' + attempt_id,
                                             headers={'X-Tempo-Work-Class': 'background'})
            if outcome == 'error':
                try:
                    attempt_request.result(timeout=10)
                except RuntimeError as error:
                    assert str(error) == 'HTTP evidence read failed'
                else:
                    raise AssertionError('The evidence read error was hidden')
            else:
                response = attempt_request.result(timeout=10)
                assert response.status_code == (200 if outcome == 'found' else 404)
                if outcome == 'found':
                    assert response.json()['attempt_id'] == checkpoint['attempt_id']
                    assert response.json()['state'] == 'active'
                    assert response.json()['events'] == checkpoint['events'] and len(response.json()['events']) == 256
                else:
                    assert response.json() == {'detail': 'Opening attempt evidence not found'}
        assert activity_gate.active_background_sections == 0
    with patch.object(opening_evidence_api, 'background_read_connection', side_effect=AssertionError('Foreground diagnostic self-admission')):
        diagnostic = client.get('/api/opening-evidence/attempts/' + checkpoint['attempt_id'])
        assert diagnostic.status_code == 200 and diagnostic.json()['events'] == checkpoint['events']
    with postgres_store.connection(read_only=True) as connection:
        assert shadow_digest(connection, fixture['repertoire_id']) == digest
        assert _fixture_scheduling(connection, fixture) == scheduling
    with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as probe:
        sessions = probe.execute('SELECT state,xact_start FROM pg_stat_activity WHERE pid=ANY(%s::int[])', (source_connection_pids,)).fetchall()
        assert sessions and all(row[0] == 'idle' and row[1] is None for row in sessions)
    _retain_color_provenance(fixture)
    print('PASS test_postgres_opening_attempt_http_admission_preserves_foreground_diagnostics (real Redis denial, authoritative read-only 250ms/25ms, ordered 256-event bound, 404/error cleanup, foreground control)')


def _assert_color_round_trip(fixture, trained_color):
    transport = _transport_color_fixture(fixture)
    assert transport['trained_color'] == trained_color
    assert 'opening_decision_manifest' in transport, 'Legacy scoped color omitted its authoritative manifest: '+str(transport.get('opening_evidence_diagnostic'))
    assert 'opening_evidence_diagnostic' not in transport
    manifest = transport['opening_decision_manifest']
    assert manifest['trained_color'] == trained_color
    assert manifest['repertoire_id'] == fixture['repertoire_id']
    checkpoint = _color_checkpoint(fixture, manifest)
    assert prepare_checkpoint(checkpoint) == manifest
    payload = {'checkpoint':checkpoint.model_dump(mode='json'), 'prepared_manifest':manifest}
    with postgres_store.connection() as database:
        before = _fixture_scheduling(database, fixture)
        result = persist_checkpoint(database, payload)
        assert result['persisted'] and result['contiguous_sequence'] == 1
        assert _fixture_scheduling(database, fixture) == before, 'Legacy checkpoint changed scheduling state'
    return manifest, checkpoint, payload


def test_postgres_legacy_opening_color_queue_checkpoint_round_trip():
    """AS-16/19: real queue transport must agree with immutable evidence."""
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Legacy color rehearsal requires disposable PostgreSQL')
    os.environ['TEMPO_DATABASE_WRITE_URL'] = os.getenv('TEMPO_SHADOW_REHEARSAL_URL', 'postgresql://postgres@postgres:5432/tempo')
    os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
    for trained_color in ('white', 'black'):
        fixture = _create_color_fixture(trained_color)
        ordinary = _transport_color_fixture(fixture, evidence=False)
        assert ordinary['trained_color'] == trained_color
        assert 'opening_decision_manifest' not in ordinary
        manifest, checkpoint, payload = _assert_color_round_trip(fixture, trained_color)
        assert _transport_color_fixture(fixture)['opening_decision_manifest'] == manifest
        with postgres_store.connection(read_only=True) as database:
            snapshot = database.execute_native('SELECT * FROM opening_evidence_presentations WHERE card_id=%s', (fixture['card_id'],)).fetchone()
            assert snapshot['trained_color'] is None
            assert database.execute_native('SELECT effective_trained_color FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s', (fixture['queue_id'],)).fetchone()[0] == trained_color
        opposite = 'black' if trained_color=='white' else 'white'
        with postgres_store.connection() as database:
            database.execute('DELETE FROM repertoire_lines WHERE repertoire_id=?', (fixture['repertoire_id'],))
            database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)',
                             (fixture['card_id']+'-replacement', fixture['repertoire_id'], 'Changed line', opposite, snapshot['start_fen'], snapshot['moves_json'], fixture['now'].isoformat()))
            database.execute_native('SELECT opening_evidence_bind_queue(%s,%s,%s)', (fixture['queue_id'], fixture['card_id'], fixture['repertoire_id']))
        assert _transport_color_fixture(fixture, evidence=False)['trained_color'] == opposite
        assert _transport_color_fixture(fixture)['opening_decision_manifest'] == manifest
        assert _transport_color_fixture(fixture)['trained_color'] == trained_color
        assert prepare_checkpoint(checkpoint) == manifest
        with postgres_store.connection() as database:
            assert persist_checkpoint(database, payload)['state'] == 'partial'
            assert database.execute_native('SELECT COUNT(*) FROM opening_evidence_events WHERE attempt_id=%s', (checkpoint.attempt_id,)).fetchone()[0] == 1
            assert database.execute_native('SELECT effective_trained_color FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s', (fixture['queue_id'],)).fetchone()[0] == trained_color
        with postgres_store.connection() as database:
            replacement_moves = ['e2e4'] if opposite=='white' else ['e2e4','e7e5']
            database.execute('UPDATE cards SET revision=2,moves_json=? WHERE id=?', (json.dumps(replacement_moves), fixture['card_id']))
        replacement_manifest = _transport_color_fixture(fixture)['opening_decision_manifest']
        assert replacement_manifest['trained_color'] == opposite
        assert replacement_manifest['manifest_id'] != manifest['manifest_id']
        assert replacement_manifest['presentation_snapshot_id'] != manifest['presentation_snapshot_id']
        assert prepare_checkpoint(checkpoint) == manifest
        _retain_color_provenance(fixture)
        # Historical preparation survives deletion of mutable source/card/queue data.
        assert prepare_checkpoint(checkpoint) == manifest
        print('PASS test_postgres_legacy_opening_color_queue_checkpoint_round_trip '+trained_color)
        print('PASS test_postgres_legacy_color_replay_survives_line_replacement '+trained_color)
        print('PASS test_postgres_legacy_edit_captures_new_color_without_rewriting_history '+trained_color)
    postgres_store.close_pools()


def test_postgres_modern_color_wins_over_repertoire_fallback():
    fixture = _create_color_fixture('black', card_color='white')
    assert _transport_color_fixture(fixture, evidence=False)['trained_color'] == 'white'
    _assert_color_round_trip(fixture, 'white')
    _retain_color_provenance(fixture)
    print('PASS test_postgres_modern_color_wins_over_repertoire_fallback')


def test_postgres_shared_legacy_color_requires_authoritative_admission():
    for trained_color in ('white', 'black'):
        fixture = _create_color_fixture(trained_color, shared=True)
        opposite = 'black' if trained_color=='white' else 'white'
        assert _transport_color_fixture(fixture, evidence=False)['trained_color'] == opposite
        with postgres_store.connection() as database:
            unbound = database.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,card_bucket) VALUES(?,?,2,2000,'opening') RETURNING id",
                                       (date.today().isoformat(), fixture['card_id'])).fetchone()[0]
        ambiguous = _transport_color_fixture(fixture, queue_id=unbound)
        assert 'opening_decision_manifest' not in ambiguous
        assert 'ambiguous' in ambiguous['opening_evidence_diagnostic']
        with postgres_store.connection(read_only=True) as database:
            assert not database.execute_native('SELECT 1 FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s', (unbound,)).fetchone()
        _assert_color_round_trip(fixture, trained_color)
        _retain_color_provenance(fixture)
        print('PASS test_postgres_shared_legacy_color_requires_authoritative_admission '+trained_color)


def test_postgres_shared_review_requeue_inherits_authoritative_evidence_scope():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Shared requeue rehearsal requires disposable PostgreSQL')
    os.environ['TEMPO_DATABASE_WRITE_URL'] = os.getenv('TEMPO_SHADOW_REHEARSAL_URL', 'postgresql://postgres@postgres:5432/tempo')
    os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
    from app.review_commands import submit_review
    for trained_color in ('white', 'black'):
        fixture = _create_color_fixture(trained_color, shared=True)
        manifest = _transport_color_fixture(fixture)['opening_decision_manifest']
        assert manifest['repertoire_id'] == fixture['repertoire_id']
        assert manifest['trained_color'] == trained_color
        completion = _color_checkpoint(fixture, manifest).model_dump(mode='json')
        completion['terminal']['state'] = 'complete'
        payload = {'card_id':fixture['card_id'],
                   'review':{'outcome':'correct','queue_entry_id':fixture['queue_id'],
                             'attempt_id':completion['attempt_id'],'recorded_at':fixture['now'].isoformat(),
                             'opening_evidence_completion':completion},
                   'prepared_manifest':prepare_checkpoint(OpeningEvidenceCheckpoint.model_validate(completion))}
        context_query = ('SELECT context.*,context.xmin::text AS row_version '
                         'FROM opening_evidence_queue_contexts context '
                         'WHERE queue_entry_id IN (%s,%s) ORDER BY queue_entry_id')
        with postgres_store.connection() as database:
            assert database.execute('SELECT COUNT(*) FROM repertoire_cards WHERE card_id=?', (fixture['card_id'],)).fetchone()[0] == 2
            result = submit_review(database, payload)
            assert result['persisted'] and result['idempotent'] is False and result['requeue_entry_id'] is not None
            repeat_id = result['requeue_entry_id']
            assert database.execute('SELECT admission_repertoire_id FROM daily_queue WHERE id=?', (repeat_id,)).fetchone()[0] is None
            contexts = [dict(row) for row in database.execute_native(context_query, (fixture['queue_id'], repeat_id)).fetchall()]
            inherited = next(row for row in contexts if row['queue_entry_id']==repeat_id)
            assert inherited['repertoire_id'] == manifest['repertoire_id']
            assert inherited['presentation_snapshot_id'] == manifest['presentation_snapshot_id']
            assert inherited['effective_trained_color'] == trained_color
        transport = _transport_color_fixture(fixture, queue_id=repeat_id)
        assert 'opening_decision_manifest' in transport, 'Shared repeat rejected its uniquely proven context: '+str(transport.get('opening_evidence_diagnostic'))
        assert transport['opening_decision_manifest'] == manifest
        assert transport['trained_color'] == trained_color and 'opening_evidence_diagnostic' not in transport
        repeat = _color_checkpoint(fixture, manifest).model_dump(mode='json')
        repeat.update(attempt_id=fixture['card_id']+'-repeat',origin_queue_entry_id=repeat_id,
                      queue_entry_id=repeat_id,parent_attempt_id=completion['attempt_id'])
        assert prepare_checkpoint(OpeningEvidenceCheckpoint.model_validate(repeat)) == manifest
        with postgres_store.connection() as database:
            scheduling = _fixture_scheduling(database, fixture)
            assert persist_checkpoint(database, {'checkpoint':repeat,'prepared_manifest':manifest})['persisted']
            assert _fixture_scheduling(database, fixture) == scheduling
        with postgres_store.connection() as database:
            assert submit_review(database, payload) == result
            assert _fixture_scheduling(database, fixture) == scheduling
            assert [dict(row) for row in database.execute_native(context_query, (fixture['queue_id'], repeat_id)).fetchall()] == contexts
        _retain_color_provenance(fixture)
        print('PASS test_postgres_shared_review_requeue_inherits_authoritative_evidence_scope '+trained_color)


def test_postgres_evidence_context_requires_unique_scope_and_matching_admission():
    fixture = _create_color_fixture('white', shared=True)
    manifest = _transport_color_fixture(fixture)['opening_decision_manifest']
    with postgres_store.connection() as database:
        # A context belonging to B cannot override explicit queue admission A.
        database.execute_native('DELETE FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s', (fixture['queue_id'],))
        database.execute_native('INSERT INTO opening_evidence_queue_contexts VALUES(%s,%s,%s,%s)',
                                (fixture['queue_id'],manifest['presentation_snapshot_id'],fixture['alternate'],'black'))
    conflict = _transport_color_fixture(fixture)
    assert 'opening_decision_manifest' not in conflict and 'ambiguous' in conflict['opening_evidence_diagnostic']
    with postgres_store.connection() as database:
        unbound = database.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,card_bucket) VALUES(?,?,2,2000,'opening') RETURNING id",
                                   (date.today().isoformat(), fixture['card_id'])).fetchone()[0]
        for repertoire,color in [(fixture['repertoire_id'],'white'),(fixture['alternate'],'black')]:
            database.execute_native('INSERT INTO opening_evidence_queue_contexts VALUES(%s,%s,%s,%s)',
                                    (unbound,manifest['presentation_snapshot_id'],repertoire,color))
    ambiguous = _transport_color_fixture(fixture, queue_id=unbound)
    assert 'opening_decision_manifest' not in ambiguous and 'ambiguous' in ambiguous['opening_evidence_diagnostic']
    _retain_color_provenance(fixture)
    print('PASS test_postgres_evidence_context_requires_unique_scope_and_matching_admission')


def test_postgres_legacy_review_requeue_inherits_color_after_source_replacement():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Requeue color rehearsal requires disposable PostgreSQL')
    os.environ['TEMPO_DATABASE_WRITE_URL'] = os.getenv('TEMPO_SHADOW_REHEARSAL_URL', 'postgresql://postgres@postgres:5432/tempo')
    os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
    for original_color, replacement_color in (('white', 'black'), ('black', 'white')):
        fixture = _create_color_fixture(original_color)
        manifest = _transport_color_fixture(fixture)['opening_decision_manifest']
        completion = _color_checkpoint(fixture, manifest).model_dump(mode='json')
        completion['terminal']['state'] = 'complete'
        context_query = ('SELECT context.*,context.xmin::text AS row_version '
                         'FROM opening_evidence_queue_contexts context '
                         'JOIN opening_evidence_presentations snapshot ON snapshot.id=context.presentation_snapshot_id '
                         'WHERE snapshot.card_id=%s ORDER BY context.queue_entry_id')
        with postgres_store.connection() as database:
            original_card = dict(database.execute('SELECT * FROM cards WHERE id=?', (fixture['card_id'],)).fetchone())
            original_contexts = [dict(row) for row in database.execute_native(context_query, (fixture['card_id'],)).fetchall()]
            database.execute('DELETE FROM repertoire_lines WHERE repertoire_id=?', (fixture['repertoire_id'],))
            database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)',
                             (fixture['card_id']+'-replacement', fixture['repertoire_id'], 'Changed source color',
                              replacement_color, original_card['start_fen'], original_card['moves_json'], fixture['now'].isoformat()))
            assert dict(database.execute('SELECT * FROM cards WHERE id=?', (fixture['card_id'],)).fetchone()) == original_card
        assert _transport_color_fixture(fixture, evidence=False)['trained_color'] == replacement_color
        authoritative = prepare_checkpoint(OpeningEvidenceCheckpoint.model_validate(completion))
        assert authoritative == manifest and manifest['trained_color'] == original_color
        payload = {'card_id':fixture['card_id'],
                   'review':{'outcome':'correct','queue_entry_id':fixture['queue_id'],
                             'attempt_id':completion['attempt_id'],'recorded_at':fixture['now'].isoformat(),
                             'opening_evidence_completion':completion}, 'prepared_manifest':authoritative}
        with postgres_store.connection() as database:
            result = submit_review(database, payload)
            assert result['persisted'] and result['requeue_entry_id'] and result['idempotent'] is False
            contexts = [dict(row) for row in database.execute_native(context_query, (fixture['card_id'],)).fetchall()]
            inherited = next(row for row in contexts if row['queue_entry_id'] == result['requeue_entry_id'])
            assert inherited['effective_trained_color'] == original_color, (
                f"Requeue inherited mutable {inherited['effective_trained_color']} instead of validated {original_color}")
            assert inherited['presentation_snapshot_id'] == manifest['presentation_snapshot_id']
            assert inherited['repertoire_id'] == manifest['repertoire_id']
            assert [row for row in contexts if row['queue_entry_id'] != result['requeue_entry_id']] == original_contexts
        repeat_transport = _transport_color_fixture(fixture, queue_id=result['requeue_entry_id'])
        assert repeat_transport['trained_color'] == original_color
        assert repeat_transport['opening_decision_manifest'] == manifest
        assert 'opening_evidence_diagnostic' not in repeat_transport
        repeat = _color_checkpoint(fixture, manifest).model_dump(mode='json')
        repeat.update(attempt_id=fixture['card_id']+'-repeat', origin_queue_entry_id=result['requeue_entry_id'],
                      queue_entry_id=result['requeue_entry_id'], parent_attempt_id=completion['attempt_id'])
        repeat_request = OpeningEvidenceCheckpoint.model_validate(repeat)
        assert prepare_checkpoint(repeat_request) == manifest
        with postgres_store.connection() as database:
            scheduling_before = _fixture_scheduling(database, fixture)
            assert persist_checkpoint(database, {'checkpoint':repeat, 'prepared_manifest':manifest})['persisted']
            assert _fixture_scheduling(database, fixture) == scheduling_before
        # A new delivery can replay the receipt's original idempotent=False result.
        # Its committed contexts must not even acquire a new PostgreSQL row version.
        with postgres_store.connection() as database:
            assert submit_review(database, payload) == result
            assert _fixture_scheduling(database, fixture) == scheduling_before
            assert [dict(row) for row in database.execute_native(context_query, (fixture['card_id'],)).fetchall()] == contexts
        _retain_color_provenance(fixture)
        print('PASS test_postgres_legacy_review_requeue_inherits_color_after_source_replacement '+original_color+'->'+replacement_color)


def test_postgres_legacy_review_requeue_preserves_validated_color():
    for trained_color in ('white', 'black'):
        fixture = _create_color_fixture(trained_color)
        manifest = _transport_color_fixture(fixture)['opening_decision_manifest']
        completion = _color_checkpoint(fixture, manifest).model_dump(mode='json')
        completion['terminal']['state'] = 'complete'
        authoritative = prepare_checkpoint(OpeningEvidenceCheckpoint.model_validate(completion))
        with postgres_store.connection() as database:
            result = submit_review(database, {'card_id':fixture['card_id'],
                'review':{'outcome':'correct','queue_entry_id':fixture['queue_id'],
                         'attempt_id':completion['attempt_id'],'recorded_at':fixture['now'].isoformat(),
                         'opening_evidence_completion':completion}, 'prepared_manifest':authoritative})
            assert result['persisted'] and result['requeue_entry_id']
            assert database.execute_native('SELECT effective_trained_color FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s AND presentation_snapshot_id=%s AND repertoire_id=%s',
                (result['requeue_entry_id'], manifest['presentation_snapshot_id'], fixture['repertoire_id'])).fetchone()[0] == trained_color
        repeat_transport = _transport_color_fixture(fixture, queue_id=result['requeue_entry_id'])
        assert repeat_transport['opening_decision_manifest'] == manifest
        repeat = _color_checkpoint(fixture, manifest).model_dump(mode='json')
        repeat.update(attempt_id=fixture['card_id']+'-repeat', origin_queue_entry_id=result['requeue_entry_id'],
                      queue_entry_id=result['requeue_entry_id'], parent_attempt_id=completion['attempt_id'])
        assert prepare_checkpoint(OpeningEvidenceCheckpoint.model_validate(repeat)) == manifest
        _retain_color_provenance(fixture)
        print('PASS test_postgres_legacy_review_requeue_preserves_validated_color '+trained_color)


def test_postgres_missing_or_invalid_legacy_color_omits_context():
    for line_color, card_color in [(None,None),('unknown',None),('white','unknown')]:
        fixture = _create_color_fixture(line_color, card_color=card_color)
        transport = _transport_color_fixture(fixture)
        assert 'opening_decision_manifest' not in transport
        assert 'trained color' in transport['opening_evidence_diagnostic']
        with postgres_store.connection(read_only=True) as database:
            assert not database.execute_native('SELECT 1 FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s', (fixture['queue_id'],)).fetchone()
        _retain_color_provenance(fixture)
    print('PASS test_postgres_missing_or_invalid_legacy_color_omits_context')


def test_postgres_legacy_color_line_order_is_deterministic():
    fixture = _create_color_fixture('white', tied_lines=True)
    _assert_color_round_trip(fixture, 'white')
    _retain_color_provenance(fixture)
    print('PASS test_postgres_legacy_color_line_order_is_deterministic')


def test_postgres_shadow_replay_atomicity_and_scheduling_invariance():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Shadow rehearsal requires a disposable PostgreSQL instance')
    os.environ['TEMPO_DATABASE_WRITE_URL'] = os.getenv('TEMPO_SHADOW_REHEARSAL_URL','postgresql://postgres@postgres:5432/tempo')
    os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
    prefix = 'shadow-rehearsal-' + uuid.uuid4().hex
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    moves = ['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4','g8f6','e1g1','f8e7','f1e1']
    with postgres_store.connection() as database:
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)", (prefix,'Shadow rehearsal','test',now.isoformat()))
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date) VALUES(?,?,'prefix',?,?,'white','new',?)",
                         (prefix,prefix,fen,json.dumps(moves),date.today().isoformat()))
        database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",(prefix,prefix))
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,unlock_after_card_id) VALUES(?,?,'response',?,'[\"e2e4\"]','white','locked',?,?)",
                         (prefix+'-child',prefix,fen,date.today().isoformat(),prefix))
        queue = database.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_repertoire_id) VALUES(?,?,999,'opening',?) RETURNING id",
                                 (date.today().isoformat(),prefix,prefix)).fetchone()[0]
        snapshot = database.execute_native('SELECT * FROM opening_evidence_presentations WHERE card_id=%s', (prefix,)).fetchone()
    from app.services.opening_decision_evidence import decision_manifest
    manifest = decision_manifest(dict(snapshot),prefix)
    transport_card={'id':prefix,'queue_entry_id':queue,'content_type':'opening','revision':1,
                    'start_fen':fen,'moves':moves,'trained_color':'white'}
    queue_manifests([transport_card])
    assert transport_card['opening_decision_manifest']==manifest,'Live queue manifest was not authoritative'
    # Missing admission scope cannot choose between eligible owners. An explicit
    # admission remains authoritative even when the same card is shared elsewhere.
    alternate_repertoire=prefix+'-alternate'
    with postgres_store.connection() as database:
        database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)',
                         (alternate_repertoire,'Other shadow scope','test',now.isoformat()))
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)',(alternate_repertoire,prefix))
        unbound_queue=database.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,card_bucket) VALUES(?,?,1,1000,'opening') RETURNING id",
                                       (date.today().isoformat(),prefix)).fetchone()[0]
    explicit={**transport_card};unbound={**transport_card,'queue_entry_id':unbound_queue}
    explicit.pop('opening_decision_manifest');unbound.pop('opening_decision_manifest')
    queue_manifests([explicit,unbound])
    assert explicit['opening_decision_manifest']['repertoire_id']==prefix
    assert 'opening_decision_manifest' not in unbound and 'ambiguous' in unbound['opening_evidence_diagnostic']
    with postgres_store.connection() as database:
        database.execute('DELETE FROM daily_queue WHERE id=?',(unbound_queue,))
        database.execute('DELETE FROM repertoires WHERE id=?',(alternate_repertoire,))
    def checkpoint(attempt='partial', observed=None):
        return {'attempt_id':prefix+'-'+attempt,'manifest':manifest,'origin_queue_entry_id':queue,'queue_entry_id':queue,
                'parent_attempt_id':None,'started_at':(now-timedelta(days=3)).isoformat(),'study_timezone':'America/New_York',
                'source':'offline','events':[],'terminal':None}
    def event(index, sequence, kind='first_response', **fields):
        decision = manifest['decisions'][index]
        return {'sequence':sequence,'decision_index':index,'decision_id':decision['decision_id'],
                'expected_uci':decision['expected_uci'],'kind':kind,'observed_at':now.isoformat(),
                'response_uci':decision['expected_uci'] if kind in {'first_response','correction'} else None,
                'assistance':None,'disposition':'expected' if kind=='first_response' else None,**fields}
    def prepared(request):
        return {'checkpoint':request,'prepared_manifest':prepare_checkpoint(OpeningEvidenceCheckpoint.model_validate(request))}
    def save(request):
        payload=prepared(request)
        with postgres_store.connection() as database: return persist_checkpoint(database,payload)
    def scheduling_snapshot(database):
        return {table:[dict(row) for row in database.execute(statement,(prefix+'%',)).fetchall()]
                for table,statement in {
                    'cards':'SELECT * FROM cards WHERE id LIKE ? ORDER BY id',
                    'reviews':'SELECT * FROM reviews WHERE card_id LIKE ? ORDER BY id',
                    'queue':'SELECT * FROM daily_queue WHERE card_id LIKE ? ORDER BY id',
                    'splits':'SELECT * FROM prefix_splits WHERE source_card_id LIKE ? ORDER BY source_card_id',
                }.items()}
    with postgres_store.connection(read_only=True) as database: unchanged=scheduling_snapshot(database)
    partial=checkpoint()
    partial['events']=[event(i,i+1) for i in range(4)]+[event(4,5,response_uci='d2d3',disposition='wrong'),event(4,6,'reveal'),event(4,7,'correction')]
    partial['terminal']={'state':'partial','final_sequence':7,'ended_at':now.isoformat()}
    late=copy.deepcopy(partial);late['events']=partial['events'][1:]
    assert save(late)['contiguous_sequence']==0
    with ThreadPoolExecutor(max_workers=3) as workers:
        results=list(workers.map(lambda _:save(partial),range(3)))
    assert all(result['contiguous_sequence']==7 for result in results)
    with postgres_store.connection(read_only=True) as database:
        assert scheduling_snapshot(database)==unchanged,'Checkpoints changed scheduling tables'
        observations=database.execute_native('SELECT observation_json FROM opening_evidence_observations WHERE attempt_id=%s ORDER BY decision_index',(partial['attempt_id'],)).fetchall()
        assert len(observations)==5
        assert [json.loads(row[0])['clean'] for row in observations]==[True]*4+[False]
        assert json.loads(observations[-1][0])['corrected']
    for changed in ('event','context','terminal','beyond'):
        conflict=copy.deepcopy(partial)
        if changed=='event':conflict['events'][0]['response_uci']='d2d4'
        if changed=='context':conflict['study_timezone']='UTC'
        if changed=='terminal':conflict['terminal']['final_sequence']=6;conflict['events']=conflict['events'][:6]
        if changed=='beyond':conflict['events'].append(event(5,8))
        try:save(conflict)
        except HTTPException as error:assert error.status_code==409
        else:raise AssertionError('Conflicting '+changed+' was accepted')
    # Two genuine same-day attempts and late historical replay add counts, never replace newer facts.
    for label,days in [('same-day-one',0),('same-day-two',0),('historical',2)]:
        attempt=checkpoint(label);attempt['events']=[event(0,1,observed_at=(now-timedelta(days=days)).isoformat())]
        attempt['terminal']={'state':'partial','final_sequence':1,'ended_at':now.isoformat()};save(attempt)
    with postgres_store.connection(read_only=True) as database:
        summary=decision_evidence(database,manifest['decisions'][0]['decision_id'])
        assert summary['summary']['first_responses']==4 and summary['summary']['distinct_clean_days']==2
        assert summary['recent_outcomes'][-1]['attempt_id'].endswith('historical')
        assert len(decision_evidence(database,manifest['decisions'][0]['decision_id'],limit=2)['observations'])==2
    # Reconnect/restart: close pools, replay exactly, and verify immutable event rows persist.
    postgres_store.close_pools();assert save(partial)['state']=='partial'
    # Same card, same clock and logical review: roll back each branch of the parity comparison.
    from fsrs import Card
    from app.services.prefix_split import preview_prefix_split
    class FixedIdentityCard(Card):
        def __init__(self, card_id=None, **arguments):
            super().__init__(card_id=42 if card_id is None else card_id, **arguments)
    for mode,outcome,guided in [('normal','correct',False),('normal','again',False),('normal','correct',True),('light','correct',False),('hard','correct',False)]:
        with patch("app.services.scheduler.Card", FixedIdentityCard), postgres_store.connection() as database:
            database.execute("UPDATE cards SET scheduling_mode=? WHERE id=?",(mode,prefix))
            logical=checkpoint('parity-'+mode+'-'+outcome+'-'+str(guided))
            logical['events']=[event(0,1)]
            logical['terminal']={'state':'complete','final_sequence':1,'ended_at':now.isoformat()}
            review={'outcome':outcome,'guided':guided,'queue_entry_id':queue,'attempt_id':logical['attempt_id'],'recorded_at':now.isoformat()}
            database.execute_native('SAVEPOINT comparable')
            absent=submit_review(database,{'card_id':prefix,'review':review})
            absent_state=scheduling_snapshot(database)
            absent_preview=preview_prefix_split(database,prefix)
            absent_cards=absent_state['cards']
            database.execute_native('ROLLBACK TO SAVEPOINT comparable')
            enabled=submit_review(database,{'card_id':prefix,'review':{**review,'opening_evidence_completion':logical},'prepared_manifest':manifest})
            enabled_cards=scheduling_snapshot(database)['cards']
            differences=[{key:(left[key],right[key]) for key in left if left[key]!=right[key]} for left,right in zip(absent_cards,enabled_cards)]
            assert enabled_cards==absent_cards,f'Shadow changed scheduling: {differences}'
            def normalized_rows(rows):
                normalized=[]
                for row in rows:
                    record={key:value for key,value in row.items() if key not in {'id','review_result_json'}}
                    if row.get('review_result_json'):
                        result=json.loads(row['review_result_json'])
                        record['review_result_json']={key:value for key,value in result.items() if key not in {'review_id','requeue_entry_id'}}
                    normalized.append(record)
                return normalized
            enabled_state=scheduling_snapshot(database)
            assert preview_prefix_split(database,prefix)==absent_preview,'Shadow changed prefix-split preview'
            for table in ('reviews','queue','splits'):
                assert normalized_rows(enabled_state[table])==normalized_rows(absent_state[table]),f'Shadow changed {table}'
            assert {k:v for k,v in enabled.items() if k not in {'review_id','requeue_entry_id'}}=={k:v for k,v in absent.items() if k not in {'review_id','requeue_entry_id'}}
            assert submit_review(database,{'card_id':prefix,'review':{**review,'opening_evidence_completion':logical},'prepared_manifest':manifest})==enabled
            database.execute_native('ROLLBACK TO SAVEPOINT comparable')
    with postgres_store.connection() as database:
        database.execute("UPDATE cards SET scheduling_mode='normal' WHERE id=?",(prefix,))
        test_offline_repeat_reconciles_parent_aggregate_only_fallback(database,prefix,queue,checkpoint,event,manifest,now.isoformat())
    # A rejected aggregate review rolls back events, observations and completion together.
    atomic=checkpoint('atomic');atomic['events']=[event(0,1)];atomic['terminal']={'state':'complete','final_sequence':1,'ended_at':now.isoformat()}
    try:
        with postgres_store.connection() as database:
            submit_review(database,{'card_id':prefix,'review':{'outcome':'correct','guided':False,'queue_entry_id':queue,'attempt_id':atomic['attempt_id'],
              'expected_review_id':999999999,'opening_evidence_completion':atomic},'prepared_manifest':manifest})
    except HTTPException as error:assert error.status_code==409
    else:raise AssertionError('Stale review succeeded')
    with postgres_store.connection(read_only=True) as database:
        assert not database.execute_native('SELECT 1 FROM opening_evidence_attempts WHERE attempt_id=%s',(atomic['attempt_id'],)).fetchone()
    # Persist a final review and receipt, then credit a competing phone attempt through existing reconciliation.
    final=checkpoint('completed');final['events']=[event(0,1)];final['terminal']={'state':'complete','final_sequence':1,'ended_at':now.isoformat()}
    with postgres_store.connection() as database:
        database.execute("UPDATE cards SET scheduling_mode='normal' WHERE id=?",(prefix,))
        result=submit_review(database,{'card_id':prefix,'review':{'outcome':'correct','queue_entry_id':queue,'attempt_id':final['attempt_id'],
          'recorded_at':now.isoformat(),'opening_evidence_completion':final},'prepared_manifest':manifest})
        assert result['persisted']
        assert database.execute_native('SELECT state FROM opening_evidence_attempts WHERE attempt_id=%s',(final['attempt_id'],)).fetchone()[0]=='complete'
    # Capture content replacement with actual revision and retain the old provenance.
    with postgres_store.connection() as database:
        database.execute("UPDATE cards SET revision=2,moves_json='[\"d2d4\"]' WHERE id=?",(prefix,))
        assert database.execute_native('SELECT COUNT(*) FROM opening_evidence_presentations WHERE card_id=%s',(prefix,)).fetchone()[0]==2
    assert save(partial)['contiguous_sequence']==7
    historical=checkpoint('competing-phone');historical['events']=[event(0,1,observed_at=(now-timedelta(minutes=1)).isoformat())]
    historical['terminal']={'state':'complete','final_sequence':1,'ended_at':(now-timedelta(minutes=1)).isoformat()}
    with postgres_store.connection() as database:
        current=scheduling_snapshot(database)['cards']
        reconciled=submit_review(database,{'card_id':prefix,'review':{'outcome':'correct','queue_entry_id':queue,'attempt_id':historical['attempt_id'],
          'expected_revision':1,'recorded_at':historical['terminal']['ended_at'],'opening_evidence_completion':historical},'prepared_manifest':manifest})
        assert reconciled['reconciliation']=='history_only' and reconciled['persisted']
        assert scheduling_snapshot(database)['cards']==current
        assert database.execute_native('SELECT state FROM opening_evidence_attempts WHERE attempt_id=%s',(historical['attempt_id'],)).fetchone()[0]=='complete'
    # Retain this bounded fixture in the disposable DB for service recreation and backup/restore stages.
    print(json.dumps({'test':'test_postgres_shadow_replay_atomicity_and_scheduling_invariance','repertoire_id':prefix,
                      'concurrent_exact_replays':3,'parity_cases':5,'historical_clean_days':2,'retained_for_backup':True}))
    with postgres_store.connection() as database:
        # Retain only shadow provenance for restore. Active fixture cards would
        # compete with the next scenario's foreground queue and quota admission.
        database.execute('DELETE FROM cards WHERE id IN (?,?)',(prefix,prefix+'-child'))
        database.execute('DELETE FROM repertoires WHERE id=?',(prefix,))
        digest=shadow_digest(database,prefix)
        database.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES(%s,'shadow.evidence.fixture',%s,'complete',%s)",(prefix,digest,json.dumps({'digest':digest})))
    postgres_store.close_pools()


if __name__=='__main__':
    if '--verify-persisted' in sys.argv:verify_persisted_shadow()
    elif '--crash-after-prepare' in sys.argv:_crash_worker_after_checkpoint_preparation()
    else:
        if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
            raise RuntimeError('Opening evidence rehearsal requires disposable PostgreSQL')
        os.environ['TEMPO_DATABASE_WRITE_URL'] = os.getenv('TEMPO_SHADOW_REHEARSAL_URL', 'postgresql://postgres@postgres:5432/tempo')
        os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
        assert os.getenv('TEMPO_REDIS_URL'), 'Opening evidence admission proof requires runner-owned Redis'
        from check_postgres_prefix_diagnostics import test_postgres_prefix_diagnostics_reducer_scope_bounds_and_foreground_admission
        test_postgres_prefix_diagnostics_reducer_scope_bounds_and_foreground_admission()
        test_postgres_opening_checkpoint_http_admission_preserves_saved_payload_replay()
        test_postgres_opening_attempt_http_admission_preserves_foreground_diagnostics()
        test_postgres_checkpoint_driver_retries_only_foreground_deferral()
        test_postgres_opening_checkpoint_reduction_yields_to_foreground_review()
        test_postgres_opening_checkpoint_stale_preparation_preserves_foreground_completion()
        test_postgres_opening_checkpoint_restart_recomputes_original_receipt()
        test_postgres_shared_review_requeue_inherits_authoritative_evidence_scope()
        test_postgres_evidence_context_requires_unique_scope_and_matching_admission()
        test_postgres_legacy_review_requeue_inherits_color_after_source_replacement()
        test_postgres_legacy_opening_color_queue_checkpoint_round_trip()
        test_postgres_modern_color_wins_over_repertoire_fallback()
        test_postgres_shared_legacy_color_requires_authoritative_admission()
        test_postgres_legacy_review_requeue_preserves_validated_color()
        test_postgres_missing_or_invalid_legacy_color_omits_context()
        test_postgres_legacy_color_line_order_is_deterministic()
        test_postgres_shadow_replay_atomicity_and_scheduling_invariance()
