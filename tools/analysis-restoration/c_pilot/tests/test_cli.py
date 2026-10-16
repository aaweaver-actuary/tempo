"""Bounded pilot orchestration, with controlled installed public dependencies."""
import json
import sys
from types import SimpleNamespace
import pytest
from tempo_analysis_pilot import cli


@pytest.mark.parametrize('configured,maximum',[(True,1),(True,2),(False,1)])
def test_R3_partial_backlog_does_not_invoke_clientless_work(tmp_path,monkeypatch,configured,maximum):
    import tempo_intelligence
    import tempo_analysis_pilot.stockfish_client as adapter
    manifest={'input_id':'input','revision':1,'source_artifact_digest':'digest','producer_manifest':{'provenance':{'kind':'synthetic'}}}
    monkeypatch.setattr(cli,'normalize',lambda *args,**kwargs:(manifest,[]))
    calls=[];accepted=[]
    def invoke(argv,**kwargs):
        action=argv[2];calls.append(action)
        value={'unprocessed_count':10-len(accepted)} if action=='report' else {}
        return SimpleNamespace(returncode=0,stdout=json.dumps(value))
    monkeypatch.setattr(cli.subprocess,'run',invoke)
    monkeypatch.setattr(adapter,'connect',lambda path:object())
    monkeypatch.setattr(tempo_intelligence.Configuration,'environment',lambda:object())
    def work(configuration,run_id,bound,client):
        accepted.extend(range(bound));return {'jobs':bound,'run':{'progress':{'pending':10-bound}}}
    monkeypatch.setattr(tempo_intelligence,'run_analysis_work',work)
    config={'schema_version':1,'workspace_id':'fixture','a_export_directory':'unused','game_ids':['game'],'intelligence_command':['installed'], 'run_id':'run','max_jobs':maximum}
    if configured:config['engine_profile']='profile.json'
    path=tmp_path/'config.json';path.write_text(json.dumps(config))
    monkeypatch.setattr(sys,'argv',['pilot','--config',str(path),'--evidence',str(tmp_path/'evidence')])
    cli.main()
    assert calls.count('work')==(0 if configured else 1)
    assert len(accepted)==(maximum if configured else 0)
    assert calls[-2:]==['report','remaining']


def test_R3_startup_failure_is_not_success(tmp_path,monkeypatch):
    import tempo_analysis_pilot.stockfish_client as adapter
    import tempo_intelligence
    monkeypatch.setattr(cli,'normalize',lambda *args,**kwargs:({'input_id':'input','revision':1},[]))
    monkeypatch.setattr(cli.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout='{}'))
    monkeypatch.setattr(tempo_intelligence.Configuration,'environment',lambda:object())
    def unavailable(path):raise RuntimeError('controlled_startup_failure')
    monkeypatch.setattr(adapter,'connect',unavailable)
    config={'schema_version':1,'workspace_id':'fixture','a_export_directory':'unused','game_ids':['game'],'intelligence_command':['installed'],'run_id':'run','engine_profile':'profile'}
    path=tmp_path/'config.json';path.write_text(json.dumps(config))
    monkeypatch.setattr(sys,'argv',['pilot','--config',str(path),'--evidence',str(tmp_path/'evidence')])
    with pytest.raises(RuntimeError):cli.main()
    assert not (tmp_path/'evidence/pilot.json').exists()


@pytest.mark.parametrize('maximum',[0,True,1001,'1'])
def test_R3_invalid_job_bound_fails_before_worker_start(tmp_path,monkeypatch,maximum):
    path=tmp_path/'config.json';path.write_text(json.dumps({'schema_version':1,'max_jobs':maximum}))
    monkeypatch.setattr(sys,'argv',['pilot','--config',str(path),'--evidence',str(tmp_path/'evidence')])
    with pytest.raises(ValueError,match='invalid_analysis_work_bound'):cli.main()
