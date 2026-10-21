"""Opening evidence policy, lifecycle and atomic publication use fixed owner facts."""
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

import chess
from fastapi import HTTPException
from psycopg.errors import SerializationFailure
import pytest

from tempo_learning_runtime import opening_evidence as evidence
from tempo_learning_runtime import card_deletion
from tempo_learning_runtime.opening_evidence_contracts import OpeningEvidenceCheckpoint

MOMENT='2026-10-20T12:00:00+00:00'


def snapshot(**changes):
    return {**dict(id=1,queue_entry_id=4,repertoire_id='rep',card_id='card',revision=2,
        start_fen=chess.STARTING_FEN,moves_json='["e2e4","e7e5","g1f3"]',trained_color='white',effective_trained_color='white'),**changes}


def request(**changes):
    manifest=evidence.decision_manifest(snapshot(),'rep');decision=manifest['decisions'][0]
    return OpeningEvidenceCheckpoint.model_validate({**dict(attempt_id='attempt',manifest=manifest,
        origin_queue_entry_id=4,queue_entry_id=4,started_at=MOMENT,study_timezone='UTC',events=[dict(
            sequence=1,decision_index=0,decision_id=decision['decision_id'],expected_uci='e2e4',
            kind='first_response',observed_at=MOMENT,response_uci='e2e4',disposition='expected')],
        terminal=dict(state='partial',final_sequence=1,ended_at=MOMENT)),**changes})


def test_prepare_closes_actual_read_section_before_manifest_validation(monkeypatch):
    events=[];row=snapshot();value=request()
    database=SimpleNamespace(execute_native=lambda *_: SimpleNamespace(fetchone=lambda: row))
    @contextmanager
    def connection(**options):
        assert options==dict(read_only=True);events.append('read');yield database;events.append('closed')
    monkeypatch.setattr(evidence.storage.postgres_store,'connection',connection)
    def manifest(context,repertoire):
        assert events==['read','closed'] and context['trained_color']=='white'
        events.append('manifest');return value.manifest.model_dump(mode='json')
    monkeypatch.setattr(evidence,'decision_manifest',manifest)
    monkeypatch.setattr(evidence,'validate_checkpoint',lambda *_: events.append('validate'))
    result=evidence.prepare_checkpoint(value)
    assert result['card_id']=='card' and events==['read','closed','manifest','validate']


@pytest.mark.parametrize('changes',[dict(effective_trained_color=None),dict(trained_color='black')])
def test_snapshot_color_conflict_retains_aggregate_review_fallback(changes,monkeypatch):
    monkeypatch.setattr(evidence.storage,'presentation_snapshot',lambda *_: snapshot(**changes))
    with pytest.raises(HTTPException) as error:evidence.prepare_checkpoint(request())
    assert error.value.status_code==409 and error.value.detail['aggregate_review_allowed'] is True


def test_no_opening_cards_avoids_snapshot_read(monkeypatch):
    monkeypatch.setattr(evidence.storage,'queue_snapshots',lambda *_: pytest.fail('No opening card opened storage'))
    cards=[dict(content_type='tactic')];evidence.queue_manifests(cards)
    assert cards==[dict(content_type='tactic')]


def test_queue_binding_failure_retains_original_read_scope_cleanup(monkeypatch):
    events=[]
    @contextmanager
    def connection(**_options):
        events.append('read')
        try:yield SimpleNamespace(execute_native=lambda *_: pytest.fail('Missing identity reached query'))
        finally:events.append('closed')
    monkeypatch.setattr(evidence.storage.postgres_store,'connection',connection)
    with pytest.raises(KeyError):evidence.queue_manifests([dict(content_type='opening')])
    assert events==['read','closed']


@pytest.mark.parametrize('rows',[[snapshot(),snapshot()],[]])
def test_ambiguous_or_absent_manifest_keeps_normal_queue_card_available(monkeypatch,rows):
    monkeypatch.setattr(evidence.storage,'queue_snapshots',lambda *_: rows)
    card=dict(content_type='opening',queue_entry_id=4,id='card',revision=2,start_fen=chess.STARTING_FEN,moves=['e2e4','e7e5','g1f3'])
    evidence.queue_manifests([card])
    assert 'Normal review remains available' in card['opening_evidence_diagnostic']
    assert 'opening_decision_manifest' not in card and card['id']=='card'


