"""Issue #82 real PostgreSQL/Redis proof, called by the existing evidence rehearsal."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import patch
import json
import os
from time import monotonic, sleep
import uuid

from fastapi.testclient import TestClient
from app import main, postgres_store
from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
from app.services.opening_decision_evidence import decision_manifest, reduce_observations
from app.services.postgres_opening_evidence import persist_checkpoint
from app.services import redis_admission_gate
from app.services.activity_gate import activity_gate
from app.services.prefix_diagnostics import project_prefix_diagnostics


def idle_prefix_read(client, url):
    """Only a known foreground wait can defer an otherwise idle proof read."""
    deadline = monotonic() + 10
    while True:
        response = client.get(url)
        if response.status_code != 503 or response.json().get('detail') != 'Waiting for foreground activity':
            return response
        assert monotonic() < deadline, 'Prefix read did not obtain foreground-idle admission within 10 seconds'
        while redis_admission_gate.foreground_present() and monotonic() < deadline:
            sleep(0.01)


def test_postgres_prefix_read_driver_retains_missing_sources_and_real_admission():
    denied = Event()
    server = redis_admission_gate.client()
    original_eval = server.eval
    def observe_eval(script, *arguments):
        result = original_eval(script, *arguments)
        if script == redis_admission_gate._CLAIM_BACKGROUND and result == 0:
            denied.set()
        return result
    client = TestClient(main.app)
    with ThreadPoolExecutor(max_workers=1) as executor, patch.object(server, 'eval', observe_eval):
        with redis_admission_gate.foreground_lease():
            result = executor.submit(idle_prefix_read, client, '/api/repertoires/absent-'+uuid.uuid4().hex+'/prefix-diagnostics')
            assert denied.wait(5) and not result.done()
        response = result.result(timeout=10)
        assert response.status_code == 404, response.text
    from types import SimpleNamespace
    unavailable = SimpleNamespace(status_code=503, json=lambda: {'detail':'provider unavailable'})
    assert idle_prefix_read(SimpleNamespace(get=lambda _url: unavailable), '/unavailable') is unavailable
    print('PASS test_postgres_prefix_read_driver_retains_missing_sources_and_real_admission (real Redis denial, original 404/provider errors)')


def test_postgres_prefix_diagnostics_reducer_scope_bounds_and_foreground_admission():
    test_postgres_prefix_read_driver_retains_missing_sources_and_real_admission()
    repertoire_id = 'prefix-diagnostics-' + uuid.uuid4().hex
    try:
        _assert_prefix_diagnostics(repertoire_id)
    finally:
        # Keep durable shadow evidence, but remove this fixture's active routes.
        # Otherwise later global game-comparison tests can select our repertoire.
        with postgres_store.connection() as database:
            database.execute('DELETE FROM cards WHERE id=?', (repertoire_id,))
            database.execute('DELETE FROM repertoires WHERE id IN (?,?)', (repertoire_id, repertoire_id+'-shared'))
        test_postgres_prefix_diagnostics_fixture_does_not_leave_eligible_routes(repertoire_id)


def test_postgres_prefix_diagnostics_fixture_does_not_leave_eligible_routes(repertoire_id):
    with postgres_store.connection(read_only=True) as database:
        assert not database.execute('SELECT 1 FROM cards WHERE id=?', (repertoire_id,)).fetchone()
        assert not database.execute('SELECT 1 FROM repertoires WHERE id IN (?,?)', (repertoire_id, repertoire_id+'-shared')).fetchone()
    print('PASS test_postgres_prefix_diagnostics_fixture_does_not_leave_eligible_routes')


def _assert_prefix_diagnostics(repertoire_id):
    card_id = repertoire_id
    other_scope = repertoire_id + '-shared'
    line_id = repertoire_id + '-line'
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    moves = ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5']
    with postgres_store.connection() as database:
        for scope in [repertoire_id, other_scope]:
            database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)',
                             (scope, 'Prefix diagnostics', 'test', now.isoformat()))
            database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)',
                             (scope+'-line', scope, 'Three decisions', 'white', fen, json.dumps(moves), now.isoformat()))
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,due_date) VALUES(?,?,'prefix',?,?,'white',?)",
                         (card_id, repertoire_id, fen, json.dumps(moves), now.date().isoformat()))
        for scope in [repertoire_id, other_scope]:
            database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)', (scope, card_id))
        database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(?,1,'ready',?)", (repertoire_id, now.isoformat()))
        database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index) VALUES(?,1,?,0,?,'fixture',?,?,'white','prefix',0,2)",
                         (repertoire_id, line_id, card_id, fen, json.dumps(moves)))
        queue_ids = {}
        for index, scope in enumerate([repertoire_id, other_scope]):
            queue_ids[scope] = database.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,card_bucket,admission_repertoire_id) VALUES(?,?,?,?,'opening',?) RETURNING id",
                                               (now.date().isoformat(), card_id, index, 2300+index, scope)).fetchone()[0]
        snapshot = dict(database.execute_native('SELECT * FROM opening_evidence_presentations WHERE card_id=%s', (card_id,)).fetchone())
    manifest = decision_manifest(snapshot, repertoire_id)
    client = TestClient(main.app)
    base_url = f'/api/repertoires/{repertoire_id}/prefix-diagnostics'
    list_response = idle_prefix_read(client, base_url)
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()['prefixes'][0]['manifest'] == manifest
    detail_url = base_url + '/' + card_id + '?manifest_id=' + manifest['manifest_id'] + '&graph_generation=1'
    unknown = idle_prefix_read(client, detail_url)
    assert unknown.status_code == 200, unknown.text
    assert all(decision['coverage'] == 'unknown' for decision in unknown.json()['decisions'])
    expected_attempts, expected_observations = [], []
    for index in range(102):
        timestamp = (now - timedelta(days=2-index//34, seconds=102-index)).isoformat()
        decision = manifest['decisions'][0]
        def event(sequence, kind='first_response', **changes):
            return {'sequence': sequence, 'decision_index': 0, 'decision_id': decision['decision_id'],
                    'expected_uci': decision['expected_uci'], 'kind': kind, 'observed_at': timestamp,
                    'response_uci': decision['expected_uci'] if kind in {'first_response', 'correction'} else None,
                    'assistance': None, 'disposition': None, **changes}
        events = [event(1)]
        if index >= 100:
            events = [event(1, 'assistance', assistance='hint'), event(2)]
        elif index == 99:
            events = [event(1, response_uci='d2d3'), event(2, 'reveal'), event(3, 'correction')]
        elif index == 98:
            second = manifest['decisions'][1]
            events += [event(2, 'manual_failure', decision_index=1, decision_id=second['decision_id'], expected_uci=second['expected_uci']),
                       event(3, 'correction', decision_index=1, decision_id=second['decision_id'], expected_uci=second['expected_uci'], response_uci=second['expected_uci'])]
        attempt_id = f'{card_id}-{index:03}'
        checkpoint = OpeningEvidenceCheckpoint(attempt_id=attempt_id, manifest=manifest,
            origin_queue_entry_id=queue_ids[repertoire_id], started_at=timestamp, study_timezone='America/New_York',
            events=events, terminal={'state':'partial', 'final_sequence':len(events), 'ended_at':timestamp})
        with postgres_store.connection() as database:
            persisted = persist_checkpoint(database, {'checkpoint':checkpoint.model_dump(mode='json'), 'prepared_manifest':manifest})
            assert persisted['state'] == 'partial'
            if index == 99:
                assert persist_checkpoint(database, {'checkpoint':checkpoint.model_dump(mode='json'), 'prepared_manifest':manifest}) == persisted
        expected_attempts.append({'attempt_id':attempt_id, 'manifest_id':manifest['manifest_id'], 'state':'partial', 'started_at':datetime.fromisoformat(timestamp)})
        expected_observations += [{'attempt_id':attempt_id,'observation':observation} for observation in reduce_observations(events,'America/New_York')]
    # Shared decision identities must not pull another repertoire's observations.
    other_manifest = decision_manifest(snapshot, other_scope)
    other_decision = other_manifest['decisions'][0]
    with postgres_store.connection() as database:
        for index in range(3):
            checkpoint = OpeningEvidenceCheckpoint(attempt_id=f'{other_scope}-{index}',manifest=other_manifest,
                origin_queue_entry_id=queue_ids[other_scope],started_at=now.isoformat(),study_timezone='UTC',
                events=[{'sequence':1,'decision_index':0,'decision_id':other_decision['decision_id'],
                  'expected_uci':other_decision['expected_uci'],'kind':'first_response','observed_at':now.isoformat(),'response_uci':'e2e4'}])
            persist_checkpoint(database, {'checkpoint':checkpoint.model_dump(mode='json'),'prepared_manifest':other_manifest})
        # Real legacy aggregate review exists, but provides no decision credit.
        database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,internal_rating) VALUES(?,'good',?,0,1,'good')", (card_id, now.isoformat()))
    from check_postgres_opening_evidence import _fixture_scheduling, shadow_digest
    fixture = {'card_id':card_id}
    with postgres_store.connection(read_only=True) as database:
        before = _fixture_scheduling(database, fixture)
        shadow_before = shadow_digest(database, repertoire_id)
    expected_attempts.reverse()
    selected = {attempt['attempt_id'] for attempt in expected_attempts[:100]}
    expected = project_prefix_diagnostics(manifest, 1, expected_attempts,
        [record for record in expected_observations if record['attempt_id'] in selected])
    statements = []
    original_sql = postgres_store.PostgresConnection.execute_native
    def observe_sql(database, sql, parameters=()):
        if sql.startswith('SELECT attempt_id,manifest_id'):
            assert database.raw.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
            assert database.raw.execute('SHOW transaction_timeout').fetchone()[0] == '250ms'
            assert database.raw.execute('SHOW lock_timeout').fetchone()[0] == '25ms'
            assert database.raw.execute('SHOW transaction_isolation').fetchone()[0] == 'repeatable read'
        if 'FROM opening_evidence_' in sql: statements.append((sql, parameters))
        return original_sql(database, sql, parameters)
    started = monotonic()
    with patch.object(postgres_store.PostgresConnection, 'execute_native', observe_sql):
        response = idle_prefix_read(client, detail_url)
    elapsed_ms = (monotonic()-started)*1000
    assert response.status_code == 200, response.text
    assert response.json() == expected
    decisions = response.json()['decisions']
    assert decisions[0]['clean_successes'] == 97 and decisions[0]['unassisted_first_response_failures'] == 1
    assert decisions[0]['assistance_before_response'] == 2 and decisions[0]['coverage'] == 'strong'
    assert decisions[1]['manual_failures'] == decisions[1]['corrections'] == 1
    assert decisions[2]['coverage'] == 'unknown' and decisions[2]['reached_observations'] == 0
    attempt_sql, parameters = next(record for record in statements if record[0].startswith('SELECT attempt_id,manifest_id'))
    assert parameters[-1] == 101
    with postgres_store.connection(read_only=True) as database:
        assert _fixture_scheduling(database, fixture) == before and shadow_digest(database, repertoire_id) == shadow_before
        database.raw.execute('SET LOCAL enable_seqscan=off')
        explain = database.execute_native('EXPLAIN (FORMAT JSON) '+attempt_sql, parameters).fetchone()[0]
        assert 'opening_evidence_presentation_attempt_window' in json.dumps(explain), explain
    # No header: middleware still marks this read as secondary, avoiding self-admission.
    server = redis_admission_gate.client()
    original_eval = server.eval
    denied, sql_started = Event(), Event()
    def observe_eval(script, *arguments):
        result = original_eval(script, *arguments)
        if script == redis_admission_gate._CLAIM_BACKGROUND and result == 0: denied.set()
        return result
    def admitted_sql(database, sql, parameters=()):
        if sql.startswith('SELECT id FROM repertoires'): sql_started.set()
        return original_sql(database, sql, parameters)
    with ThreadPoolExecutor(max_workers=1) as requests, patch.object(server,'eval',observe_eval), patch.object(postgres_store.PostgresConnection,'execute_native',admitted_sql):
        with redis_admission_gate.foreground_lease():
            pending = requests.submit(client.get, detail_url)
            assert denied.wait(5), 'Diagnostic did not yield to foreground admission'
            deferred = pending.result(timeout=5)
            assert deferred.status_code == 503 and deferred.headers['Retry-After'] == '1', deferred.text
            assert deferred.json() == {'detail': 'Waiting for foreground activity'}
            assert not sql_started.is_set()
            foreground = client.get('/api/settings')
            assert foreground.status_code == 200, foreground.text
            assert 'initial_depth' in foreground.json()
        retried = idle_prefix_read(client, detail_url)
        assert retried.status_code == 200 and retried.json() == expected, retried.text
    assert activity_gate.active_background_sections == 0 and server.zcard(redis_admission_gate._BACKGROUND_KEY) == 0
    with postgres_store.connection(read_only=True) as database:
        assert _fixture_scheduling(database, fixture) == before and shadow_digest(database, repertoire_id) == shadow_before
    # A new revision with identical decision identities starts unknown; old evidence survives.
    with postgres_store.connection() as database:
        database.execute('UPDATE cards SET revision=revision+1 WHERE id=?',(card_id,))
    assert idle_prefix_read(client, detail_url).status_code == 409
    new_manifest = idle_prefix_read(client, base_url).json()['prefixes'][0]['manifest']
    new_detail = idle_prefix_read(client, base_url+'/'+card_id+'?manifest_id='+new_manifest['manifest_id']+'&graph_generation=1')
    assert new_detail.status_code == 200 and all(decision['coverage']=='unknown' for decision in new_detail.json()['decisions'])
    assert new_manifest['decisions'][0]['decision_id'] == manifest['decisions'][0]['decision_id']
    print(f'PASS test_postgres_prefix_diagnostics_reducer_scope_bounds_and_foreground_admission (102 scoped attempts, 100 selected, indexed lookup, real Redis contention, unchanged scheduling/shadow digest; HTTP read {elapsed_ms:.3f}ms)')


if __name__ == '__main__':
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Prefix diagnostics rehearsal requires disposable PostgreSQL')
    with postgres_store.connection(read_only=True) as database:
        marker = database.execute_native("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0]
        if marker != 'tempo-disposable-postgres-test':
            raise RuntimeError('The database is not marked as runner-owned disposable state')
    test_postgres_prefix_diagnostics_reducer_scope_bounds_and_foreground_admission()
    postgres_store.close_pools()
