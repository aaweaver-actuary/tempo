"""Standalone shadow CPU work must not own a background database section."""
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
import copy

import chess
import pytest
from psycopg.errors import SerializationFailure

from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
from app.services import postgres_opening_evidence as evidence
from app.services.opening_decision_evidence import EvidenceConflict, decision_manifest


@pytest.mark.parametrize('historical_preparation', [None, 'valid', 'obsolete'])
def test_standalone_opening_checkpoint_reduces_outside_background_transaction(monkeypatch, historical_preparation):
    from app import command_gateway, database, tasks
    from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
    from app.services import postgres_opening_evidence as evidence
    manifest = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/opening-evidence-manifest.json").read_text())
    request = OpeningEvidenceCheckpoint(attempt_id="outside-transaction", manifest=manifest,
        origin_queue_entry_id=101, queue_entry_id=101, started_at="2026-09-30T12:00:00Z", study_timezone="UTC")
    payload = {"checkpoint": request.model_dump(mode="json")}
    if historical_preparation is not None:
        payload['prepared_manifest'] = manifest if historical_preparation == 'valid' else {'obsolete': True}
    attempt = {"context_json": evidence.canonical_json(request.model_dump(mode="json", exclude={"events", "terminal", "queue_entry_id"})),
               "queue_entry_id":101, "state":"active", "terminal_json":None, "contiguous_sequence":0}
    active_sections = 0
    observed_sections = []
    read_options = []

    def execute(statement, parameters=()):
        row = None
        if statement.startswith("SELECT command_name,request_hash,state,"):
            row = ("opening_evidence.checkpoint", command_gateway.request_digest("opening_evidence.checkpoint", payload), "executing", None, None, "owned-token")
        elif statement.startswith("SELECT revision,archived FROM cards"):
            row = {"revision": request.manifest.card_revision, "archived": 0}
        elif statement.startswith("SELECT retired_operation_id FROM opening_evidence_attempts"):
            row = (None,)
        elif statement.startswith("SELECT * FROM opening_evidence_attempts"):
            row = attempt
        elif statement.startswith("SELECT 1 FROM opening_evidence_queue_contexts"):
            row = (1,)
        elif statement.startswith("SELECT snapshot.*"):
            row = {"trained_color":"white", "effective_trained_color":"white"}
        return SimpleNamespace(fetchone=lambda:row, fetchall=lambda:[])

    connection = SimpleNamespace(raw=SimpleNamespace(execute=execute), execute_native=execute)
    @contextmanager
    def section(*args, **options):
        nonlocal active_sections
        active_sections += 1
        try:
            yield connection
        finally:
            active_sections -= 1

    original_reduce = evidence.reduce_observations
    def instrumented_reduce(events, study_timezone):
        observed_sections.append(active_sections)
        return original_reduce(events, study_timezone)

    monkeypatch.setattr(command_gateway, "_writer_connection", section)
    @contextmanager
    def authoritative_read(**options):
        read_options.append(options)
        with section() as source:
            yield source
    monkeypatch.setattr(database, "background_read_connection", authoritative_read)
    monkeypatch.setattr(evidence, "reduce_observations", instrumented_reduce)
    def manifest_after_read(*args):
        assert active_sections == 0, "Chess validation held the source read open"
        return manifest
    monkeypatch.setattr(evidence, "decision_manifest", manifest_after_read)
    monkeypatch.setattr(tasks, "record_operation_attempt", lambda *args, **options:(True, payload, "owned-token", 1))
    result = tasks.execute_background_command.run("checkpoint-operation", "opening_evidence.checkpoint", payload)
    assert result == {"persisted":True, "attempt_id":request.attempt_id, "contiguous_sequence":0, "received_sequences":[], "state":"active"}
    assert observed_sections == [0], "Standalone reduction held the command gateway's database transaction"
    assert active_sections == 0
    assert read_options == [{'authoritative': True}]
    assert "cards.review" not in command_gateway._preparers, "Foreground review must retain its atomic handler"


