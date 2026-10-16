"""A's real PostgreSQL publication/identity/receipt regression owners."""
from copy import deepcopy
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import random
import resource
import time
import uuid
from pathlib import Path

import chess
import pytest
from test_runtime import bundle, activate, head, review_payload, submit


def content_bundle(count=1):
    payload=bundle()
    payload.update(schema_version=2,source_digest='a'*64,routes=[],legacy_aliases={})
    definitions=[]
    facts=[]
    positions=set()
    candidate=0
    while len(definitions)<count:
        board=chess.Board()
        chooser=random.Random(candidate)
        if count>1:
            for _ in range(4):
                board.push(chooser.choice(sorted(board.legal_moves,key=lambda move:move.uci())))
        candidate+=1
        if board.fen() in positions:
            continue
        positions.add(board.fen())
        index=len(definitions)
        identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,'content-card-'+str(index)))
        move=next(iter(sorted(board.legal_moves,key=lambda value:value.uci()))).uci() if count>1 else 'e2e4'
        definition={**payload['publication']['definitions'][0],'card_id':identifier,'decision_id':'decision-'+str(index),
            'starting_fen':board.fen(),'specification':{'type':'move_line','mode':'single','grading_policy':'reference','accepted_lines':[[move]]}}
        definitions.append(definition)
        facts.append({**payload['facts'][0],'card_id':identifier,'route_order':index})
        payload['routes'].append({'repertoire_id':'repertoire-a','route_id':'route-'+str(index),'policy_id':'policy-a',
            'learner_color':'white','starting_fen':board.fen(),'moves':[move],'bindings':[{'ply':0,'card_id':identifier,'reentry':False}]})
    payload['publication']['definitions']=definitions
    payload['facts']=facts
    return payload


def stage_v2(case,payload):
    return case.client.post('/internal/publications/v2/stage',json=payload,headers={'Authorization':'Bearer '+case.configuration.operator_token})


def request_for(payload,expected=0):
    return {'activation_id':uuid.uuid4().hex,'publication_id':payload['publication']['publication_id'],
        'publication_generation':payload['publication']['publication_generation'],'expected_activation_revision':expected}


