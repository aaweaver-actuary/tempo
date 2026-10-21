"""Synthetic source/engine finding assembly; no database or worker execution."""
from copy import deepcopy
from pathlib import Path

import pytest

from tempo_intelligence.analysis import findings as assembly
from tempo_intelligence.analysis.classify import ordered_positions, source_findings
from tempo_intelligence.analysis.models import Manifest, Page


def facts(game_id):
    fixtures=Path(__file__).resolve().parents[1]/'fixtures/analysis'
    manifest=Manifest.model_validate_json((fixtures/'manifest.json').read_text()).model_dump(mode='json')
    games=Page.model_validate_json((fixtures/'games-0.json').read_text()).model_dump(mode='json')['games']
    games=[game for game in games if game['game_id']==game_id]
    return {'payload':manifest},games


def result(move,score,version='synthetic'):
    return {'engine_version':version,'provenance':{
        'runtime_name':'synthetic','runtime_version':'test','implementation_version':'test'},
        'variations':[{'moves':[move],'depth':20,'score':{'kind':'centipawns','value':score}}]}


@pytest.mark.parametrize('compatible',[True,False])
def test_unscoped_engine_facts_preserve_unknown_association_and_profile_fence(compatible):
    source,games=facts('alternative')
    games[0]['contexts']=[]
    position=ordered_positions(games)[0]
    items,unknown=source_findings(source['payload'],games)
    run={'payload':{'ranking_source':'actual'},'requested_generation':2}
    rows=[{'accepted_root':'root','accepted_played':'played','position_id':'position','payload':position}]
    results={'root':result('e2e4',200),'played':result(position['actual_move'],0,
        version='synthetic' if compatible else 'different')}
    merged,unknown,incompatible=assembly.merge_findings('fixture',run,rows,results,source,games,items,unknown,[])
    assert {'game_id':'alternative','ply':None,'reason':'comparison_unavailable'} in unknown
    if compatible:
        assert incompatible==set() and len(merged)==1
        item=merged[0]
        assert item['category']=='engine_opportunity' and item['engine']['loss_cp']==200
        assert item['repertoire_id'] is None and item['policy_id'] is None and item['lineage'] is None
        assert item['actual_frequency'] is None and item['eligible_encounter_games'] is None
        assert item['uncertainty']==['repertoire_association_unknown']
    else:
        assert merged==[] and incompatible=={'position'}
        assert {'game_id':'alternative','ply':0,'reason':'incompatible_engine_profile'} in unknown


def test_accepted_advice_merges_identity_and_retains_engine_proposal_and_ranking(monkeypatch):
    source,games=facts('gap')
    source['payload']['producer']='a-analysis-export'
    items,unknown=source_findings(source['payload'],games)
    gap=items[0]
    original_evidence=list(gap['evidence_ids'])
    gap['proposal_anchor']={'source_id':'synthetic-source'}
    advice=deepcopy(gap)
    advice.update(source_evidence=[{'kind':'lichess','evidence_id':'population','frequency':.5}],
                  evidence_ids=['population','population'])
    proactive=deepcopy(advice)
    proactive.update(finding_id='proactive',supporting_game_count=0)
    monkeypatch.setattr(assembly,'source_advice',lambda manifest,records:[advice,proactive])
    position=next(p for p in ordered_positions(games) if p['ply']==2)
    rows=[{'accepted_root':'root','accepted_played':'played','position_id':'position','payload':position}]
    results={'root':result('d2d4',200),'played':result(position['actual_move'],0)}
    run={'payload':{'ranking_source':'actual'},'requested_generation':7}
    merged,unknown,incompatible=assembly.merge_findings('fixture',run,rows,results,source,games,items,unknown,[])
    retained=next(item for item in merged if item['finding_id']==gap['finding_id'])
    assert sum(item['finding_id']==gap['finding_id'] for item in merged)==1
    assert retained['evidence_ids']==sorted(set(original_evidence+['population']))
    assert retained['evidence_ids'].count('population')==1
    assert retained['source_evidence']==advice['source_evidence']
    assert retained['response_proposal']['accepted_lines']==[['d2d4']]
    assert retained['response_proposal']['proposal_id'].endswith(':7')
    assert retained['actions']['response_proposal']=={'available':True}
    assert retained['proposal_cost']==1 and incompatible==set()
    assert merged[-1]['finding_id']=='proactive'


def test_store_keeps_classification_then_evidence_capture_then_findings_merge(monkeypatch):
    from tempo_intelligence_database.adapters import analysis_store
    events=[]
    initial_findings=[]
    initial_unknown=[]
    records=[]
    def classify(manifest,games):
        events.append('classify')
        return initial_findings,initial_unknown
    def merge(workspace,run,rows,results,source,games,findings,unknown,source_records):
        events.append('merge')
        assert workspace=='fixture' and findings is initial_findings and unknown is initial_unknown
        assert source_records is records
        return findings,unknown,set()
    monkeypatch.setattr(analysis_store,'source_findings',classify)
    monkeypatch.setattr(assembly,'merge_findings',merge)
    class Owner:
        workspace_id='fixture'
        def transaction(self,**options):
            raise AssertionError('this composition proof must not open a database')
    store=analysis_store.AnalysisStore(Owner())
    def capture(identities,background=False):
        events.append('capture')
        assert identities==['accepted-source'] and background is True
        return records
    store.evidence_records=capture
    value=store._derive_evidence({'payload':{'source_evidence_ids':['accepted-source']}},[],{}, {'payload':{}},[])
    assert value==(initial_findings,initial_unknown,set())
    assert events==['classify','capture','merge']
