"""PR295 repair regressions; all evidence here is explicitly synthetic."""
from copy import deepcopy
import pytest
from tempo_intelligence.analysis.classify import rank_key
from tempo_intelligence.analysis.engine import comparison


def engine_result(move='e2e4', cp=200, **changes):
    result = {'engine_version':'profile', 'provenance':{
        'runtime_name':'node','runtime_version':'26.10.0','implementation_version':'implementation'},
        'variations':[{'rank':1,'moves':[move],'depth':20,
                       'score_perspective':'side_to_move','score':{'kind':'centipawns','value':cp}}]}
    result.update(changes)
    return result


def test_R1_global_priority_known_unknown_frequency_and_significance():
    def finding(identity, support=1, frequency=None, engine=None, cost=None):
        return dict(finding_id=identity,supporting_game_count=support,actual_frequency=frequency,engine=engine,proposal_cost=cost)
    items=[finding('unknown'),finding('known',frequency=.1)]
    assert sorted(items,key=rank_key)[0]['finding_id']=='known'
    assert items[0]['actual_frequency'] is None
    assert rank_key(finding('significant',engine={'significant':True,'loss_cp':200})) < rank_key(finding('weak',engine={'significant':False}))
    assert rank_key(finding('small',cost=1)) < rank_key(finding('large',cost=3))
    assert rank_key(finding('large',cost=3)) < rank_key(finding('unknown'))


@pytest.mark.parametrize('source',['actual','lichess','masters','maia'])
def test_R1_only_selected_population_ranks(source):
    items=[dict(finding_id=identity,supporting_game_count=1,actual_frequency=actual,
                source_evidence=[{'kind':kind,'evidence_id':kind+'-selected','frequency':frequency}
                                 for kind in ['lichess','masters','maia']])
           for identity,actual,frequency in [('actual-first',.9,.1),('source-first',.1,.9)]]
    chosen=sorted(items,key=lambda item:rank_key(item,source,source+'-selected'))
    assert chosen[0]['finding_id']==('actual-first' if source=='actual' else 'source-first')
    assert sorted(items,key=lambda item:rank_key(item,source,'unrelated'))[0]['finding_id']==('actual-first')


@pytest.mark.parametrize('field',['engine_version','runtime_name','runtime_version','implementation_version'])
def test_R5_incompatible_worker_identity_rejected(field):
    root=engine_result();played=engine_result('d2d4',0)
    if field=='engine_version':played[field]='different'
    else:played['provenance'][field]='different'
    with pytest.raises(ValueError,match='incompatible_engine_profile'):
        comparison(root,played,'d2d4')


def test_R5_completed_depth_and_typed_mate_preserved():
    root=engine_result();played=engine_result('d2d4',0)
    played['variations'][0]['depth']=19
    assert comparison(root,played,'d2d4')['status']=='insufficient_quality'
    played['variations'][0]['depth']=20
    root['variations'][0]['score']={'kind':'mate','value':3}
    compared=comparison(root,played,'d2d4')
    assert compared['significant'] and 'loss_cp' not in compared


@pytest.mark.parametrize('case',['none','one','ambiguous','duplicate','earlier','diverged','reentry'])
def test_R2_exact_association_and_input_order(case):
    from tempo_intelligence.analysis.classify import engine_association
    selections=[{'repertoire_id':name,'policy_id':name,'learner_color':'white','lineage':{'activation_status':'activated'}} for name in ['a','b']]
    context=dict(ply=2,repertoire_id='a',policy_id='a',route_established=True,after_divergence=False,reentry_authorized=False,
                 coverage='covered',status='matched',evidence_id='context-a')
    contexts=[] if case=='none' else [context]
    if case=='ambiguous':contexts.append({**context,'repertoire_id':'b','policy_id':'b','evidence_id':'context-b'})
    if case=='duplicate':contexts.append(deepcopy(context))
    if case=='earlier':context['ply']=0
    if case in {'diverged','reentry'}:context.update(after_divergence=True,reentry_authorized=case=='reentry')
    manifest={'selections':selections}
    position={'game_id':'game','ply':2,'learner_color':'white'}
    association=engine_association(manifest,{'contexts':contexts},position)
    manifest['selections'].reverse();contexts.reverse()
    assert association==engine_association(manifest,{'contexts':contexts},position)
    selected,candidates=association
    assert (selected is not None)==(case in {'one','duplicate','reentry'})
    if selected:assert selected['repertoire_id']=='a'
    if case=='ambiguous':assert len(candidates)==2


def test_R1_source_order_independence():
    import json
    from pathlib import Path
    from tempo_intelligence.analysis.classify import source_findings
    root=Path(__file__).parents[1]/'fixtures/analysis'
    manifest=json.loads((root/'manifest.json').read_text());games=json.loads((root/'games-0.json').read_text())['games']
    original=source_findings(manifest,games)[0]
    manifest['selections'].reverse();games.reverse()
    for game in games:game['contexts'].reverse()
    assert sorted(source_findings(manifest,games)[0],key=rank_key)==sorted(original,key=rank_key)


def test_R2_null_lineage_only_for_unscoped_engine_findings():
    from tempo_intelligence.analysis.classify import base_finding
    from tempo_intelligence.analysis.read_models import Finding
    import chess
    item=base_finding('engine_opportunity',{'workspace_id':'fixture','repertoire_id':None,'policy_id':None,'lineage':None},
        {'game_id':'game','source_revision':1,'fen':chess.STARTING_FEN,'actual_move':'e2e4','learner_color':'white','ply':0},'e2e4','test')
    item.update(finding_revision=1,evidence_revision='sha256:'+'0'*64,input_id='input',input_revision=1)
    assert Finding.model_validate(item).lineage is None
    with pytest.raises(ValueError,match='source_finding_lineage_required'):Finding.model_validate({**item,'category':'repertoire_miss'})


def test_R5_original_root_and_restriction_required():
    from tempo_intelligence.analysis.engine import validate_pair_jobs
    root={'fen':'original','moves':['e2e4'],'search_moves':[]}
    played={**root,'search_moves':['e7e5']}
    validate_pair_jobs(root,played,'e7e5')
    for change in [{'fen':'different'},{'moves':[]},{'search_moves':[]}]:
        with pytest.raises(ValueError,match='incompatible_engine_inputs'):validate_pair_jobs(root,{**played,**change},'e7e5')


def test_R1_R2_legacy_report_contract_retains_historical_fields():
    import json
    from pathlib import Path
    from tempo_intelligence.analysis.read_models import ReportPage
    from tempo_intelligence.analysis.classify import source_findings
    from tempo_intelligence.analysis.read_models import Finding
    root=Path(__file__).parents[1]/'fixtures/analysis'
    manifest=json.loads((root/'manifest.json').read_text());games=json.loads((root/'games-0.json').read_text())['games']
    finding=source_findings(manifest,games)[0][0]
    finding.update(finding_revision=1,evidence_revision='sha256:'+'0'*64,input_id='input',input_revision=1)
    item=Finding.model_validate(finding).model_dump(mode='json')
    payload={'schema_version':1,'workspace_id':'fixture','run_id':'legacy','generation':1,'status':'current','items':[item],'next_cursor':None}
    legacy=ReportPage.model_validate(payload)
    assert legacy.derivation_version==1 and legacy.ordering=='category-source-rank-semantic-v1'
    assert legacy.items[0].model_dump(mode='json')==item
    # Reviewed historical engine records sometimes borrowed lineage with null IDs.
    borrowed={**item,'category':'engine_opportunity','repertoire_id':None,'policy_id':None}
    assert Finding.model_validate(borrowed).lineage is not None