@pytest.mark.parametrize('fault',['context','terminal','event'])
def test_pure_preparation_rejects_reused_immutable_identity(fault):
    value=request();prepared=evidence._compute_checkpoint_publication(value,None,())
    source=dict(context_json=prepared.context_json,queue_entry_id=4,terminal_json=prepared.terminal_json,state='active')
    events=prepared.missing_events
    if fault=='context':source['context_json']='different'
    elif fault=='terminal':source['terminal_json']=evidence.canonical_json(dict(state='partial',final_sequence=0,ended_at=MOMENT))
    else:events=((1,'different'),)
    with pytest.raises(evidence.EvidenceConflict):evidence._compute_checkpoint_publication(value,source,events)


@pytest.fixture
def scope(monkeypatch):
    events=[]
    monkeypatch.setattr(evidence.storage,'lock_card_identity',lambda *_: events.append('identity lock'))
    monkeypatch.setattr(card_deletion,'require_card_not_deleted',lambda *_: events.append('deletion gate'))
    monkeypatch.setattr(evidence.storage,'current_card_for_update',lambda *_: events.append('card lock') or dict(archived=0,serving_active=1))
    monkeypatch.setattr(evidence.storage,'completed_aggregate',lambda *_: None)
    monkeypatch.setattr(evidence.storage,'retired_operation',lambda *_: None)
    monkeypatch.setattr(evidence.storage,'presentation_scope',lambda *_: (1,))
    monkeypatch.setattr(evidence.storage,'queue_scope',lambda *_: (1,))
    return events


def test_scope_lock_deletion_order_and_origin_proof_precede_queue_or_parent(monkeypatch,scope):
    monkeypatch.setattr(evidence.storage,'presentation_scope',lambda *_: None)
    monkeypatch.setattr(evidence.storage,'queue_scope',lambda *_: pytest.fail('Missing origin read queue'))
    with pytest.raises(evidence.EvidenceConflict,match='immutable presentation scope'):
        evidence._validate_checkpoint_scope(None,request(),completing_review=False)
    assert scope==['identity lock','deletion gate','card lock']


def test_completed_aggregate_replay_retains_original_retirement_bypass(monkeypatch,scope):
    monkeypatch.setattr(evidence.storage,'current_card_for_update',lambda *_: dict(archived=1,serving_active=0))
    monkeypatch.setattr(evidence.storage,'completed_aggregate',lambda *_: (1,))
    monkeypatch.setattr(evidence.storage,'retired_operation',lambda *_: ('retired',))
    evidence._validate_checkpoint_scope(None,request(),completing_review=True)
    assert scope==['identity lock','deletion gate']


def test_aggregate_completion_gate_precedes_write(monkeypatch):
    monkeypatch.setattr(evidence.storage,'complete_review',lambda *_: pytest.fail('Unpersisted aggregate wrote evidence'))
    with pytest.raises(evidence.EvidenceConflict,match='did not confirm'):evidence.complete_review_evidence(None,request(),dict(persisted=False))


def test_stale_standalone_source_retains_driver_failure_and_stops_before_events(monkeypatch):
    prepared=evidence._compute_checkpoint_publication(request(),None,())
    monkeypatch.setattr(evidence,'_validate_checkpoint_scope',lambda *_args,**_kwargs: None)
    monkeypatch.setattr(evidence.storage,'insert_attempt_returning',lambda *_: None)
    monkeypatch.setattr(evidence,'_read_checkpoint_source',lambda *_args,**_kwargs: ({},()))
    monkeypatch.setattr(evidence.storage,'insert_missing_events',lambda *_: pytest.fail('Stale source wrote events'))
    with pytest.raises(SerializationFailure,match='source changed'):evidence.commit_standalone_checkpoint(object(),prepared)
    assert evidence.SerializationFailure is SerializationFailure


