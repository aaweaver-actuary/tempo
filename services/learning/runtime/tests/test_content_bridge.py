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
import psycopg
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


@pytest.mark.parametrize('count',[129,512,2048,7258])
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
    from tempo_learning_database.adapters.postgres_store import PostgresConnection
    payload=content_bundle(2048)
    activate_v2(case,payload)
    with case.database.scope():
        for _ in range(100):
            run_once()
            response=case.client.get('/api/queue/window?include_opening_evidence=true')
            assert response.status_code==200,response.text
            if response.json()['cards']:
                break
        else:
            raise AssertionError('Initial complete publication did not issue foreground work')
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
    assert case.client.post('/internal/publications/v2/stage',content=b' '* (12*1024*1024+1),headers={**operator,'Content-Type':'application/json'}).status_code==413
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
    missing_alias=deepcopy(payload)
    missing_alias['legacy_aliases']={}
    missing_alias['publication'].update(publication_generation=2,source_revision=2)
    missing_alias['publication']['definitions'][0].update(publication_generation=2,source_revision=2)
    assert stage_v2(case,missing_alias).status_code==200
    denied=case.client.post('/internal/publications/v2/activate',json=request_for(missing_alias,1),headers={'Authorization':'Bearer '+case.configuration.operator_token})
    assert denied.status_code==409,denied.text
    assert case.query('SELECT count(*) FROM learning.cards')[0][0]==1
    assert [tuple(row) for row in case.query('SELECT * FROM learning.reviews')]==historical
    renamed=deepcopy(payload)
    renamed['publication']['publication_generation']=3
    renamed['publication']['source_revision']=3
    definition=renamed['publication']['definitions'][0]
    definition.update(publication_generation=3,source_revision=3,prompt='Renamed presentation')
    activate_v2(case,renamed,1)
    assert [tuple(row) for row in case.query('SELECT * FROM learning.reviews')]==historical
    with case.database.scope(),case.database.transaction(read_only=True) as database:
        from tempo_learning_runtime.remediation_adapter import AcceptedDefinitions
        accepted=AcceptedDefinitions(database).decision(definition['card_id'],definition['decision_id'],'content-publication',3)
        assert accepted.card_id==card['id'] and definition['card_id'] in accepted.aliases


def test_C3_complete_historical_alias_activation_fits_documented_lock_capacity(case):
    """A full alias cutover needs both physical and Content card edit locks.

    This constructed capacity cohort is separate from the real-game rehearsal.
    Do not reduce its count: the small-alias bridge regression cannot expose the
    shared lock-table failure found with the actual 6,524-card captured source.
    """
    from tempo_learning_runtime.maintenance import run_once
    original=content_bundle(6524)
    activate_v2(case,original)
    with case.database.scope():
        for _ in range(100):
            run_once()
            if case.client.get('/api/queue/window').json()['cards']:
                break
        else:
            raise AssertionError('Capacity publication did not issue ordinary work')
    issued=head(case)
    assert submit(case,issued,review_payload(issued)).status_code==200
    history=[tuple(row) for row in case.query('SELECT * FROM learning.reviews ORDER BY id')]
    scheduled=[tuple(row) for row in case.query('SELECT id,state,due_date,fsrs_card_json,repetitions,interval_days FROM learning.cards ORDER BY id')]
    # Learning-owned fixture models pre-publication copied legacy records.
    case.query('UPDATE learning.cards SET publication_id=NULL,serving_active=0')
    successor=deepcopy(original)
    physical_ids={definition['card_id']:'adopted:'+definition['card_id'] for definition in original['publication']['definitions']}
    successor['source_digest']='b'*64
    successor['publication']['publication_id']='alias-capacity-publication'
    for definition in successor['publication']['definitions']:
        definition.update(card_id=physical_ids[definition['card_id']],publication_id='alias-capacity-publication')
    for fact in successor['facts']:
        fact['card_id']=physical_ids[fact['card_id']]
    for route in successor['routes']:
        for binding in route['bindings']:
            binding['card_id']=physical_ids[binding['card_id']]
    successor['legacy_aliases']={authored:physical for physical,authored in physical_ids.items()}
    activation_request=request_for(successor)
    started=time.perf_counter()
    receipt=activate_v2(case,successor,request=activation_request)
    elapsed=time.perf_counter()-started
    assert receipt['status']=='activated'
    assert case.query('SELECT count(*) FROM learning.content_card_aliases')[0][0]==6524
    assert case.query('SELECT count(*) FROM learning.cards')[0][0]==6524
    assert [tuple(row) for row in case.query('SELECT * FROM learning.reviews ORDER BY id')]==history
    assert [tuple(row) for row in case.query('SELECT id,state,due_date,fsrs_card_json,repetitions,interval_days FROM learning.cards ORDER BY id')]==scheduled
    assert activate_v2(case,successor,request=activation_request)==receipt
    destination=os.environ.get('TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE')
    if destination:
        Path(destination).mkdir(parents=True,exist_ok=True)
        (Path(destination)/'alias-capacity-6524.json').write_text(json.dumps({
            'synthetic':True,'aliases':6524,'distinct_card_edit_lock_keys':13048,
            'activation_including_stage_seconds':elapsed,'preserved_reviews':len(history),
            'preserved_schedules':len(scheduled),'receipt':receipt},indent=2))


