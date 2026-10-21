"""Publication authority and lifecycle decisions use semantic storage operations."""
from contextlib import contextmanager
from types import SimpleNamespace
import json

import chess
from fastapi import HTTPException
import pytest

from tempo_learning_runtime import publications,publication_bridge as bridge


def bundle():
    return bridge.PublicationBundleV2.model_validate(dict(
        schema_version=2,source_digest='a'*64,
        publication=dict(contract_version=1,workspace_id='workspace',publication_id='pub',
            source_revision=1,publication_generation=2,built_at='2026-10-21T00:00:00Z',definitions=[dict(
                card_id='authored',decision_id='decision',policy_id='policy',source_revision=1,
                publication_id='pub',publication_generation=2,exercise_revision=2,
                starting_fen=chess.STARTING_FEN,prompt='Play',specification=dict(type='move_line',
                    mode='single',grading_policy='reference',accepted_lines=[['e2e4']]))]),
        facts=[dict(card_id='authored',content_type='opening',trained_color='white',
            memberships=['rep'],due_date='2026-10-21')],
        routes=[dict(repertoire_id='rep',route_id='route',policy_id='policy',learner_color='white',
            starting_fen=chess.STARTING_FEN,moves=['e2e4'],bindings=[dict(ply=0,card_id='authored')])],
        legacy_aliases={'authored':'physical'},
    ))


def activation_payload():
    return dict(publication_id='pub',publication_generation=2,expected_activation_revision=1,activation_id='activation')


@pytest.mark.parametrize('owner',[publications,bridge])
def test_staging_workspace_rejection_precedes_lock_or_storage(monkeypatch,owner):
    current=bundle()
    if owner is publications:monkeypatch.setattr(owner,'PublicationBundle',SimpleNamespace(model_validate=lambda _: current))
    monkeypatch.setattr(owner.storage,'lock_publication',lambda *_: pytest.fail('Wrong workspace obtained lock'))
    with pytest.raises(HTTPException) as error:owner.stage(None,current,'other')
    assert error.value.status_code==403


@pytest.mark.parametrize('owner',[publications,bridge])
@pytest.mark.parametrize('conflict',['identity','generation'])
def test_stage_fences_precede_accepted_write(monkeypatch,owner,conflict):
    current=bundle();events=[]
    if owner is publications:monkeypatch.setattr(owner,'PublicationBundle',SimpleNamespace(model_validate=lambda _: current))
    monkeypatch.setattr(owner.storage,'lock_publication',lambda *_: events.append('lock'))
    monkeypatch.setattr(owner.storage,'accepted_digest',lambda *_: events.append('identity') or (('different',) if conflict=='identity' else None))
    monkeypatch.setattr(owner.storage,'latest_generation',lambda *_: events.append('latest') or 3)
    monkeypatch.setattr(owner.storage,'insert_accepted',lambda *_: pytest.fail('Stale identity reached write'))
    with pytest.raises(HTTPException) as error:owner.stage(None,current,'workspace')
    assert error.value.status_code==409
    assert events==(['lock','identity'] if conflict=='identity' else ['lock','identity','latest'])


@pytest.mark.parametrize('owner',[publications,bridge])
@pytest.mark.parametrize('same_request',[False,True])
def test_activation_receipt_replay_fences_before_serving_lock(monkeypatch,owner,same_request):
    payload=activation_payload();events=[]
    fingerprint=owner.request_digest('publication.activate.v2' if owner is bridge else 'publication.activate',payload)
    monkeypatch.setattr(owner.storage,'lock_publication',lambda *_: events.append('owner lock'))
    monkeypatch.setattr(owner.storage,'activation_receipt',lambda *_: (fingerprint if same_request else 'different','{"status":"activated"}'))
    monkeypatch.setattr(owner.storage,'serving_for_update',lambda *_: pytest.fail('Replay reached writer state'))
    if same_request:assert owner.activate(None,payload)==dict(status='activated')
    else:
        with pytest.raises(HTTPException) as error:owner.activate(None,payload)
        assert error.value.status_code==409
    assert events==['owner lock']


