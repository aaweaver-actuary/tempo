import json
from pathlib import Path
from tempo_intelligence import analysis_v1
Page, Manifest=analysis_v1.Page,analysis_v1.Manifest
from tempo_intelligence.identity import digest
root=Path.cwd()
lineage={'source_id':'synthetic-source','source_revision':1,'source_digest':'synthetic-source-digest','publication_id':'synthetic-publication','publication_generation':1,'activation_id':'synthetic-activation','activation_revision':1,'activation_status':'activated','serving_status':'unknown'}
selection={'repertoire_id':'rep','policy_id':'policy','learner_color':'white','lineage':lineage}
def context(ply,**values):
 return {'ply':ply,'repertoire_id':'rep','policy_id':'policy','evidence_id':f'synthetic-{ply}',**values}
import chess
base={'source_revision':1,'source_digest':'synthetic-game-digest','learner_color':'white','starting_fen':chess.STARTING_FEN,'played_at':None}
games=[{**base,'game_id':'miss','moves':['g1f3','e7e5'],'contexts':[context(0,status='missed',card_id='existing-card',decision_id='existing-decision',expected_moves=['d2d4','e2e4'],route_established=True,coverage='covered',delivery_status='accepted')]},
 {**base,'game_id':'gap','moves':['e2e4','a7a6','g1f3','b7b6','d2d4'],'contexts':[context(0,status='matched',expected_moves=['e2e4'],route_established=True,coverage='covered'),context(2,coverage='uncovered',route_established=True,scope_complete=True,scope_revision=1,opponent_ply=1,opponent_move='a7a6',route_ids=['route']),context(4,coverage='uncovered',route_established=True,scope_complete=True,scope_revision=1,opponent_ply=3,opponent_move='b7b6',route_ids=['route'],after_divergence=True)]},
 {**base,'game_id':'alternative','moves':['d2d4','d7d5'],'contexts':[context(0,status='matched',expected_moves=['e2e4','d2d4'],route_established=True,coverage='covered')]}]
page=Page.model_validate({'schema_version':1,'input_id':'synthetic-C','revision':1,'page_id':'games-0','games':games}).model_dump(mode='json')
manifest=Manifest.model_validate({'schema_version':1,'workspace_id':'fixture','input_id':'synthetic-C','revision':1,'producer':'synthetic','producer_version':'c-fixture-v1','source_artifact_digest':digest({'synthetic':'explicitly not an A producer acceptance'}),'completeness':'complete','game_ids':[g['game_id'] for g in games],'selections':[selection],'pages':[{'page_id':'games-0','digest':digest(page),'game_count':len(games)}]}).model_dump(mode='json')
destination=root/'services/intelligence/fixtures/analysis'
for name,value in [('manifest',manifest),('games-0',page),('run',{'schema_version':1,'workspace_id':'fixture','run_id':'synthetic-C','revision':1,'input_id':'synthetic-C','input_revision':1,'position_budget':250})]:
 (destination/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
