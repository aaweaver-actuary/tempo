"""Explicit installed-B acceptance; selected only with an actual worker profile."""
from pathlib import Path
import json
import os
import pytest
import chess
from tempo_intelligence.analysis.models import Manifest,Page,RunRequest,Command
from tempo_intelligence_database.adapters.analysis_store import AnalysisStore
from tempo_intelligence.analysis.worker import work
from tempo_intelligence.identity import digest


def test_actual_B_durable_same_root_result_acceptance(state):
    from tempo_analysis_pilot import connect
    owner=AnalysisStore(state[0])
    page=Page.model_validate({'schema_version':1,'input_id':'actual-B-synthetic-game','revision':1,'page_id':'games','games':[{'game_id':'missed-mate','source_revision':1,'source_digest':'explicit-synthetic-game','learner_color':'white','starting_fen':'7k/5Q2/6K1/8/8/8/8/8 w - - 0 1','moves':['f7e6'],'contexts':[]}]})
    manifest=json.loads((Path(__file__).parents[1]/'fixtures/analysis/manifest.json').read_text());manifest.update(input_id=page.input_id,game_ids=['missed-mate'],pages=[{'page_id':'games','digest':digest(page.model_dump(mode='json')),'game_count':1}])
    owner.manifest(Manifest.model_validate(manifest));owner.page(page);owner.finalize(page.input_id,Command(command_id='finalize-B',action='finalize',expected_revision=1))
    owner.submit(RunRequest(schema_version=1,workspace_id='fixture',run_id='actual-B',revision=1,input_id=page.input_id,input_revision=1,position_budget=1))
    output=Path(os.environ['TEMPO_C_BROWSER_FIXTURE_FILE']).parent/'actual-B.json'
    try:result=work(owner,'actual-B',2,client=connect(Path(os.environ['TEMPO_C_STOCKFISH_PROFILE'])))
    except Exception:
        with state[0].transaction(read_only=True) as db:
            events=db.execute('SELECT payload FROM analysis_execution_events').fetchall()
            accepted=db.execute('SELECT payload FROM analysis_results').fetchall()
        output.write_text(json.dumps({'case':'C_actual_B_durable','passed':False,'events':events,'accepted':accepted},indent=2)+'\n')
        raise
    report=owner.report('actual-B')
    assert result['jobs']==2
    assert report['insufficient_quality_count']==0 and report['unprocessed_count']==0
    finding=next(item for item in report['items'] if item['category']=='engine_opportunity')
    assert finding['engine']['significant'] and finding['engine']['best']['score']['kind']=='mate'
    assert finding['engine']['best']['depth']==finding['engine']['played']['depth']>=18
    with state[0].transaction(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM analysis_results').fetchone()['count']==2
    with state[0].transaction(read_only=True) as db:
        executions=db.execute('SELECT payload FROM analysis_executions').fetchall()
        accepted=db.execute('SELECT payload FROM analysis_results').fetchall()
    output=Path(os.environ['TEMPO_C_BROWSER_FIXTURE_FILE']).parent/'actual-B.json'
    output.write_text(json.dumps({'case':'C_actual_B_durable','passed':True,'synthetic_game':True,'worker_profile':json.loads(Path(os.environ['TEMPO_C_STOCKFISH_PROFILE']).read_text()),'report':report,'executions':executions,'accepted':accepted,'work_receipt':result},indent=2)+'\n')