def large_request():
    manifest = decision_manifest({'id':1, 'card_id':'large-card', 'revision':1, 'start_fen':chess.STARTING_FEN,
        'moves_json':json.dumps((['g1f3','g8f6','f3g1','f6g8']*10)[:-1]), 'trained_color':'white'}, 'large-repertoire')
    events = []
    for index, decision in enumerate(manifest['decisions']):
        common = {'decision_index':index, 'decision_id':decision['decision_id'], 'expected_uci':decision['expected_uci'],
                  'observed_at':'2026-09-30T12:00:00Z'}
        events.append({**common, 'sequence':len(events)+1, 'kind':'first_response', 'response_uci':decision['expected_uci']})
        for _ in range(12 if index < 16 else 11):
            events.append({**common, 'sequence':len(events)+1, 'kind':'assistance', 'assistance':'hint'})
    return OpeningEvidenceCheckpoint(attempt_id='large-attempt', manifest=manifest, origin_queue_entry_id=101,
        queue_entry_id=101, started_at='2026-09-30T12:00:00Z', study_timezone='America/New_York', events=events)


def source_header(request, **changes):
    return {'context_json':evidence.canonical_json(request.model_dump(mode='json', exclude={'events','terminal','queue_entry_id'})),
            'queue_entry_id':101, 'terminal_json':None, 'contiguous_sequence':0, 'state':'active', **changes}


@pytest.mark.parametrize('historical_manifest', [None, {'obsolete': True}])
def test_standalone_checkpoint_preparation_uses_immutable_source_instead_of_historical_manifest(monkeypatch, historical_manifest):
    from app import database
    from fastapi import HTTPException
    manifest = json.loads((Path(__file__).resolve().parents[2] / 'tests/fixtures/opening-evidence-manifest.json').read_text())
    request = OpeningEvidenceCheckpoint(attempt_id='historical-payload', manifest=manifest,
        origin_queue_entry_id=101, started_at='2026-09-30T12:00:00Z', study_timezone='UTC')
    snapshot = {'id': 1, 'card_id': 'shadow-card', 'revision': 3, 'start_fen': manifest['decisions'][0]['fen'],
                'moves_json': '["e2e4","e7e5","g1f3","b8c6","f1b5"]', 'trained_color': 'white',
                'effective_trained_color': 'white'}
    active = False
    def execute(statement, parameters):
        assert active
        if statement.startswith('SELECT snapshot.*'):
            assert parameters == (1, 101, 'shadow-repertoire')
            return SimpleNamespace(fetchone=lambda: snapshot)
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])
    @contextmanager
    def read(**options):
        nonlocal active
        assert options == {'authoritative': True}
        active = True
        try:
            yield SimpleNamespace(execute_native=execute)
        finally:
            active = False
    monkeypatch.setattr(database, 'background_read_connection', read)
    original_manifest = evidence.decision_manifest
    def derive_after_read(*arguments):
        assert not active, 'Manifest authority was derived while the source read remained open'
        return original_manifest(*arguments)
    monkeypatch.setattr(evidence, 'decision_manifest', derive_after_read)
    payload = {'checkpoint': request.model_dump(mode='json')}
    if historical_manifest is not None:
        payload['prepared_manifest'] = historical_manifest
    before = copy.deepcopy(payload)
    prepared = evidence.prepare_standalone_checkpoint(payload)
    assert prepared.request == request and payload == before and not active
    changed = copy.deepcopy(payload)
    changed['checkpoint']['manifest']['manifest_id'] = '0' * 64
    with pytest.raises(HTTPException) as rejected:
        evidence.prepare_standalone_checkpoint(changed)
    assert rejected.value.status_code == 409
    assert rejected.value.detail['code'] == 'opening_evidence_conflict'