@pytest.fixture
def v2_activation(monkeypatch):
    current=bundle();events=[]
    token=bridge.prepared_activation.set(('accepted-digest',current))
    monkeypatch.setattr(bridge.storage,'lock_publication',lambda *_: events.append('publication lock'))
    monkeypatch.setattr(bridge.storage,'activation_receipt',lambda *_: None)
    monkeypatch.setattr(bridge.storage,'serving_for_update',lambda *_: (1,1))
    monkeypatch.setattr(bridge.storage,'accepted_for_update',lambda *_: ('unused','staged','accepted-digest'))
    monkeypatch.setattr(bridge.storage,'latest_generation',lambda *_: 2)
    monkeypatch.setattr(bridge,'lock_opening_repertoires',lambda *_: events.append('repertoire locks'))
    monkeypatch.setattr(bridge.storage,'publication_card_ids',lambda *_: [('removed',)])
    monkeypatch.setattr(bridge.storage,'lock_cards',lambda _db,ids: events.append(('card locks',ids)))
    old=dict(id='physical',publication_id=None,publication_generation=None,content_type='opening',
        trained_color='white',revision=2,start_fen=chess.STARTING_FEN,moves_json='["e2e4"]')
    monkeypatch.setattr(bridge.storage,'cards_for_update',lambda *_: [old])
    monkeypatch.setattr(bridge.storage,'deleted_cards',lambda *_: None)
    monkeypatch.setattr(bridge.storage,'other_opening_owner',lambda *_: None)
    monkeypatch.setattr(bridge.storage,'accepted_generations',lambda *_: [])
    monkeypatch.setattr(bridge.storage,'accepted_aliases',lambda *_: [])
    for name in ['insert_repertoires','insert_aliases','deactivate_cards','upsert_cards','remove_memberships',
                 'insert_memberships','remove_integrity_blocks','insert_integrity_blocks','insert_lines',
                 'upsert_opening_publications','insert_opening_steps','supersede_card_queues',
                 'supersede_accepted','activate_accepted','upsert_serving','insert_activation_receipt']:
        monkeypatch.setattr(bridge.storage,name,lambda *args,_name=name: events.append((_name,args[1:])))
    monkeypatch.setattr(bridge,'enqueue_task_in_transaction',lambda *args: events.append(('enqueue',args[1:])))
    yield current,old,events
    bridge.prepared_activation.reset(token)


def test_prepared_digest_rejection_precedes_repertoire_and_card_locks(v2_activation):
    _current,_old,events=v2_activation
    token=bridge.prepared_activation.set(('changed',_current))
    try:
        with pytest.raises(HTTPException):bridge.activate(None,activation_payload())
        assert events==['publication lock']
    finally:bridge.prepared_activation.reset(token)


@pytest.mark.parametrize('conflict',['alias','executable','revision'])
def test_alias_and_revision_authority_rejection_precedes_any_write(monkeypatch,v2_activation,conflict):
    current,old,events=v2_activation
    if conflict=='alias':monkeypatch.setattr(bridge.storage,'accepted_aliases',lambda *_: [('authored','elsewhere','pub','different')])
    elif conflict=='executable':old['moves_json']='["d2d4"]'
    else:
        old.update(publication_id='pub',publication_generation=1)
        previous=current.publication.definitions[0].model_dump(mode='json')
        previous['starting_fen']='different'
        monkeypatch.setattr(bridge.storage,'accepted_generations',lambda *_: [(1,json.dumps(dict(publication=dict(definitions=[previous]))))])
    with pytest.raises(HTTPException) as error:bridge.activate(None,activation_payload())
    assert error.value.status_code==409
    assert events==['publication lock','repertoire locks',('card locks',['authored','physical','removed'])]


def test_alias_activation_keeps_physical_identity_and_publishes_before_followup(v2_activation):
    current,old,events=v2_activation
    result=bridge.activate(None,activation_payload())
    assert result['activation_revision']==2
    writes={event[0]:event[1] for event in events if isinstance(event,tuple) and event[0] in {'upsert_cards','insert_aliases','insert_opening_steps','supersede_card_queues'}}
    assert writes['upsert_cards'][0][0]['id']=='physical'
    assert writes['insert_aliases'][0][0]['content_card_id']=='authored'
    assert writes['insert_aliases'][0][0]['learning_card_id']=='physical'
    assert writes['insert_opening_steps'][0][0]['card_id']=='physical'
    assert writes['supersede_card_queues']==(['removed'],)
    assert events[-2][0]=='insert_activation_receipt' and events[-1][0]=='enqueue'
    assert old['revision']==2 and current.publication.definitions[0].card_id=='authored'


