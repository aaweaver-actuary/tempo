"""Analysis input admission/finalization with actual fixed ops and synthetic rows."""
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tempo_intelligence.analysis.models import Command, Manifest, Page, payload_digest
from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import digest
from tempo_intelligence_database.adapters.analysis_store import AnalysisStore


def fixtures():
    directory=Path(__file__).parent.parent/'fixtures/analysis'
    return Manifest.model_validate_json((directory/'manifest.json').read_text()),Page.model_validate_json((directory/'games-0.json').read_text())


def reader_with_script(steps):
    pending=deque(steps)
    events=[]
    class Database:
        active=False
        def execute(self,_query,parameters=()):
            assert self.active
            event,expected,row=pending.popleft()
            assert tuple(getattr(item,'obj',item) for item in parameters)==expected
            events.append(event)
            return SimpleNamespace(fetchone=lambda:row,fetchall=lambda:row)
    database=Database()
    @contextmanager
    def transaction(**options):
        assert options in ({},{'read_only':True})
        events.append('read' if options else 'write')
        database.active=True
        try:
            yield database
        except DomainError:
            events.append('rollback')
            raise
        else:
            events.append('commit')
        finally:
            database.active=False
    class Reader(AnalysisStore):
        def __getattribute__(self,name):
            if name in ('_workspace','workspace','_input','_receipt','_save_receipt'):
                events.append('lookup-'+name)
            return super().__getattribute__(name)
    reader=Reader(SimpleNamespace(workspace_id='fixture',transaction=transaction))
    class Observed:
        def __init__(self,value,label):self.value=value;self.label=label
        def __getattr__(self,name):return getattr(self.value,name)
        def model_dump(self,**options):
            events.append((self.label+'-dump',database.active))
            return self.value.model_dump(**options)
    return reader,database,pending,events,Observed


@pytest.mark.parametrize('conflict',[False,True],ids=['accept','immutable-conflict'])
def test_manifest_admission_and_two_serializations_precede_write_and_response_follows_commit(conflict):
    value,_=fixtures()
    payload=value.model_dump(mode='json');fingerprint=payload_digest(value)
    row={'digest':'sha256:different' if conflict else fingerprint,'state':'staging'}
    reader,database,pending,events,Observed=reader_with_script([
        ('insert',(value.input_id,1,payload,fingerprint),None),('read',(value.input_id,1),row)])
    observed=Observed(value,'manifest')
    if conflict:
        with pytest.raises(DomainError,match='immutable_input_conflict'):reader.manifest(observed)
    else:
        assert reader.manifest(observed)=={'schema_version':1,'workspace_id':'fixture','input_id':value.input_id,'revision':1,'state':'staging','digest':fingerprint}
    assert events==['lookup-_workspace','lookup-workspace',('manifest-dump',False),('manifest-dump',False),'write','insert','read']+(['rollback'] if conflict else ['commit','lookup-workspace'])
    assert not pending and not database.active


@pytest.mark.parametrize('case',['accept','reference-mismatch','immutable-conflict'])
def test_page_admission_keeps_locked_reference_check_before_immutable_write(case):
    manifest,value=fixtures()
    payload=value.model_dump(mode='json');fingerprint=payload_digest(value)
    source=manifest.model_dump(mode='json')
    if case=='reference-mismatch':source['pages'][0]['game_count']+=1
    steps=[('lock',(value.input_id,1),{'payload':source})]
    if case!='reference-mismatch':steps += [('insert',(value.input_id,1,value.page_id,payload,fingerprint),None),('saved',(value.input_id,1,value.page_id),{'digest':'sha256:different' if case=='immutable-conflict' else fingerprint})]
    reader,database,pending,events,Observed=reader_with_script(steps)
    if case=='accept':assert reader.page(Observed(value,'page'))=={'schema_version':1,'workspace_id':'fixture','accepted':True,'page_id':value.page_id}
    else:
        with pytest.raises(DomainError,match='analysis_page_mismatch' if case=='reference-mismatch' else 'immutable_page_conflict'):
            reader.page(Observed(value,'page'))
    expected=[('page-dump',False),('page-dump',False),'write','lock']
    if case!='reference-mismatch':expected+=['insert','saved']
    expected+=['commit','lookup-workspace'] if case=='accept' else ['rollback']
    assert events==expected and not pending and not database.active


@pytest.mark.parametrize('case',['accept','replay','page-set-incomplete','game-scope-mismatch'])
def test_finalization_retains_prior_read_receipt_lookup_lock_guards_write_and_receipt_last(case):
    manifest,page=fixtures()
    command=Command(command_id='finalize',action='finalize',expected_revision=1)
    payload={'input_id':manifest.input_id,**command.model_dump(mode='json')}
    fingerprint=digest(payload)
    source=manifest.model_dump(mode='json')
    games=page.model_dump(mode='json')['games']
    if case=='game-scope-mismatch':games[0]={**games[0],'game_id':'different'}
    result={'schema_version':1,'workspace_id':'fixture','accepted':True,'state':'accepted','input_id':manifest.input_id,'revision':1,'request_digest':fingerprint}
    replay={'payload':{'retained':'result'},'digest':fingerprint} if case=='replay' else None
    steps=[('input',(manifest.input_id,1),{'payload':source}),('games',(manifest.input_id,1),[{'payload':{'games':games}}]),
        ('command-lock',('analysis-command:finalize',),None),('receipt',('finalize',),replay)]
    if case!='replay':
        steps += [('input-lock',(manifest.input_id,1),{'payload':source}),('pages',(manifest.input_id,1),[] if case=='page-set-incomplete' else [{'page_id':page.page_id}])]
        if case=='accept':steps += [('accept',(manifest.input_id,1),None),('save',('finalize',fingerprint,result),None)]
    reader,database,pending,events,Observed=reader_with_script(steps)
    if case in ('page-set-incomplete','game-scope-mismatch'):
        with pytest.raises(DomainError,match='analysis_input_incomplete' if case=='page-set-incomplete' else 'analysis_game_scope_mismatch'):
            reader.finalize(manifest.input_id,Observed(command,'command'))
    else:
        actual=reader.finalize(manifest.input_id,Observed(command,'command'))
        assert actual==({'retained':'result','request_digest':fingerprint} if case=='replay' else result)
    expected=['lookup-_input','read','input','games','commit','write','lookup-_receipt',('command-dump',True),'command-lock','receipt']
    if case!='replay':expected+=['input-lock','pages']
    if case=='accept':expected+=['accept','lookup-workspace','lookup-_save_receipt',('command-dump',True),'save','commit']
    else:expected+=['commit' if case=='replay' else 'rollback']
    assert events==expected and not pending and not database.active


@pytest.mark.parametrize('case',['invalid-action','incomplete-read'])
def test_finalization_early_admission_failure_precedes_write_context(case):
    manifest,page=fixtures()
    command=Command(command_id='finalize',action='cancel' if case=='invalid-action' else 'finalize',expected_revision=1)
    steps=[] if case=='invalid-action' else [('input',(manifest.input_id,1),{'payload':manifest.model_dump(mode='json')}),('games',(manifest.input_id,1),[{'payload':{'games':[]}}])]
    reader,database,pending,events,_=reader_with_script(steps)
    with pytest.raises(DomainError,match='invalid_input_action' if case=='invalid-action' else 'analysis_input_incomplete'):
        reader.finalize(manifest.input_id,command)
    assert events==([] if case=='invalid-action' else ['lookup-_input','read','input','games','commit'])
    assert not pending and not database.active