def test_standalone_publication_retains_borrowed_transaction_and_write_order(monkeypatch):
    database=object();events=[];prepared=evidence._compute_checkpoint_publication(request(),None,())
    monkeypatch.setattr(evidence,'_validate_checkpoint_scope',lambda db,*_args,**_kwargs: events.append('scope') if db is database else pytest.fail('Changed transaction'))
    monkeypatch.setattr(evidence.storage,'insert_attempt_returning',lambda *_: events.append('header') or ('attempt',))
    monkeypatch.setattr(evidence,'_read_checkpoint_source',lambda *_args,**_kwargs: events.append('source recheck') or ({},()))
    monkeypatch.setattr(evidence.storage,'insert_missing_events',lambda *_: events.append('events'))
    monkeypatch.setattr(evidence,'_persist_observations',lambda *_: events.append('observations'))
    monkeypatch.setattr(evidence.storage,'update_checkpoint',lambda *_: events.append('header update'))
    assert evidence.commit_standalone_checkpoint(database,prepared)==prepared.result
    assert events==['scope','header','source recheck','events','observations','header update']


@pytest.mark.parametrize('new_day',[False,True])
def test_observation_delta_and_unique_clean_day_preserve_idempotent_counts(monkeypatch,new_day):
    observation=evidence._compute_checkpoint_publication(request(),None,()).observations[0];events=[]
    monkeypatch.setattr(evidence.storage,'prior_observation',lambda *_: None)
    monkeypatch.setattr(evidence.storage,'add_summary_counts',lambda _db,key,counts: events.append(('counts',key,counts)))
    monkeypatch.setattr(evidence.storage,'insert_clean_day',lambda *_: ('day',) if new_day else None)
    monkeypatch.setattr(evidence.storage,'increment_clean_days',lambda *_: events.append('new day'))
    monkeypatch.setattr(evidence.storage,'upsert_observation',lambda *_: events.append('observation'))
    evidence._persist_observations(None,'attempt',[observation])
    assert events==[('counts',observation['decision_id'],(1,1,0,0,0,0)),*(['new day'] if new_day else []),'observation']
    events.clear()
    monkeypatch.setattr(evidence.storage,'prior_observation',lambda *_: (evidence.canonical_json(observation),))
    evidence._persist_observations(None,'attempt',[observation]);assert events==[]


def test_decision_projection_keeps_overflow_out_of_page_and_original_offsets(monkeypatch):
    row=dict(attempt_id='attempt',card_id='card',card_revision=2,manifest_id='manifest',repertoire_id='rep',state='partial',observation_json='{"clean":true}')
    monkeypatch.setattr(evidence.storage,'decision_summary',lambda *_: None)
    monkeypatch.setattr(evidence.storage,'decision_observations',lambda *_: [row,{**row,'attempt_id':'overflow'}])
    monkeypatch.setattr(evidence.storage,'recent_responses',lambda *_: [('attempt',0,'{"clean":true}')])
    monkeypatch.setattr(evidence.storage,'latest_failure',lambda *_: None)
    monkeypatch.setattr(evidence.storage,'latest_observation',lambda *_: dict(attempt_id='attempt',decision_index=0,observed_at=MOMENT))
    result=evidence.decision_evidence(None,'decision',offset=7,limit=1)
    assert result['next_offset']==8 and len(result['observations'])==1
    assert result['observations'][0]['attempt_id']=='attempt' and result['recent_outcomes'][0]['clean'] is True


def test_checkpoint_validation_translates_conflict_without_entering_writer(monkeypatch):
    monkeypatch.setattr(evidence,'validate_checkpoint',lambda *_: (_ for _ in ()).throw(evidence.EvidenceConflict('Changed')))
    monkeypatch.setattr(evidence,'_persist_checkpoint',lambda *_args,**_kwargs: pytest.fail('Invalid manifest entered writer'))
    with pytest.raises(HTTPException) as error:evidence.persist_checkpoint(None,dict(checkpoint=request().model_dump(mode='json'),prepared_manifest={}))
    assert error.value.detail==dict(code='opening_evidence_conflict',message='Changed',aggregate_review_allowed=True)