def test_standalone_checkpoint_prepares_maximum_events_and_decisions_without_mutating_source():
    request = large_request()
    assert len(request.events) == 256 and len(request.manifest.decisions) == 20
    source_events = ((1, evidence.canonical_json(request.events[0].model_dump(mode='json'))),)
    source = source_header(request, contiguous_sequence=1)
    before = copy.deepcopy((request, source, source_events))
    prepared = evidence._compute_checkpoint_publication(request, source, source_events)
    assert (request, source, source_events) == before
    assert len(prepared.missing_events) == 255 and len(prepared.observations) == 20
    assert prepared.result['contiguous_sequence'] == 256 and all(item['clean'] for item in prepared.observations)
    assert len({item['decision_id'] for item in prepared.observations}) < 20, 'Fixture must exercise transposition aggregation'
    assert all(item['study_day'] == '2026-09-30' for item in prepared.observations)


def test_standalone_checkpoint_preparation_preserves_terminal_gaps_and_completed_replay():
    request = large_request()
    terminal = {'state':'partial', 'final_sequence':256, 'ended_at':'2026-09-30T12:01:00Z'}
    gap = request.model_copy(update={'events':request.events[1:], 'terminal':None})
    prepared = evidence._compute_checkpoint_publication(gap, source_header(request, terminal_json=evidence.canonical_json(terminal)), ())
    assert not prepared.observations and prepared.result['contiguous_sequence'] == 0 and prepared.result['state'] == 'active'
    complete = {**terminal, 'state':'complete'}
    events = tuple((event.sequence, evidence.canonical_json(event.model_dump(mode='json'))) for event in request.events)
    replay = evidence._compute_checkpoint_publication(request, source_header(request, state='complete', contiguous_sequence=256,
        terminal_json=evidence.canonical_json(complete)), events)
    assert not replay.missing_events and replay.result['state'] == 'complete' and replay.terminal_json == evidence.canonical_json(complete)


@pytest.mark.parametrize('conflict', ['context','event','seal','terminal','complete'])
def test_standalone_checkpoint_preparation_retains_immutable_conflicts(conflict):
    request = large_request()
    source = source_header(request)
    events = ()
    if conflict == 'context':
        source['context_json'] = '{}'
    elif conflict == 'event':
        changed = request.events[0].model_dump(mode='json'); changed['response_uci'] = 'd2d4'
        events = ((1, evidence.canonical_json(changed)),)
    elif conflict == 'seal':
        source['terminal_json'] = evidence.canonical_json({'state':'partial', 'final_sequence':1, 'ended_at':'2026-09-30T12:01:00Z'})
    else:
        raw = request.model_dump(mode='json')
        raw['terminal'] = {'state':'complete' if conflict=='complete' else 'partial', 'final_sequence':256, 'ended_at':'2026-09-30T12:01:00Z'}
        request = OpeningEvidenceCheckpoint.model_validate(raw)
        if conflict == 'terminal':
            source['terminal_json'] = evidence.canonical_json({**raw['terminal'], 'ended_at':'2026-09-30T12:02:00Z'})
    with pytest.raises(EvidenceConflict):
        evidence._compute_checkpoint_publication(request, source, events)


@pytest.mark.parametrize('changed', ['header','events','new_attempt'])
def test_standalone_checkpoint_stale_publication_retries_without_projection_writes(monkeypatch, changed):
    request = large_request()
    source = source_header(request) if changed != 'new_attempt' else None
    prepared = evidence._compute_checkpoint_publication(request, source, ())
    current = source_header(request, state='complete') if changed == 'header' else source_header(request)
    current_events = ((1, '{}'),) if changed == 'events' else ()
    writes = []
    connection = SimpleNamespace(execute_native=lambda statement, *args:writes.append(statement) or SimpleNamespace(fetchone=lambda:None))
    monkeypatch.setattr(evidence, '_validate_checkpoint_scope', lambda *args, **options:None)
    monkeypatch.setattr(evidence, '_read_checkpoint_source', lambda *args, **options:(current,current_events))
    monkeypatch.setattr(evidence, '_persist_observations', lambda *args:pytest.fail('Stale publication wrote projections'))
    with pytest.raises(SerializationFailure, match='prepare again'):
        evidence.commit_standalone_checkpoint(connection, prepared)
    assert len(writes) == 1 and writes[0].startswith('INSERT INTO opening_evidence_attempts')