def test_retirement_revision_fence_precedes_repertoire_or_card_effects(monkeypatch):
    monkeypatch.setattr(publications.storage,'lock_publication',lambda *_: None)
    monkeypatch.setattr(publications.storage,'serving_for_update',lambda *_: (2,3))
    monkeypatch.setattr(publications,'lock_opening_repertoires',lambda *_: pytest.fail('Stale retirement reached owner locks'))
    with pytest.raises(HTTPException):publications.retire(None,activation_payload())


def test_retirement_preserves_lock_and_effect_order(monkeypatch):
    events=[]
    monkeypatch.setattr(publications.storage,'lock_publication',lambda *_: events.append('publication'))
    monkeypatch.setattr(publications.storage,'serving_for_update',lambda *_: (2,1))
    monkeypatch.setattr(publications,'lock_opening_repertoires',lambda *_: events.append('repertoires'))
    monkeypatch.setattr(publications.storage,'ordered_publication_cards',lambda *_: [('a',),('z',)])
    monkeypatch.setattr(publications.storage,'lock_card',lambda _db,card: events.append(('card',card)))
    names=['deactivate_cards','archive_study_exercises','disable_endgame_templates','supersede_publication_queue','retire_accepted','increment_activation_revision']
    for name in names:monkeypatch.setattr(publications.storage,name,lambda *_args,_name=name: events.append(_name))
    result=publications.retire(None,activation_payload())
    assert events==['publication','repertoires',('card','a'),('card','z'),*names]
    assert result==dict(status='retired',stale_attempts_fenced=True,activation_revision=2)


def test_repertoire_owner_locks_include_removed_and_new_memberships_in_sorted_order(monkeypatch):
    locked=[]
    monkeypatch.setattr(publications.storage,'opening_repertoires',lambda *_: [('z',),('a',)])
    monkeypatch.setattr(publications.storage,'lock_repertoire',lambda _db,key: locked.append(key))
    publications.lock_opening_repertoires(None,'pub',[SimpleNamespace(content_type='opening',memberships=['b','a'])])
    assert locked==['a','b','z']


def test_opening_graph_verification_requires_exact_executable_facts(monkeypatch):
    current=bundle();fact=current.facts[0]
    monkeypatch.setattr(publications.storage,'opening_graph_steps',lambda *_: [('rep',2,'pub:rep',0,fact.card_id,None,chess.STARTING_FEN,'["d2d4"]','white')])
    with pytest.raises(ValueError,match='does not match executable'):publications.verify_opening_graph(None,current)


def test_activation_preparation_closes_read_before_validation_and_resets_on_failure(monkeypatch):
    from tempo_learning_database import connections
    from tempo_learning_runtime import gateway
    events=[];database=object();prepared=bundle()
    @contextmanager
    def connection(**options):
        assert options==dict(read_only=True)
        events.append('read begin');yield database;events.append('read closed')
    monkeypatch.setattr(connections,'connection',connection)
    monkeypatch.setattr(gateway,'read_operation',lambda *_args,**_options: dict(state='unknown'))
    monkeypatch.setattr(bridge.storage,'accepted_payload',lambda db,*_: ('digest','document') if db is database else pytest.fail('Wrong read connection'))
    def validate(value):
        assert events[-1]=='read closed' and value=='document'
        events.append('validated');return prepared
    monkeypatch.setattr(bridge,'PublicationBundleV2',SimpleNamespace(model_validate_json=validate))
    def dispatch(*_):
        assert bridge.prepared_activation.get()==('digest',prepared)
        events.append('dispatch');raise ValueError('rollback')
    monkeypatch.setattr(gateway,'dispatch',dispatch)
    prior=bridge.prepared_activation.get()
    with pytest.raises(ValueError,match='rollback'):bridge.dispatch_activation(activation_payload(),'activation')
    assert bridge.prepared_activation.get() is prior
    assert events==['read begin','read closed','validated','dispatch']