@pytest.mark.parametrize('sequence_already_ahead',[False,True])
def test_C3_copied_review_identity_advances_without_reusing_or_reordering_history(case,sequence_already_ahead):
    import importlib.util
    helper_path=Path(__file__).parents[1]/'integration/restore_rehearsal.py'
    specification=importlib.util.spec_from_file_location('learning_owned_rehearsal_restore',helper_path)
    helper=importlib.util.module_from_spec(specification);specification.loader.exec_module(helper)
    activate(case)
    first=head(case)
    saved=submit(case,first,review_payload(first))
    assert saved.status_code==200,saved.text
    history=[tuple(row) for row in case.query('SELECT * FROM learning.reviews ORDER BY id')]
    assert len(history)==1 and saved.json()['review_id']==1
    with psycopg.connect(case.admin,options='-c search_path=learning,pg_catalog') as database:
        database.execute("SELECT setval('learning.reviews_id_seq',1,false)")
        # The pre-repair restore shape really rejects the next ordinary INSERT;
        # a rolled-back collision is not fabricated accepted review history.
        with pytest.raises(psycopg.errors.UniqueViolation):
            with database.transaction():
                database.execute('INSERT INTO learning.reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(%s,%s,%s,0,0)',
                    (first['id'],'correct',datetime.now(timezone.utc).isoformat()))
        database.execute("SELECT setval('learning.reviews_id_seq',1,false)")
        repaired=helper.advance_review_identity(database)
        assert repaired['changed'] and repaired['next_generated_id']==2
        assert not helper.advance_review_identity(database)['changed']
        if sequence_already_ahead:
            database.execute("SELECT setval('learning.reviews_id_seq',10000,true)")
            retained=helper.advance_review_identity(database)
            assert not retained['changed'] and retained['next_generated_id']==10001
    assert [tuple(row) for row in case.query('SELECT * FROM learning.reviews ORDER BY id')]==history
    subsequent=head(case)
    response=submit(case,subsequent,review_payload(subsequent))
    assert response.status_code==200,response.text
    assert response.json()['review_id']==(10001 if sequence_already_ahead else 2)
    assert tuple(case.query('SELECT * FROM learning.reviews WHERE id=1')[0])==history[0]


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


def test_A_historical_alias_move_membership_accepts_new_alternative(case):
    activate(case)
    issued = head(case)
    physical_id = issued['id']
    assert submit(case, issued, review_payload(issued)).status_code == 200
    history = [tuple(row) for row in case.query("SELECT * FROM learning.reviews")]
    schedule = [tuple(row) for row in case.query("SELECT state,due_date,fsrs_card_json FROM learning.cards WHERE id=%s", (physical_id,))]
    case.query("UPDATE learning.cards SET publication_id=NULL,serving_active=0 WHERE id=%s", (physical_id,))
    payload = content_bundle()
    payload['publication']['publication_id'] = 'content-publication'
    payload['publication']['definitions'][0]['publication_id'] = 'content-publication'
    authored_id = payload['publication']['definitions'][0]['card_id']
    payload['legacy_aliases'] = {authored_id: physical_id}
    activate_v2(case, payload)
    successor = deepcopy(payload)
    successor["publication"].update(publication_generation=2, source_revision=2)
    successor["publication"]["definitions"][0].update(publication_generation=2, source_revision=2, exercise_revision=2)
    successor["publication"]["definitions"][0]["specification"]["accepted_lines"].append(["d2d4"])
    activate_v2(case, successor, 1)
    for move, expected in (("e2e4", True), ("d2d4", True), ("c2c4", False)):
        response = case.client.get('/api/cards/' + physical_id + '/move-membership', params={"revision": 2, "fen": chess.STARTING_FEN, "move_uci": move})
        assert response.status_code == 200, response.text
        assert response.json()["verified"] is expected
    assert [tuple(row) for row in case.query("SELECT * FROM learning.reviews")] == history
    assert [tuple(row) for row in case.query("SELECT state,due_date,fsrs_card_json FROM learning.cards WHERE id=%s", (physical_id,))] == schedule
    assert case.query("SELECT count(*) FROM learning.cards WHERE id=%s", (authored_id,))[0][0] == 0
    assert case.client.get('/api/cards/' + physical_id + '/move-membership', params={"revision": 1, "fen": chess.STARTING_FEN, "move_uci": "e2e4"}).status_code == 409
    destination = os.environ.get('TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE')
    if destination:
        Path(destination).mkdir(parents=True, exist_ok=True)
        (Path(destination)/'alias-membership.json').write_text(json.dumps({
            'physical_card_id':physical_id, 'authored_card_id':authored_id,
            'reviews_before':history, 'reviews_after':[tuple(row) for row in case.query('SELECT * FROM learning.reviews')],
            'schedule_before':schedule, 'schedule_after':[tuple(row) for row in case.query('SELECT state,due_date,fsrs_card_json FROM learning.cards WHERE id=%s',(physical_id,))],
            'accepted_moves':['e2e4','d2d4'], 'rejected_legal_move':'c2c4', 'obsolete_revision_status':409}, indent=2, default=str))


