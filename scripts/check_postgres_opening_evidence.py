"""AS-03/08/09/10/11/15/16/19 against runner-owned PostgreSQL transactions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import hashlib
import copy
import json
import os
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fastapi import HTTPException
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
    else:test_postgres_shadow_replay_atomicity_and_scheduling_invariance()
