"""Validate historical synthetic scenario claims without importing their builder."""
from datetime import datetime
from tempo_evidence_math import (OpeningRankingError, presentation_occurrences,
    MoveProbabilityDistribution, move_universe, opening_position_key)


def validate_fixture_content(document,coverage):
    def require(condition,message):
        if not condition:raise OpeningRankingError('invalid_corpus',message)
    source=document['source_versions'];candidates=document['candidates'];policy=document['policy_id']
    require(source.get('fixture_version')==1,'Corpus requires explicit fixture version')
    supporting={card['card_id']:card for card in source['supporting_cards']}
    candidate_ids={candidate['card_id'] for candidate in candidates}
    require(not candidate_ids & supporting.keys(),'Supporting history must not be ranked candidates')
    for candidate in candidates:
        metadata=candidate['metadata'];moves=metadata['moves']
        occurrences=presentation_occurrences(policy,{'id':candidate['card_id'],'revision':metadata['revision'],
            'trained_color':'white','start_fen':metadata['start_fen'],'moves_json':_json_text(moves)})
        require(len(occurrences)==len(metadata['decisions']),'Candidate decision inventory disagrees with legal presentation')
        for occurrence,decision in zip(occurrences,metadata['decisions']):
            position=opening_position_key(occurrence['decision_fen'])
            require(decision['position_key']==position and decision['move_uci']==occurrence['expected_uci']
                and tuple(decision['prefix'])==tuple(moves[:occurrence['move_offset']]),'Invalid historical decision geometry')
            require(decision['decision_id']=='white:'+position+':'+decision['move_uci'],'Invalid historical synthetic decision label')
        for route in metadata['routes']:
            parent=supporting[route['parent_card_id']]
            require(parent['policy_id']==policy and len(parent['moves'])<len(moves)
                and moves[:len(parent['moves'])]==parent['moves'],'Route parent is not a true same-policy supporting prefix')
    evidence=source['opponent_evidence']
    for item in evidence:
        distribution=MoveProbabilityDistribution.model_validate({'position':move_universe(item['fen']).model_dump(),
            'moves':item['moves'],'unknown_mass':item['unknown_mass']})
        require(distribution.position.fen_key==item['position_key'],'Evidence position identity mismatch')
        legal=set(distribution.position.legal_moves)
        require(set(item['authored_replies'])<=legal and item['outside_reply'] in legal
            and item['outside_reply'] not in item['authored_replies'],'Invalid authored/outside legal reply inventory')
        probabilities={move.move_uci:move.probability for move in distribution.moves}
        for move in item['unassigned_authored_replies']:
            require(move in item['authored_replies'] and probabilities[move] is None and distribution.unknown_mass>0,
                'Unassigned authored reply must retain unknown mass')
        for bound in item['bounds']:
            require(bound['move_uci'] in legal and 0<=bound['lower']<bound['upper']<=1,'Invalid historical sensitivity annotation')
    moves=[candidate['metadata']['moves'] for candidate in candidates]
    if 'common_italian' in coverage:require(any(len(line)>4 and line[4]=='f1c4' for line in moves),'Missing common Italian geometry')
    if 'common_ruy_lopez' in coverage:require(any(len(line)>4 and line[4]=='f1b5' for line in moves),'Missing common Ruy geometry')
    if 'shared_prefixes' in coverage:require(any(len(candidate['metadata']['routes'])>1 for candidate in candidates),'Missing shared route representations')
    if 'rare_sidelines' in coverage:require(any(line[:2]==['e2e4','b7b6'] for line in moves),'Missing rare sideline')
    if 'opponent_deviations' in coverage:require(any(line[:2]==['e2e4','c7c5'] for line in moves),'Missing opponent deviation')
    if 'multiple_cohorts' in coverage:
        cohorts={item['cohort_id'] for item in evidence}
        require(len(cohorts)>=2 and cohorts==set(source['cohorts']),'Missing distinct cohort evidence')
        require(len({_json_text(value) for value in source['cohorts'].values()})>=2,'Cohort definitions must differ')
    if 'exact_probabilities' in coverage:require(all(item['unknown_mass']==0 and all(move['probability'] is not None for move in item['moves']) for item in evidence),'Exact scenario has unassigned probabilities')
    if 'bounded_probabilities' in coverage:require(any(item['bounds'] for item in evidence),'Missing probability bound annotations')
    if 'unknown_unassigned_mass' in coverage:require(any(item['unassigned_authored_replies'] and item['unknown_mass']>0 for item in evidence),'Missing unknown authored mass')
    support=list(supporting.values())
    if 'newly_introduced_cards' in coverage:require(any(card['introduced_at'].startswith(document['study_day']) for card in support),'Missing introduced supporting history')
    if 'mature_cards' in coverage:require(any(card['state']=='mature' for card in support),'Missing mature supporting history')
    if 'recently_failed_decisions' in coverage:
        require(any(card.get('observation',{}).get('outcome')=='incorrect'
            and datetime.fromisoformat(card['observation']['observed_at'].replace('Z','+00:00'))<=datetime.fromisoformat(document['as_of'].replace('Z','+00:00')) for card in support),'Missing current failed-decision history')
    if 'cumulative_prefix_cards' in coverage:require(any(len(candidate['metadata']['decisions'])>=5 for candidate in candidates),'Missing cumulative-prefix evidence')


def _json_text(value):
    import json
    return json.dumps(value,sort_keys=True,separators=(',',':'))