def abort_document(request, retirement=False):
    return {'workspace_id': 'workspace-a', 'command_name': 'publication.retire' if retirement else 'publication.activate.v2', 'request': request}


def abort_publication(case, request, retirement=False, document=None, credential=None):
    identifier = request['retirement_id' if retirement else 'activation_id']
    return case.client.post('/internal/publications/operations/' + identifier + '/abort',
        json=document or abort_document(request, retirement),
        headers={'Authorization': 'Bearer ' + (credential or case.configuration.operator_token)})


@pytest.mark.parametrize('queued', [False, True])
@pytest.mark.parametrize('retirement', [False, True])
def test_A_abort_before_activation_fences_original_identity(case, queued, retirement):
    from tempo_learning_runtime import gateway
    from tempo_learning_runtime.maintenance import safe_run_once
    payload = content_bundle()
    request = request_for(payload)
    if retirement:
        activate_v2(case, payload)
        request = {'retirement_id': request.pop('activation_id'), **request, 'expected_activation_revision': 1}
    if queued:
        with case.database.scope():
            gateway.record_intent(request.get('activation_id') or request['retirement_id'], abort_document(request, retirement)['command_name'], request)
    before = [tuple(row) for row in case.query('SELECT * FROM learning.serving_publications')]
    fenced = abort_publication(case, request, retirement)
    assert fenced.status_code == 200, fenced.text
    receipt = fenced.json()
    assert receipt['state'] == 'failed' and receipt['request'] == request
    assert receipt['error']['code'] == 'publication_abandoned_before_execution'
    assert 'response' not in receipt
    assert abort_publication(case, request, retirement).json() == receipt
    identifier = request.get('activation_id') or request['retirement_id']
    operator = {'Authorization': 'Bearer ' + case.configuration.operator_token}
    path = '/internal/publications/retire' if retirement else '/internal/publications/v2/activate'
    # No stage is required to rediscover a fenced activation's terminal result.
    assert case.client.post(path, json=request, headers=operator).status_code == 409
    assert case.client.post('/internal/publications/operations/' + identifier + '/retry', headers=operator).status_code == 409
    with case.database.scope():
        gateway.execute(identifier, publication=True)
        safe_run_once()
    assert case.client.get('/internal/publications/operations/' + identifier, headers=operator).json() == receipt
    assert [tuple(row) for row in case.query('SELECT * FROM learning.serving_publications')] == before
    assert case.query('SELECT count(*) FROM learning.activation_receipts')[0][0] == (1 if retirement else 0)


@pytest.mark.parametrize('completed', [False, True])
def test_A_activation_before_abort_preserves_real_completion(case, completed):
    payload = content_bundle()
    if not completed:
        payload['legacy_aliases'] = {payload['publication']['definitions'][0]['card_id']: 'missing-physical-card'}
    assert stage_v2(case, payload).status_code == 200
    request = request_for(payload)
    operator = {'Authorization': 'Bearer ' + case.configuration.operator_token}
    response = case.client.post('/internal/publications/v2/activate', json=request, headers=operator)
    assert response.status_code == (200 if completed else 409)
    original = case.client.get('/internal/publications/operations/' + request['activation_id'], headers=operator).json()
    assert abort_publication(case, request).json() == original
    assert original['state'] == ('complete' if completed else 'failed')
    assert case.query('SELECT count(*) FROM learning.activation_receipts')[0][0] == int(completed)


