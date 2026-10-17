from pathlib import Path
import json
import os
import subprocess
import sys
from unittest.mock import patch
import pytest
from tempo_evidence_math import canonical_json, load_valuation_snapshot, evaluate_opening_card_valuation, valuation_snapshot_document, PrecomputedValuationScorer, load_snapshot, OpeningRankingError
from tempo_ranking_evaluation import score_snapshot, replay_opening_card_valuation, rank
from tempo_ranking_evaluation import cli, harness
from tempo_ranking_evaluation.resources import installed_corpus_directory

FIXTURES=Path(__file__).parent/'fixtures'
CLI=Path(sys.executable).parent/'tempo-evaluate-opening-rankings'


def run(*arguments,env=None):
    return subprocess.run([sys.executable,'-I','-B',str(CLI),*map(str,arguments)],capture_output=True,text=True,env=env,timeout=20)


def test_computed_snapshot_replays_without_recalculation_and_reserved_bundle_conflict():
    document=json.loads((FIXTURES/'valuation-v2.json').read_text())
    calculation=evaluate_opening_card_valuation(load_valuation_snapshot(document))
    snapshot=valuation_snapshot_document(calculation)
    candidates,context,bundles,baseline=harness.evaluate_snapshot(snapshot)
    assert baseline==bundles[0]['name']+'@'+bundles[0]['version']
    assert all(bundle['name']!='production-order' for bundle in bundles)
    report=score_snapshot(document,top_k=(1,))
    assert report['evaluations'][0]['spearman']==-1
    assert report['evaluations'][0]['top_k']['1']['overlap']==0
    assert {row['card_id']:row['score'] for row in report['evaluations'][0]['rows']}==pytest.approx({'multi':.168,'single':.189})
    with patch('tempo_ranking_evaluation.replay.evaluate_opening_card_valuation',side_effect=AssertionError('replay recomputed mathematics')):
        replay=replay_opening_card_valuation(snapshot,top_k=(1,))
    assert replay['evaluations']==report['evaluations']
    assert replay['replay_basis']!=report['replay_basis']


def test_installed_cli_compare_matches_documented_example_and_mathematical_score():
    result=run('compare','--snapshot',FIXTURES/'opening-ranking-snapshot.json','--top-k',2)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==json.loads((FIXTURES/'opening-ranking-comparison.json').read_text())
    result=run('score','--snapshot',FIXTURES/'valuation-v2.json','--top-k',1)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['evaluations'][0]['spearman']==-1


@pytest.mark.parametrize('arguments',[
 (),('capture',),('corpus','--scenario','missing'),('corpus','--top-k','0'),('corpus','--top-k','1','--top-k','1'),
 ('corpus','--top-k',str(2**53)),('compare','--snapshot','/nonexistent/snapshot.json')])
def test_installed_cli_errors_have_exit_two_json_stderr_and_no_report(arguments):
    result=run(*arguments)
    assert result.returncode==2
    assert result.stdout==''
    assert json.loads(result.stderr)['status']=='error'


def test_cli_protects_input_existing_and_alias_outputs_and_leaves_no_partial_file(tmp_path):
    source=tmp_path/'snapshot.json';source.write_bytes((FIXTURES/'opening-ranking-snapshot.json').read_bytes())
    before=source.read_bytes()
    for output in (source,tmp_path/'alias.json'):
        if output!=source:output.symlink_to(source)
        result=run('compare','--snapshot',source,'--output',output)
        assert result.returncode==2 and result.stdout=='' and source.read_bytes()==before
    output=tmp_path/'new-report.json'
    result=run('corpus','--scenario','missing','--output',output)
    assert result.returncode==2 and not output.exists() and result.stdout==''
    result=run('corpus','--scenario','italian-common','--output',output)
    assert result.returncode==0 and result.stdout=='' and output.is_file()
    saved=output.read_bytes()
    result=run('corpus','--output',output)
    assert result.returncode==2 and output.read_bytes()==saved


def test_cli_serialized_ceiling_applies_before_stdout_and_file_creation(tmp_path,capsys,monkeypatch):
    monkeypatch.setattr(cli,'MAX_REPORT_BYTES',16)
    for path in (None,tmp_path/'report.json'):
        with pytest.raises(OpeningRankingError,match='64 MiB'):cli.emit_report({'metadata':'x'*100},path)
        assert capsys.readouterr().out==''
        if path:assert not path.exists()