def activate_v2(case,payload,expected=0,request=None):
    staged=stage_v2(case,payload)
    assert staged.status_code==200,staged.text
    response=case.client.post('/internal/publications/v2/activate',json=request or request_for(payload,expected),headers={'Authorization':'Bearer '+case.configuration.operator_token})
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.parametrize('count',[129,512,2048])
def test_A_complete_large_publication_measured_without_truncation(case,count,tmp_path):
    payload=content_bundle(count)
    started=time.perf_counter()
    response=stage_v2(case,payload)
    staging=time.perf_counter()-started
    assert response.status_code==200,response.text
    assert case.query('SELECT count(*) FROM learning.cards')[0][0]==0
    request=request_for(payload)
    timings=[]
    for generation in (1,2,3):
        if generation>1:
            payload=deepcopy(payload)
            payload['publication']['publication_generation']=generation
            payload['publication']['source_revision']=generation
            for definition in payload['publication']['definitions']:
                definition['publication_generation']=generation
                definition['source_revision']=generation
            assert stage_v2(case,payload).status_code==200
            request=request_for(payload,generation-1)
        started=time.perf_counter()
        response=case.client.post('/internal/publications/v2/activate',json=request,headers={'Authorization':'Bearer '+case.configuration.operator_token})
        timings.append(time.perf_counter()-started)
        assert response.status_code==200,response.text
        assert case.query('SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0]==count
        assert case.query('SELECT count(*) FROM learning.opening_graph_steps WHERE generation=%s',(generation,))[0][0]==count
        assert case.client.get('/api/study/bootstrap').status_code==200
    measurement={'definitions':count,'memberships':count,'route_lengths':{'minimum':1,'maximum':1},
        'accepted_alternatives':{'minimum':1,'maximum':1},'serialized_bytes':len(json.dumps(payload).encode()),
        'stage_seconds':staging,'activation_seconds':timings,'driver_max_rss':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'statement_timeout_ms':1500,'transaction_timeout_ms':2500,'lock_timeout_ms':250}
    (tmp_path/'capacity.json').write_text(json.dumps(measurement,indent=2))
    destination=os.environ.get('TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE')
    if destination:
        Path(destination).mkdir(parents=True,exist_ok=True)
        (Path(destination)/f'capacity-{count}.json').write_text(json.dumps(measurement,indent=2))


def test_A_metadata_successor_preserves_issued_attempt_under_measured_foreground_load(case,monkeypatch):
    from tempo_learning_runtime.maintenance import run_once
    from tempo_learning_runtime.postgres_store import PostgresConnection
    payload=content_bundle(2048)
    activate_v2(case,payload)
    with case.database.scope():
        for _ in range(100):
            if not run_once():
                break
        else:
            raise AssertionError('Initial complete publication did not converge')
    issued=head(case)
    payload=deepcopy(payload)
    payload['publication'].update(publication_generation=2,source_revision=2)
    for definition in payload['publication']['definitions']:
        definition.update(publication_generation=2,source_revision=2,prompt='Renamed metadata')
    assert stage_v2(case,payload).status_code==200
    transaction_durations=[]
    lock_durations=[]
    lock_starts={}
    original_transaction=case.database.transaction
    original_native=PostgresConnection.execute_native
    @contextmanager
    def measured_transaction(**options):
        started=time.perf_counter()
        database=None
        with original_transaction(**options) as database:
            yield database
        if database in lock_starts:
            finished=time.perf_counter()
            transaction_durations.append(finished-started)
            lock_durations.append(finished-lock_starts.pop(database))
    def measured_native(database,statement,parameters=()):
        result=original_native(database,statement,parameters)
        if statement.startswith('SELECT pg_advisory_xact_lock') and parameters==('learning:publication:publication-a',):
            lock_starts[database]=time.perf_counter()
        return result
    foreground=[]
    with monkeypatch.context() as patch:
        patch.setattr(case.database,'transaction',measured_transaction)
        patch.setattr(PostgresConnection,'execute_native',measured_native)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(case.client.post,'/internal/publications/v2/activate',json=request_for(payload,1),headers={'Authorization':'Bearer '+case.configuration.operator_token})
            while not future.done():
                started=time.perf_counter()
                response=case.client.get('/api/study/bootstrap')
                foreground.append(time.perf_counter()-started)
                assert response.status_code==200,response.text
            activated=future.result()
    assert activated.status_code==200,activated.text
    assert foreground and len(transaction_durations)==len(lock_durations)==1
    assert transaction_durations[0]<2.5
    response=submit(case,issued,review_payload(issued))
    assert response.status_code==200,response.text
    assert case.query('SELECT count(*) FROM learning.reviews')[0][0]==1
    measurement={'definitions':2048,'metadata_successor_transaction_seconds':transaction_durations,
        'publication_lock_held_seconds':lock_durations,'concurrent_foreground_bootstrap_seconds':foreground,
        'predecessor_attempt_review_status':response.status_code,'transaction_timeout_ms':2500,'lock_timeout_ms':250}
    destination=os.environ.get('TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE')
    if destination:
        (Path(destination)/'foreground-capacity-2048.json').write_text(json.dumps(measurement,indent=2))


def test_A_activation_response_loss_replays_original_operator_receipt(case):
    payload=content_bundle()
    request=request_for(payload)
    receipt=activate_v2(case,payload,request=request)
    operator={'Authorization':'Bearer '+case.configuration.operator_token}
    assert case.client.get('/internal/publications/operations/'+request['activation_id'],headers=operator).json()['response']==receipt
    assert case.client.post('/internal/publications/v2/activate',json=request,headers=operator).json()==receipt
    assert case.client.get('/api/operations/'+request['activation_id']).status_code==403
    assert case.client.post('/api/operations/'+request['activation_id']+'/retry').status_code==403
    assert case.client.get('/internal/publications/publication-a/status').status_code==401
    assert case.client.get('/internal/publications/publication-a/status',headers={'Authorization':'Bearer '+case.configuration.evidence_token}).status_code==401
    assert case.client.post('/internal/publications/v2/stage',content=b' '* (8*1024*1024+1),headers={**operator,'Content-Type':'application/json'}).status_code==413
    assert case.query('SELECT count(*) FROM learning.cards')[0][0]==1


def test_A_pending_publication_is_reconciled_by_operator_without_essential_dispatch(case):
    from tempo_learning_runtime import gateway
    from tempo_learning_runtime.maintenance import run_once
    payload=content_bundle()
    assert stage_v2(case,payload).status_code==200
    request=request_for(payload)
    with case.database.scope():
        gateway.record_intent(request['activation_id'],'publication.activate.v2',request)
        assert run_once() is False
        assert gateway.read_operation(request['activation_id'])['state']=='queued'
    response=case.client.post('/internal/publications/operations/'+request['activation_id']+'/retry',headers={'Authorization':'Bearer '+case.configuration.operator_token})
    assert response.status_code==200,response.text
    assert response.json()['activation_id']==request['activation_id']


def test_A_identity_rename_replay_preserves_genuine_review_and_alias(case):
    legacy=activate(case)
    card=head(case)
    response=submit(case,card,review_payload(card))
    assert response.status_code==200,response.text
    historical=[tuple(row) for row in case.query('SELECT * FROM learning.reviews')]
    scheduled=[tuple(row) for row in case.query('SELECT fsrs_card_json,repetitions,interval_days FROM learning.cards WHERE id=%s',(card['id'],))]
    # Disposable pre-publication legacy state, with a genuine saved Study review.
    case.query('UPDATE learning.cards SET publication_id=NULL,serving_active=0 WHERE id=%s',(card['id'],))
    payload=content_bundle()
    payload['publication']['publication_id']='content-publication'
    payload['publication']['definitions'][0]['publication_id']='content-publication'
    payload['legacy_aliases']={payload['facts'][0]['card_id']:card['id']}
    activate_v2(case,payload)
    assert case.query('SELECT count(*) FROM learning.cards')[0][0]==1
    assert [tuple(row) for row in case.query('SELECT * FROM learning.reviews')]==historical
    assert [tuple(row) for row in case.query('SELECT fsrs_card_json,repetitions,interval_days FROM learning.cards WHERE id=%s',(card['id'],))]==scheduled
    renamed=deepcopy(payload)
    renamed['publication']['publication_generation']=2
    renamed['publication']['source_revision']=2
    definition=renamed['publication']['definitions'][0]
    definition.update(publication_generation=2,source_revision=2,prompt='Renamed presentation')
    activate_v2(case,renamed,1)
    assert [tuple(row) for row in case.query('SELECT * FROM learning.reviews')]==historical
    with case.database.scope(),case.database.transaction(read_only=True) as database:
        from tempo_learning_runtime.remediation_adapter import AcceptedDefinitions
        accepted=AcceptedDefinitions(database).decision(definition['card_id'],definition['decision_id'],'content-publication',2)
        assert accepted.card_id==card['id'] and definition['card_id'] in accepted.aliases


def test_A_identity_conflict_rolls_back_activation(case):
    payload=content_bundle()
    payload['legacy_aliases']={payload['facts'][0]['card_id']:'missing-history-card'}
    assert stage_v2(case,payload).status_code==200
    response=case.client.post('/internal/publications/v2/activate',json=request_for(payload),headers={'Authorization':'Bearer '+case.configuration.operator_token})
    assert response.status_code==409
    assert case.query('SELECT count(*) FROM learning.cards')[0][0]==0
    assert case.query('SELECT count(*) FROM learning.serving_publications')[0][0]==0


def test_A_old_ack_after_replacement_and_final_retirement(case):
    payload=content_bundle()
    first=activate_v2(case,payload)
    replacement=deepcopy(payload)
    replacement['publication'].update(publication_generation=2,source_revision=2)
    replacement['publication']['definitions'][0].update(publication_generation=2,source_revision=2)
    activate_v2(case,replacement,1)
    operator={'Authorization':'Bearer '+case.configuration.operator_token}
    status=case.client.get('/internal/publications/publication-a/status',headers=operator).json()
    assert first['activation_revision']==1 and status['activation_revision']==2 and status['publication_generation']==2
    request={'retirement_id':uuid.uuid4().hex,'publication_id':'publication-a','publication_generation':2,'expected_activation_revision':2}
    retired=case.client.post('/internal/publications/retire',json=request,headers=operator)
    assert retired.status_code==200,retired.text
    assert retired.json()['stale_attempts_fenced']
    assert case.client.get('/internal/publications/publication-a/status',headers=operator).json()['status']=='retired'
    assert case.query('SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0]==0