@pytest.mark.parametrize('mismatch', ['workspace', 'publication', 'generation', 'revision', 'identity', 'family', 'extra'])
def test_A_publication_abort_rejects_conflicting_request(case, mismatch):
    from tempo_learning_runtime import gateway
    payload = content_bundle()
    request = request_for(payload)
    with case.database.scope():
        gateway.record_intent(request['activation_id'], 'publication.activate.v2', request)
    document = abort_document(deepcopy(request))
    if mismatch == 'workspace': document['workspace_id'] = 'another-workspace'
    elif mismatch == 'publication': document['request']['publication_id'] = 'another-publication'
    elif mismatch == 'generation': document['request']['publication_generation'] += 1
    elif mismatch == 'revision': document['request']['expected_activation_revision'] += 1
    elif mismatch == 'identity': document['request']['activation_id'] = 'another-operation'
    elif mismatch == 'family': document['command_name'] = 'cards.review'
    else: document['request']['prepared_manifest'] = {'forged': True}
    response = abort_publication(case, request, document=document)
    assert response.status_code in (403, 409, 422), response.text
    with case.database.scope():
        receipt = gateway.read_operation(request['activation_id'], publication=True)
    assert receipt['state'] == 'queued' and receipt['request'] == request


@pytest.mark.parametrize('credential', ['study', 'evidence', 'invalid'])
def test_A_publication_abort_requires_operator_credential(case, credential):
    request = request_for(content_bundle())
    token = {'study': case.configuration.token, 'evidence': case.configuration.evidence_token, 'invalid': 'invalid'}[credential]
    assert abort_publication(case, request, credential=token).status_code == 401
    assert case.query('SELECT count(*) FROM learning.operation_receipts')[0][0] == 0


def test_A_publication_abort_is_idempotent_after_restart(case):
    from fastapi.testclient import TestClient
    from tempo_learning_runtime import create_app
    request = request_for(content_bundle())
    receipt = abort_publication(case, request).json()
    before = [tuple(row) for row in case.query('SELECT * FROM learning.operation_receipts')]
    with TestClient(create_app(case.configuration)) as restarted:
        response = restarted.post('/internal/publications/operations/' + request['activation_id'] + '/abort',
            json=abort_document(request), headers={'Authorization': 'Bearer ' + case.configuration.operator_token})
        assert response.status_code == 200 and response.json() == receipt
    assert [tuple(row) for row in case.query('SELECT * FROM learning.operation_receipts')] == before


