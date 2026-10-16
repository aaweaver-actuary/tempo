"""Source-specific advice from already accepted immutable evidence; no acquisition."""
from datetime import datetime,timedelta,timezone
from collections import defaultdict
import chess
from .classify import base_finding,explorer_coverage,maia_distribution


def source_advice(manifest,records):
    result=[]
    by_position=defaultdict(list)
    for record in records:by_position[record["descriptor"]["position_key"]].append(record)
    for scope in manifest.get('coverage_scopes',[]):
        selection=next((s for s in manifest['selections'] if (s['repertoire_id'],s['policy_id'],s['learner_color'])==(scope['repertoire_id'],scope['policy_id'],scope['learner_color'])),None)
        if not selection or not scope['complete'] or selection['lineage']['activation_status']!='activated':continue
        position_key=' '.join(chess.Board(scope['fen']).fen(en_passant='legal').split()[:4])
        for record in by_position[position_key]:
            descriptor=record['descriptor'];kind=descriptor['kind'];payload=record['payload']
            if descriptor['position_key']!=position_key:continue
            accepted=datetime.fromisoformat(record['accepted_at']) if isinstance(record['accepted_at'],str) else record['accepted_at']
            fresh=accepted+timedelta(days=1)>datetime.now(timezone.utc)
            frequencies={};coverage=None
            if kind in {'lichess','masters'}:
                coverage=explorer_coverage(payload,scope['covered_replies'])
                if payload['total_games']>=200:
                    frequencies={m['uci']:m['games']/payload['total_games'] for m in payload['moves']}
            elif kind=='maia':
                frequencies=maia_distribution(payload['job'],payload['result'],scope['legal_moves'])
                coverage={'lower':sum(p for move,p in frequencies.items() if move in scope['covered_replies']),'upper':sum(p for move,p in frequencies.items() if move in scope['covered_replies']),'status':'model_output'}
            for move,frequency in frequencies.items():
                if move in scope['covered_replies'] or frequency<0.05:continue
                board=chess.Board(scope['fen']);board.push_uci(move)
                position={'game_id':None,'source_revision':selection['lineage']['source_revision'],'ply':None,'fen':board.fen(en_passant='fen'),'actual_move':None,'learner_color':scope['learner_color']}
                item=base_finding('practical_gap',{**selection,'workspace_id':manifest['workspace_id']},position,move,'population_uncovered_response' if kind!='maia' else 'model_uncovered_response')
                item.update(supporting_games=[],supporting_game_count=0,eligible_encounter_games=None,actual_frequency=None,gap_kind='proactive',opponent_move=move,evidence_status='current' if fresh else 'stale',evidence_ids=[scope['evidence_id'],record['evidence_id']],source_evidence=[{'kind':kind,'frequency':frequency,'evidence_id':record['evidence_id'],'descriptor':descriptor,'coverage':coverage,'freshness':'current' if fresh else 'stale','meaning':'model_probability' if kind=='maia' else 'source_sample_frequency','limitations':payload.get('limitations',[]),'accepted_at':accepted.isoformat(),'total_games':payload.get('total_games'),'unallocated_games':payload.get('unallocated_games'),'sample_complete':payload.get('sample_complete')}])
                result.append(item)
    grouped={}
    for item in result:
        old=grouped.get(item['finding_id'])
        if old:
            old['source_evidence']+=item['source_evidence'];old['evidence_ids']=sorted(set(old['evidence_ids']+item['evidence_ids']))
        else:grouped[item['finding_id']]=item
    return list(grouped.values())
