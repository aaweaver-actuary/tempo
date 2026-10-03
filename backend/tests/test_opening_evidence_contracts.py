"""Compatibility and authoritative preparation seams in the regular suite."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_opening_checkpoint_dispatches_as_background_without_changing_review_dispatch(monkeypatch):
    from app import main, command_dispatch, opening_evidence_api
    from app.models import ReviewRequest
    from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
    fixture = json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/opening-evidence-manifest.json').read_text())
    request = OpeningEvidenceCheckpoint(attempt_id='background-checkpoint', manifest=fixture,
        origin_queue_entry_id=101, started_at='2026-09-30T12:00:00Z', study_timezone='UTC')
    dispatched = []
    monkeypatch.setattr(opening_evidence_api, 'prepare_checkpoint', lambda request: fixture)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(command_dispatch, 'dispatch_command',
        lambda name, payload, **options: dispatched.append((name, payload, options)) or {'persisted': True})
    for _ in range(2):
        opening_evidence_api.opening_evidence_checkpoint(request, idempotency_key='checkpoint-key')
    main.review('card', ReviewRequest(outcome='correct', queue_entry_id=101), idempotency_key='review-key')
    assert dispatched[0] == dispatched[1]
    assert dispatched[0] == ('opening_evidence.checkpoint',
        {'checkpoint': request.model_dump(mode='json'), 'prepared_manifest': fixture},
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
    monkeypatch.setattr(opening_evidence_api, 'prepare_checkpoint', lambda request: fixture)
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
        review_commands.submit_review(SimpleNamespace(execute=lambda *args:None),
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
def test_review_requeue_context_correction_only_runs_for_fresh_review(
        monkeypatch, has_receipt, idempotent, requeue_id, expected_corrections):
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
        return SimpleNamespace(fetchone=lambda:('complete',))

    database = SimpleNamespace(execute=execute, execute_native=execute_native)
    def apply_review(*arguments, **options):
        assert options['database'] is database and validation_order == ['validated']
        validation_order.append('reviewed')
        return result
    monkeypatch.setattr(review_commands, 'lock_queue_date_for_position', lambda *arguments:None)
    monkeypatch.setattr(main, '_apply_review', apply_review)
    monkeypatch.setattr(postgres_opening_evidence, 'persist_checkpoint',
                        lambda *arguments, **options:validation_order.append('validated'))
    monkeypatch.setattr(postgres_opening_evidence, 'complete_review_evidence',
                        lambda *arguments:validation_order.append('completed'))
    assert review_commands.submit_review(database, {'card_id':manifest['card_id'],
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