@pytest.mark.parametrize('first', ['activate', 'abort'])
@pytest.mark.parametrize('retry', [False, True])
def test_A_publication_abort_serializes_with_activation(case, monkeypatch, first, retry):
    from threading import Event
    from contextvars import ContextVar
    from tempo_learning_runtime import gateway, publication_bridge
    from tempo_learning_database.adapters.postgres_store import PostgresConnection
    payload = content_bundle()
    assert stage_v2(case, payload).status_code == 200
    request = request_for(payload)
    identifier = request['activation_id']
    if retry:
        with case.database.scope():
            gateway.record_intent(identifier, 'publication.activate.v2', request)
    entered, contending, release = Event(), Event(), Event()
    role = ContextVar('publication_race_role', default=None)
    original_execute = PostgresConnection.execute_native
    def execute_native(database, statement, parameters=()):
        tracked = statement.startswith('SELECT pg_advisory_xact_lock') and parameters == (identifier,)
        if tracked and role.get() != first:
            contending.set()
        result = original_execute(database, statement, parameters)
        if tracked and role.get() == first:
            entered.set()
            assert release.wait(timeout=2)
        return result
    monkeypatch.setattr(PostgresConnection, 'execute_native', execute_native)
    def perform(action):
        role.set(action)
        operator = {'Authorization': 'Bearer ' + case.configuration.operator_token}
        if action == 'abort':
            return abort_publication(case, request)
        path = '/internal/publications/operations/' + identifier + '/retry' if retry else '/internal/publications/v2/activate'
        return case.client.post(path, json={} if retry else request, headers=operator)
    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(perform, first)
        try:
            assert entered.wait(timeout=3)
            loser = executor.submit(perform, 'abort' if first == 'activate' else 'activate')
            assert contending.wait(timeout=3)
        finally:
            release.set()
        assert winner.result().status_code == 200
        assert loser.result().status_code == (200 if first == 'activate' else 409)
    receipt = case.client.get('/internal/publications/operations/' + identifier,
        headers={'Authorization': 'Bearer ' + case.configuration.operator_token}).json()
    assert receipt['state'] == ('complete' if first == 'activate' else 'failed')
    assert case.query('SELECT count(*) FROM learning.activation_receipts')[0][0] == int(first == 'activate')
    assert case.query('SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0] == int(first == 'activate')


@pytest.mark.parametrize('retirement', [False, True])
def test_A_publication_transport_key_cannot_bypass_fenced_identity(case, retirement):
    request = request_for(content_bundle())
    if retirement:
        request['retirement_id'] = request.pop('activation_id')
    abort_publication(case, request, retirement)
    path = '/internal/publications/retire' if retirement else '/internal/publications/v2/activate'
    response = case.client.post(path, json=request, headers={'Authorization': 'Bearer ' + case.configuration.operator_token, 'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 422
    assert case.query('SELECT count(*) FROM learning.operation_receipts')[0][0] == 1


def test_A_publication_fence_measured_under_existing_budgets(case, monkeypatch):
    from contextlib import contextmanager
    from tempo_learning_runtime import gateway
    timings = []
    original_connection = gateway.connection
    @contextmanager
    def measured_connection(**options):
        started = time.perf_counter()
        try:
            with original_connection(**options) as database:
                yield database
        finally:
            timings.append({'read_only': options.get('read_only', False), 'seconds': time.perf_counter() - started})
    monkeypatch.setattr(gateway, 'connection', measured_connection)
    request = request_for(content_bundle())
    started = time.perf_counter()
    response = abort_publication(case, request)
    elapsed = time.perf_counter() - started
    assert response.status_code == 200 and response.json()['state'] == 'failed'
    assert len([timing for timing in timings if not timing['read_only']]) == 1
    assert case.query('SELECT count(*) FROM learning.cards')[0][0] == 0
    evidence = Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'fence-timing.json').write_text(json.dumps({'api_seconds': elapsed, 'transactions': timings,
        'lock_timeout_ms': 250, 'statement_timeout_ms': 1500, 'foreground_transaction_timeout_ms': 2500}, indent=2))


def test_A_selected_maintenance_cannot_resurrect_terminally_fenced_retirement(case, monkeypatch):
    from threading import Event
    from tempo_learning_runtime import gateway, maintenance
    payload = content_bundle()
    activate_v2(case, payload)
    request = {'retirement_id': uuid.uuid4().hex, 'publication_id': payload['publication']['publication_id'],
        'publication_generation': 1, 'expected_activation_revision': 1}
    with case.database.scope():
        gateway.record_intent(request['retirement_id'], 'publication.retire', request)
    selected, release = Event(), Event()
    original_execute = gateway.execute
    def execute_after_selection(identifier, **options):
        assert identifier == request['retirement_id']
        selected.set()
        assert release.wait(timeout=3)
        return original_execute(identifier, **options)
    monkeypatch.setattr(gateway, 'execute', execute_after_selection)
    def maintain():
        with case.database.scope():
            return maintenance.run_once()
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(maintain)
        try:
            assert selected.wait(timeout=3)
            receipt = abort_publication(case, request, retirement=True).json()
            assert receipt['state'] == 'failed'
        finally:
            release.set()
        assert worker.result()
    assert case.query('SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0] == 1
    assert case.query('SELECT activation_revision FROM learning.serving_publications')[0][0] == 1
    with case.database.scope():
        assert gateway.read_operation(request['retirement_id'], publication=True) == receipt


def test_C3_publication_bundle_capacity_is_bounded_after_default_expansion(monkeypatch):
    from tempo_learning_runtime import publication_bridge as module
    payload=content_bundle()
    expanded=module.PublicationBundleV2.model_validate(payload)
    assert module.MAX_DEFINITIONS == 8192
    assert module.MAX_BUNDLE_BYTES == 12 * 1024 * 1024
    size=len(expanded.model_dump_json().encode())
    monkeypatch.setattr(module,'MAX_BUNDLE_BYTES',size)
    assert module.PublicationBundleV2.model_validate(payload).publication == expanded.publication
    monkeypatch.setattr(module,'MAX_BUNDLE_BYTES',size-1)
    with pytest.raises(ValueError,match='bundle exceeds'):
        module.PublicationBundleV2.model_validate(payload)
