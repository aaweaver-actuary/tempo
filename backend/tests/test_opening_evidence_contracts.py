"""Compatibility and authoritative preparation seams in the regular suite."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def checkpoint_http_envelope():
    manifest = json.loads((Path(__file__).resolve().parents[2] / 'tests/fixtures/opening-evidence-manifest.json').read_text())
    return {'attempt_id': 'http-admission-attempt', 'manifest': manifest, 'origin_queue_entry_id': 101,
            'started_at': '2026-09-30T12:00:00Z', 'study_timezone': 'UTC'}


@pytest.mark.parametrize('background_header', [True, False])
def test_opening_checkpoint_http_dispatch_does_not_prepare_evidence_in_api(monkeypatch, checkpoint_http_envelope, background_header):
    from fastapi.testclient import TestClient
    from app import main, command_dispatch
    from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
    from app.services.activity_gate import activity_gate
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(main.postgres_store, 'connection',
                        lambda **options: pytest.fail('Checkpoint HTTP dispatch performed an API-side evidence source read'))
    submitted = []
    def dispatch(command_name, payload, **options):
        assert activity_gate.in_background, 'Checkpoint middleware acquired a foreground lease'
        submitted.append((command_name, payload, options))
        return {'operation_id': 'http-checkpoint-key', 'state': 'pending'}
    monkeypatch.setattr(command_dispatch, 'dispatch_command', dispatch)
    headers = {'Idempotency-Key': 'http-checkpoint-key', **({'X-Tempo-Work-Class': 'background'} if background_header else {})}
    response = TestClient(main.app).post('/api/opening-evidence/checkpoints', json=checkpoint_http_envelope, headers=headers)
    assert response.status_code == 200
    assert submitted == [('opening_evidence.checkpoint',
        {'checkpoint': OpeningEvidenceCheckpoint.model_validate(checkpoint_http_envelope).model_dump(mode='json')},
        {'idempotency_key': 'http-checkpoint-key', 'background': True})]


@pytest.fixture
def attempt_http_boundary(monkeypatch):
    from threading import Event
    from fastapi.testclient import TestClient
    from app import main
    from app.services.activity_gate import activity_gate
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL', raising=False)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    boundary = SimpleNamespace(reached=Event(), sql_started=Event(), sections=[], active_connections=0,
                               attempt={'attempt_id': 'http-attempt', 'state': 'partial'}, failure=None)
    original_wait = activity_gate.wait_for_foreground
    def observe_admission():
        assert activity_gate.in_background, 'Foreground diagnostic tried to wait on its own lease'
        boundary.reached.set()
        original_wait()
    monkeypatch.setattr(activity_gate, 'wait_for_foreground', observe_admission)
    def execute(statement, parameters):
        boundary.sql_started.set()
        boundary.reached.set()
        assert parameters == ('http-attempt',)
        if boundary.failure:
            raise boundary.failure
        if 'opening_evidence_attempts' in statement:
            return SimpleNamespace(fetchone=lambda: boundary.attempt)
        assert statement == 'SELECT event_json FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence LIMIT 256'
        return SimpleNamespace(fetchall=lambda: [('{"sequence":1}',), ('{"sequence":2}',)])
    @contextmanager
    def connection(**options):
        boundary.sections.append(options)
        boundary.active_connections += 1
        try:
            yield SimpleNamespace(execute_native=execute)
        finally:
            boundary.active_connections -= 1
    monkeypatch.setattr(main.postgres_store, 'connection', connection)
    boundary.client = TestClient(main.app)
    return boundary


@pytest.mark.parametrize('outcome', ['found', 'missing', 'error'])
def test_background_opening_attempt_http_read_waits_for_foreground_admission(attempt_http_boundary, outcome):
    from concurrent.futures import ThreadPoolExecutor
    from app.services.activity_gate import activity_gate
    boundary = attempt_http_boundary
    if outcome == 'missing':
        boundary.attempt = None
    elif outcome == 'error':
        boundary.failure = RuntimeError('evidence read failed')
    with ThreadPoolExecutor(max_workers=1) as requests:
        with activity_gate.foreground():
            response_future = requests.submit(boundary.client.get, '/api/opening-evidence/attempts/http-attempt',
                                              headers={'X-Tempo-Work-Class': 'background'})
            assert boundary.reached.wait(5), 'Background route reached neither admission nor evidence SQL'
            assert not boundary.sql_started.is_set(), 'Background attempt evidence SQL started before foreground admission released'
            assert not boundary.sections, 'Background attempt opened PostgreSQL before admission'
        if outcome == 'error':
            with pytest.raises(RuntimeError, match='evidence read failed'):
                response_future.result(timeout=5)
        else:
            response = response_future.result(timeout=5)
            assert response.status_code == (200 if outcome == 'found' else 404)
            if outcome == 'found':
                assert response.json() == {**boundary.attempt, 'events': [{'sequence': 1}, {'sequence': 2}]}
            else:
                assert response.json() == {'detail': 'Opening attempt evidence not found'}
    assert boundary.sections == [{'read_only': True, 'background': True, 'authoritative': True}]
    assert boundary.active_connections == activity_gate.active_background_sections == 0
    assert not activity_gate.foreground_requests_active


def test_foreground_opening_attempt_http_read_does_not_self_deadlock(attempt_http_boundary):
    boundary = attempt_http_boundary
    response = boundary.client.get('/api/opening-evidence/attempts/http-attempt')
    assert response.status_code == 200
    assert response.json() == {**boundary.attempt, 'events': [{'sequence': 1}, {'sequence': 2}]}
    assert boundary.sections == [{'read_only': True}]
    assert boundary.active_connections == 0


@pytest.mark.parametrize('background_header', [True, False])
def test_opening_evidence_http_unavailable_rejects_without_database_or_dispatch(monkeypatch, checkpoint_http_envelope, background_header):
    from fastapi.testclient import TestClient
    from app import main, command_dispatch
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: False)
    monkeypatch.setattr(main.postgres_store, 'connection', lambda **options: pytest.fail('Unavailable evidence opened PostgreSQL'))
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda *args, **options: pytest.fail('Unavailable evidence reached the broker'))
    client = TestClient(main.app)
    headers = {'X-Tempo-Work-Class': 'background'} if background_header else {}
    rejected = client.post('/api/opening-evidence/checkpoints', json=checkpoint_http_envelope, headers=headers)
    assert rejected.status_code == 409
    assert rejected.json() == {'detail': {'code': 'opening_evidence_unavailable',
        'message': 'Opening shadow evidence requires PostgreSQL', 'aggregate_review_allowed': True}}
    unavailable = client.get('/api/opening-evidence/attempts/http-attempt', headers=headers)
    assert unavailable.status_code == 503
    assert unavailable.json() == {'detail': 'Opening shadow evidence requires PostgreSQL'}


def test_opening_checkpoint_http_schema_rejection_precedes_dispatch(monkeypatch, checkpoint_http_envelope):
    from fastapi.testclient import TestClient
    from app import main, command_dispatch
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(main.postgres_store, 'connection', lambda **options: pytest.fail('Invalid schema opened PostgreSQL'))
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda *args, **options: pytest.fail('Invalid schema reached the broker'))
    response = TestClient(main.app).post('/api/opening-evidence/checkpoints', json={**checkpoint_http_envelope, 'events': 'invalid'})
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['body', 'events']


def test_opening_checkpoint_payload_identity_ignores_historical_preparation(checkpoint_http_envelope):
    from app.command_gateway import request_digest
    payload = {'checkpoint': checkpoint_http_envelope}
    historical_payload = {**payload, 'prepared_manifest': {'obsolete': True}}
    digest = request_digest('opening_evidence.checkpoint', payload)
    assert request_digest('opening_evidence.checkpoint', historical_payload) == digest
    changed_payload = {'checkpoint': {**checkpoint_http_envelope, 'study_timezone': 'America/New_York'}}
    assert request_digest('opening_evidence.checkpoint', changed_payload) != digest


@pytest.mark.parametrize("reconciling", [False, True])
def test_foreground_review_http_preparation_keeps_foreground_request_lease(monkeypatch, checkpoint_http_envelope, reconciling):
    from fastapi.testclient import TestClient
    from app import main, command_dispatch
    from app.services.activity_gate import activity_gate
    manifest = checkpoint_http_envelope['manifest']
    completion = {**checkpoint_http_envelope, 'queue_entry_id': 101,
                  'terminal': {'state': 'complete', 'final_sequence': 0, 'ended_at': '2026-09-30T12:01:00Z'}}
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(activity_gate, 'wait_for_foreground', lambda: pytest.fail('Foreground review waited on its own lease'))
    @contextmanager
    def source_read(**options):
        assert options == {'read_only': True}
        assert not activity_gate.in_background and activity_gate.foreground_requests_active
        snapshot = {'id': 1, 'card_id': 'shadow-card', 'revision': 3, 'start_fen': manifest['decisions'][0]['fen'],
                    'moves_json': '["e2e4","e7e5","g1f3","b8c6","f1b5"]', 'trained_color': 'white', 'effective_trained_color': 'white'}
        yield SimpleNamespace(execute_native=lambda *arguments: SimpleNamespace(fetchone=lambda: snapshot))
    monkeypatch.setattr(main.postgres_store, 'connection', source_read)
    submitted = []
    def dispatch(name, payload, **options):
        assert not activity_gate.in_background and activity_gate.foreground_requests_active
        submitted.append((name, payload, options))
        return {'persisted': True}
    monkeypatch.setattr(command_dispatch, 'dispatch_command', dispatch)
    response = TestClient(main.app).post('/api/cards/shadow-card/review' + ('/reconcile' if reconciling else ''), json={'outcome': 'correct',
        'attempt_id': completion['attempt_id'], 'queue_entry_id': 101, 'opening_evidence_completion': completion})
    assert response.status_code == 200
    assert submitted[0][0] == ('cards.review.reconcile' if reconciling else 'cards.review') and submitted[0][1]['prepared_manifest'] == manifest
    assert submitted[0][2].get('background', False) is False
    assert not activity_gate.foreground_requests_active


def test_opening_attempt_http_closes_read_before_event_decoding(attempt_http_boundary, monkeypatch):
    boundary = attempt_http_boundary
    original_decode = json.loads
    decoded = []
    def decode(value, *arguments, **options):
        if value in ('{"sequence":1}', '{"sequence":2}'):
            assert boundary.active_connections == 0, 'HTTP response decoding held the attempt read open'
            decoded.append(value)
        return original_decode(value, *arguments, **options)
    monkeypatch.setattr(json, 'loads', decode)
    assert boundary.client.get('/api/opening-evidence/attempts/http-attempt', headers={'X-Tempo-Work-Class': 'background'}).status_code == 200
    assert decoded == ['{"sequence":1}', '{"sequence":2}']


def test_opening_checkpoint_dispatches_as_background_without_changing_review_dispatch(monkeypatch):
    from app import main, command_dispatch, opening_evidence_api
    from app.models import ReviewRequest
    from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
    fixture = json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/opening-evidence-manifest.json').read_text())
    request = OpeningEvidenceCheckpoint(attempt_id='background-checkpoint', manifest=fixture,
        origin_queue_entry_id=101, started_at='2026-09-30T12:00:00Z', study_timezone='UTC')
    dispatched = []
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(command_dispatch, 'dispatch_command',
        lambda name, payload, **options: dispatched.append((name, payload, options)) or {'persisted': True})
    for _ in range(2):
        opening_evidence_api.opening_evidence_checkpoint(request, idempotency_key='checkpoint-key')
    main.review('card', ReviewRequest(outcome='correct', queue_entry_id=101), idempotency_key='review-key')
    assert dispatched[0] == dispatched[1]
    assert dispatched[0] == ('opening_evidence.checkpoint',
        {'checkpoint': request.model_dump(mode='json')},
        {'idempotency_key': 'checkpoint-key', 'background': True})
    assert dispatched[2][0] == 'cards.review'
    assert dispatched[2][2].get('background', False) is False


def test_opening_checkpoint_request_is_background_without_a_client_work_class_header(monkeypatch):
    from contextlib import contextmanager
    from fastapi.testclient import TestClient
    from app import main, command_dispatch, opening_evidence_api
    fixture = json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/opening-evidence-manifest.json').read_text())
    request = {'attempt_id': 'background-http', 'manifest': fixture, 'origin_queue_entry_id': 101,
               'started_at': '2026-09-30T12:00:00Z', 'study_timezone': 'UTC'}
    observed_scopes = []
    @contextmanager
    def scope(work_class):
        observed_scopes.append(work_class)
        yield
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(main.activity_gate, 'foreground', lambda: scope('foreground'))
    monkeypatch.setattr(main.activity_gate, 'background_request', lambda: scope('background'))
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda *args, **kwargs: {'persisted': True})
    client = TestClient(main.app)
    assert client.post('/api/opening-evidence/checkpoints', json=request).status_code == 200
    assert observed_scopes == ['background']
    observed_scopes.clear()
    assert client.post('/api/cards/card/review', json={'outcome': 'correct', 'queue_entry_id': 101}).status_code == 200
    assert observed_scopes == ['foreground']


def test_shadow_manifest_fixture_matches_backend_producer_and_prescribed_revision():
    from app.services.opening_decision_evidence import decision_manifest
    fixture = json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/opening-evidence-manifest.json').read_text())
    snapshot = {'id':1,'card_id':'shadow-card','revision':3,'start_fen':fixture['decisions'][0]['fen'],
                'moves_json':'["e2e4","e7e5","g1f3","b8c6","f1b5"]','trained_color':'white'}
    assert decision_manifest(snapshot,'shadow-repertoire') == fixture
    changed={**snapshot,'revision':4}
    assert decision_manifest(changed,'shadow-repertoire')['manifest_id'] != fixture['manifest_id']


def test_legacy_review_dispatch_preserves_exact_payload_and_command_fingerprint(monkeypatch):
    from app import main, command_dispatch
    from app.models import ReviewRequest
    from app.command_gateway import request_digest
    requests=[]
    monkeypatch.setattr(main.postgres_store,'configured',lambda:True)
    monkeypatch.setattr(command_dispatch,'dispatch_command',lambda name,payload,**options:requests.append((name,payload)) or {'persisted':True})
    request=ReviewRequest(outcome='correct',guided=False,queue_entry_id=101,attempt_id='legacy-id')
    main.review('card',request,idempotency_key='legacy-key')
    expected={'card_id':'card','review':request.model_dump(mode='json',exclude={'opening_evidence_completion'})}
    assert requests==[('cards.review',expected)]
    digest=hashlib.sha256(json.dumps(['cards.review',expected],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert request_digest('cards.review',expected)==digest
    assert request_digest('cards.review',{**expected,'prepared_manifest':{'derived':True}})==digest


def test_shadow_chess_validation_closes_read_connection_before_traversal(monkeypatch):
    from app.services import postgres_opening_evidence as service
    from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
    fixture=json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/opening-evidence-manifest.json').read_text())
    request=OpeningEvidenceCheckpoint(attempt_id='attempt',manifest=fixture,origin_queue_entry_id=101,
        started_at='2026-09-30T12:00:00Z',study_timezone='UTC')
    active=False
    @contextmanager
    def read(**options):
        nonlocal active
        active=True
        yield SimpleNamespace(execute_native=lambda *args:SimpleNamespace(fetchone=lambda:{'trained_color':'white','effective_trained_color':'white'}))
        active=False
    def derive(*args):
        assert not active,'Chess traversal held its database read connection'
        return fixture
    monkeypatch.setattr(service.postgres_store,'configured',lambda:True)
    monkeypatch.setattr(service.postgres_store,'connection',read)
    monkeypatch.setattr(service,'decision_manifest',derive)
    assert service.prepare_checkpoint(request)==fixture


def test_evidence_migration_is_unique_additive_and_matches_readiness():
    from app.schema_version import POSTGRES_SCHEMA_VERSION
    directory=Path(__file__).resolve().parents[1]/'migrations'
    migrations=sorted(directory.glob('[0-9][0-9][0-9]_*.sql'))
    assert [int(path.name[:3]) for path in migrations]==list(range(1,POSTGRES_SCHEMA_VERSION+1))
    migration=next(path for path in migrations if path.name.endswith('_opening_decision_evidence.sql'))
    source=migration.read_text()
    assert 'INSERT INTO tempo_schema_migrations(version) VALUES(30)' in source
    assert 'UPDATE cards SET' not in source and 'UPDATE daily_queue SET' not in source
    assert 'REFERENCES cards' not in source and 'REFERENCES repertoires' not in source


def test_queue_evidence_is_explicitly_negotiated_for_live_window_and_prepared_clients(monkeypatch):
    from app import main
    calls=[]
    def payload(limit=None, *, include_opening_evidence=False):
        calls.append((limit,include_opening_evidence))
        return {'cards':[], 'count':0, **({'opening_evidence_study_timezone':'UTC'} if include_opening_evidence else {})}
    monkeypatch.setattr(main,'_queue_payload',payload)
    for route in (main.queue_today,main.queue_window,main.prepared_queue):
        legacy=route()
        assert 'opening_evidence_study_timezone' not in legacy
        enabled=route(include_opening_evidence=True)
        assert enabled['opening_evidence_study_timezone']=='UTC'
    assert calls==[(None,False),(None,True),(20,False),(20,True),(None,False),(None,True)]


def test_sqlite_reports_unsupported_evidence_without_claiming_shadow_persistence(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app import main
    from app.models import ReviewRequest
    fixture=json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/opening-evidence-manifest.json').read_text())
    monkeypatch.setattr(main.postgres_store,'configured',lambda:False)
    monkeypatch.setattr(main,'_apply_review',lambda *args,**kwargs:{'persisted':True})
    assert main.review('shadow-card',ReviewRequest(outcome='correct'))=={'persisted':True}
    request=ReviewRequest(outcome='correct',attempt_id='shadow-attempt',queue_entry_id=101,
      opening_evidence_completion={'attempt_id':'shadow-attempt','manifest':fixture,'origin_queue_entry_id':101,
        'queue_entry_id':101,'started_at':'2026-09-30T12:00:00Z','study_timezone':'UTC',
        'terminal':{'state':'complete','final_sequence':0,'ended_at':'2026-09-30T12:01:00Z'}})
    with pytest.raises(HTTPException) as rejected:main.review('shadow-card',request)
    assert rejected.value.detail['code']=='opening_evidence_unavailable'
    assert rejected.value.detail['aggregate_review_allowed'] is True


def test_review_completion_requires_a_resolved_actual_queue_binding(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app import review_commands
    fixture=json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/opening-evidence-manifest.json').read_text())
    monkeypatch.setattr(review_commands,'lock_queue_date_for_position',lambda *args:None)
    completion={'attempt_id':'unbound-repeat','manifest':fixture,'origin_queue_entry_id':101,
      'queue_entry_id':None,'started_at':'2026-09-30T12:00:00Z','study_timezone':'UTC',
      'terminal':{'state':'complete','final_sequence':0,'ended_at':'2026-09-30T12:01:00Z'}}
    with pytest.raises(HTTPException) as rejected:
        review_commands.submit_review(SimpleNamespace(execute=lambda *args:SimpleNamespace(fetchone=lambda:None)),
          {'card_id':'shadow-card','review':{'outcome':'correct','attempt_id':'unbound-repeat',
            'opening_evidence_completion':completion}})
    assert rejected.value.detail['code']=='opening_evidence_conflict'
    assert 'queue entry' in rejected.value.detail['message']


def test_scoped_color_adapter_preserves_raw_legacy_presentation():
    from app.services.postgres_opening_evidence import _manifest_snapshot
    snapshot = {'trained_color':None, 'effective_trained_color':'black', 'id':17}
    assert _manifest_snapshot(snapshot) == {**snapshot, 'trained_color':'black'}
    assert snapshot['trained_color'] is None


def test_scoped_color_adapter_rejects_unknown_and_explicit_mismatch():
    import pytest
    from app.services.postgres_opening_evidence import _manifest_snapshot
    for raw_color, effective_color in [(None,None),(None,'unknown'),('white','black'),('unknown','white')]:
        with pytest.raises(ValueError):
            _manifest_snapshot({'trained_color':raw_color, 'effective_trained_color':effective_color})


def test_evidence_migration_captures_valid_scope_color_without_overwriting_context():
    source = (Path(__file__).resolve().parents[1]/'migrations/030_opening_decision_evidence.sql').read_text()
    assert "effective_trained_color TEXT NOT NULL CHECK(effective_trained_color IN ('white','black'))" in source
    assert 'WHERE line.repertoire_id=scope.repertoire_id ORDER BY line.created_at,line.id LIMIT 1' in source
    binding = source.split('CREATE FUNCTION opening_evidence_bind_queue',1)[1].split('CREATE FUNCTION opening_evidence_capture_queue_context',1)[0]
    assert 'COALESCE(card.trained_color,' in binding
    assert "learner.effective_trained_color IN ('white','black')" in binding
    assert 'ON CONFLICT DO NOTHING' in binding
    assert 'DO UPDATE' not in binding


@pytest.mark.parametrize(('has_receipt', 'idempotent', 'requeue_id', 'expected_corrections'), [
    (False, False, 202, 1),
    (True, False, 202, 0),  # Receipt replay retains the original idempotent=False result.
    (False, True, 202, 0),
    (False, False, None, 0),
    (False, None, 202, 0),
])
@pytest.mark.parametrize("reconciling", [False, True])
def test_review_requeue_context_correction_only_runs_for_fresh_review(
        monkeypatch, has_receipt, idempotent, requeue_id, expected_corrections, reconciling):
    from app import main, review_commands
    from app.services import postgres_opening_evidence
    manifest = json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/opening-evidence-manifest.json').read_text())
    completion = {'attempt_id':'context-authority', 'manifest':manifest, 'origin_queue_entry_id':101,
                  'queue_entry_id':101, 'started_at':'2026-09-30T12:00:00Z', 'study_timezone':'UTC',
                  'terminal':{'state':'complete', 'final_sequence':0, 'ended_at':'2026-09-30T12:01:00Z'}}
    corrections = []
    validation_order = []
    result = {'persisted':True, 'requeue_entry_id':requeue_id}
    if idempotent is not None:
        result['idempotent'] = idempotent

    def execute(statement, parameters):
        if 'FROM review_attempt_receipts' in statement:
            row = (1,) if has_receipt else None
        elif 'SELECT revision' in statement:
            row = (manifest['card_revision'],)
        elif 'SELECT status' in statement:
            row = ('queued',)
        else:
            row = None
        return SimpleNamespace(fetchone=lambda:row)

    def execute_native(statement, parameters):
        if 'INSERT INTO opening_evidence_queue_contexts' in statement:
            assert validation_order == ['validated', 'reviewed', 'completed']
            corrections.append(parameters)
        return SimpleNamespace(fetchone=lambda:None if 'FROM deleted_cards' in statement else ('complete',))

    database = SimpleNamespace(execute=execute, execute_native=execute_native, raw=SimpleNamespace(execute=lambda *args:None))
    def apply_review(*arguments, **options):
        assert options['database'] is database and validation_order == ['validated']
        validation_order.append('reviewed')
        return result
    monkeypatch.setattr(review_commands, 'lock_queue_date_for_position', lambda *arguments:None)
    monkeypatch.setattr(main, '_apply_review', apply_review)
    def persist_completion(connection, payload, **options):
        assert connection is database and options == {'completing_review':True}
        validation_order.append('validated')
    def complete_evidence(connection, *arguments):
        assert connection is database
        validation_order.append('completed')
    monkeypatch.setattr(postgres_opening_evidence, 'persist_checkpoint', persist_completion)
    monkeypatch.setattr(postgres_opening_evidence, 'complete_review_evidence', complete_evidence)
    handler = review_commands.reconcile_review if reconciling else review_commands.submit_review
    assert handler(database, {'card_id':manifest['card_id'],
        'review':{'outcome':'correct', 'attempt_id':completion['attempt_id'], 'queue_entry_id':101,
                  'opening_evidence_completion':completion}, 'prepared_manifest':manifest}) == result
    assert len(corrections) == expected_corrections
    if corrections:
        assert corrections == [(requeue_id, manifest['presentation_snapshot_id'],
                                manifest['repertoire_id'], manifest['trained_color'])]


@pytest.mark.parametrize('background', [True, False])
def test_opening_checkpoint_receipt_read_uses_background_admission(monkeypatch, background):
    from fastapi.testclient import TestClient
    from app import main
    observed_scopes = []
    reads = []
    @contextmanager
    def scope(work_class):
        observed_scopes.append(work_class)
        yield
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(main.activity_gate, 'foreground', lambda: scope('foreground'))
    monkeypatch.setattr(main.activity_gate, 'background_request', lambda: scope('background'))
    def read(operation_id, **options):
        reads.append((operation_id, options))
        assert observed_scopes == ['background' if background else 'foreground']
        return {'state': 'complete', 'response': {'persisted': True}}
    monkeypatch.setattr(main, 'read_operation', read)
    headers = {'X-Tempo-Work-Class': 'background'} if background else {}
    response = TestClient(main.app).get('/api/operations/opening-checkpoint:receipt', headers=headers)
    assert response.status_code == 200
    assert response.json() == {'state': 'complete', 'response': {'persisted': True}}
    assert reads == [('opening-checkpoint:receipt', {'background': background})]


def test_reconciliation_conflict_rolls_back_new_evidence_and_preserves_saved_checkpoint(monkeypatch):
    from app import main, review_commands
    from app.review_conflicts import ReviewConflict
    from app.services import postgres_opening_evidence as evidence
    manifest = json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/opening-evidence-manifest.json').read_text())
    completion = {'attempt_id':'atomic-reconcile', 'manifest':manifest, 'origin_queue_entry_id':101,
        'queue_entry_id':101, 'started_at':'2026-09-30T12:00:00Z', 'study_timezone':'UTC',
        'terminal':{'state':'complete', 'final_sequence':0, 'ended_at':'2026-09-30T12:01:00Z'}}
    writes = ['saved-checkpoint']
    checkpoints = []
    operations = []
    def transaction(statement, *arguments):
        operations.append(statement)
        if statement.startswith('SAVEPOINT'):
            checkpoints[:] = writes
        elif statement.startswith('ROLLBACK TO'):
            writes[:] = checkpoints
    def execute(statement, parameters):
        row = (manifest['card_revision'],) if 'SELECT revision' in statement else ('queued',) if 'SELECT status' in statement else None
        return SimpleNamespace(fetchone=lambda:row)
    database = SimpleNamespace(execute=execute, execute_native=execute, raw=SimpleNamespace(execute=transaction))
    monkeypatch.setattr(review_commands, 'lock_queue_date_for_position', lambda *args:None)
    def persist(*args, **options):
        writes.append('new-completion-events')
    def conflict(*args, **options):
        assert writes == ['saved-checkpoint', 'new-completion-events'], 'Reconciliation bypassed evidence validation/persistence'
        raise ReviewConflict('content_changed', 'Saved content changed')
    monkeypatch.setattr(evidence, 'persist_checkpoint', persist)
    monkeypatch.setattr(evidence, 'complete_review_evidence', lambda *args:pytest.fail('Conflicted review completed evidence'))
    monkeypatch.setattr(main, '_apply_review', conflict)
    result = review_commands.reconcile_review(database, {'card_id':manifest['card_id'],
        'review':{'outcome':'correct','attempt_id':completion['attempt_id'],'queue_entry_id':101,
                  'opening_evidence_completion':completion},'prepared_manifest':manifest})
    assert result == {'persisted':False,'conflict':{'code':'content_changed','message':'Saved content changed','retryable':False}}
    assert writes == ['saved-checkpoint']
    assert any(statement.startswith('ROLLBACK TO SAVEPOINT') for statement in operations)


def test_reconciliation_digest_ignores_only_derived_preparation():
    from app.command_gateway import request_digest
    payload = {'card_id':'shadow-card','review':{'attempt_id':'same-result','outcome':'correct'}}
    digest = request_digest('cards.review.reconcile', payload)
    assert request_digest('cards.review.reconcile', {**payload,'prepared_manifest':{'derived':True}}) == digest
    assert request_digest('cards.review.reconcile', {**payload,'review':{**payload['review'],'outcome':'again'}}) != digest