def test_cli_write_failure_removes_only_new_partial_output(tmp_path,monkeypatch):
    output=tmp_path/'report.json';original=Path.open
    class FailedWriter:
        def __enter__(self):
            self.stream=original(output,'xb');return self
        def write(self,value):
            self.stream.write(value[:3]);raise OSError('injected full disk')
        def __exit__(self,*arguments):self.stream.close()
    monkeypatch.setattr(Path,'open',lambda self,*args,**kwargs:FailedWriter() if self==output else original(self,*args,**kwargs))
    with pytest.raises(OSError):cli.emit_report({'complete':True},output)
    assert not output.exists()


def test_cli_compare_amplification_is_rejected_before_rank(tmp_path,monkeypatch,capsys):
    source=json.loads((FIXTURES/'opening-ranking-snapshot.json').read_text())
    source['candidates'][0]['metadata']['large']='x'*(3*1024*1024)
    # Malformed digest must not be reached before an already excessive expansion.
    path=tmp_path/'large.json';path.write_text(canonical_json(source))
    monkeypatch.setattr(cli,'rank',lambda *a,**k:pytest.fail('rank before bounds'))
    assert cli.main(['compare','--snapshot',str(path),'--top-k','1','--top-k','2','--top-k','3','--top-k','4'])==2
    captured=capsys.readouterr();assert captured.out==''
    assert json.loads(captured.err)['code']=='limit_exceeded'


def test_complete_corpus_validates_unselected_scenario_content_before_rank(tmp_path,monkeypatch):
    corpus=installed_corpus_directory()
    for file in corpus.glob('*.json'):(tmp_path/file.name).write_bytes(file.read_bytes())
    path=tmp_path/'ruy-common.json';document=json.loads(path.read_text())
    document['candidates'][0]['metadata']['moves'][0]='a1a8'
    from hashlib import sha256
    document['snapshot_id']=sha256(canonical_json({k:v for k,v in document.items() if k!='snapshot_id'}).encode()).hexdigest()
    path.write_text(canonical_json(document))
    manifest=json.loads((tmp_path/'manifest.json').read_text())
    next(entry for entry in manifest['scenarios'] if entry['id']=='ruy-common')['snapshot_id']=document['snapshot_id']
    (tmp_path/'manifest.json').write_text(canonical_json(manifest))
    monkeypatch.setattr(harness,'rank',lambda *a,**k:pytest.fail('invalid unselected corpus reached rank'))
    with pytest.raises(OpeningRankingError):harness.run_corpus(tmp_path/'manifest.json',scenario_ids=['italian-common'])


def test_installed_cli_bytes_are_hash_seed_timezone_independent_for_score():
    outputs=[]
    for seed,zone in (('1','UTC'),('22','Pacific/Honolulu'),('random','Asia/Tokyo'),('77','UTC')):
        result=run('score','--snapshot',FIXTURES/'valuation-v2.json',env=dict(os.environ,PYTHONHASHSEED=seed,TZ=zone))
        assert result.returncode==0,result.stderr
        outputs.append(result.stdout)
    assert len(set(outputs))==1


def test_corpus_resolved_symlink_cannot_escape_directory(tmp_path):
    corpus=installed_corpus_directory()
    for file in corpus.glob('*.json'):(tmp_path/file.name).write_bytes(file.read_bytes())
    outside=tmp_path.parent/(tmp_path.name+'-outside.json')
    outside.write_bytes((tmp_path/'italian-common.json').read_bytes())
    (tmp_path/'italian-common.json').unlink();(tmp_path/'italian-common.json').symlink_to(outside)
    try:
        with pytest.raises(OpeningRankingError,match='within'):harness.run_corpus(tmp_path/'manifest.json')
    finally:outside.unlink()


@pytest.mark.parametrize('count',[0,33])
def test_corpus_scenario_count_is_bounded_before_ranking(tmp_path,count):
    document=json.loads((installed_corpus_directory()/'manifest.json').read_text())
    document['scenarios']=(document['scenarios']*6)[:count]
    path=tmp_path/'manifest.json';path.write_text(canonical_json(document))
    with pytest.raises(OpeningRankingError,match='1 and 32'):harness.run_corpus(path)


def test_frozen_precomputed_scorer_identity_cannot_be_changed():
    from dataclasses import FrozenInstanceError
    from tempo_ranking_evaluation import PrecomputedScoreScorer
    scorer=PrecomputedScoreScorer({'name':'fixed','version':'1','scores':{'card':{'score':None,'reason':'unknown'}}})
    with pytest.raises(FrozenInstanceError):scorer.name='changed'
