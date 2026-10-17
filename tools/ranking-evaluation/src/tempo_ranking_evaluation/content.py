"""Validate historical synthetic scenario claims without importing their builder."""
from datetime import datetime
from tempo_evidence_math import (OpeningRankingError, presentation_occurrences,
    MoveProbabilityDistribution, move_universe, opening_position_key)


def validate_fixture_content(document,coverage):
    def require(condition,message):
        if not condition:raise OpeningRankingError('invalid_corpus',message)
    source=document['source_versions'];candidates=document['candidates'];policy=document['policy_id']
    require(source.get('fixture_version')==1,'Corpus requires explicit fixture version')
    def timestamp(value):
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        require(parsed.tzinfo is not None,'Historical evidence requires timezone-aware timestamps')
        return parsed
    as_of=timestamp(document['as_of'])
    start_fen=candidates[0]['metadata']['start_fen']
    def legal_decisions(card_id,metadata,*,support=False):
        moves=metadata['moves']
        occurrences=presentation_occurrences(policy,{'id':card_id,'revision':metadata.get('revision',1) if support else metadata['revision'],
            'trained_color':'white','start_fen':metadata.get('start_fen',start_fen),'moves_json':_json_text(moves)})
        legal=[(opening_position_key(item['decision_fen']),item['expected_uci'],tuple(moves[:item['move_offset']]),item['move_offset']+1)
               for item in occurrences]
        decisions=metadata['decisions']
        require(bool(decisions),'Missing legal supporting decision evidence' if support else 'Missing candidate decision evidence')
        if not support:require(len(occurrences)==len(decisions),'Candidate decision inventory disagrees with legal presentation')
        seen=set()
        for index,decision in enumerate(decisions):
            geometry=(decision['position_key'],decision['move_uci'],tuple(decision['prefix']),decision['ply'])
            require(geometry in legal and geometry not in seen,'Invalid historical decision geometry')
            if not support:require(geometry==legal[index],'Candidate decisions must retain presentation order')
            require(decision['decision_id']=='white:'+decision['position_key']+':'+decision['move_uci'],'Invalid historical synthetic decision label')
            seen.add(geometry)
        return {decision['decision_id'] for decision in decisions},occurrences
    support_cards=source['supporting_cards']
    supporting={card['card_id']:card for card in support_cards}
    require(len(supporting)==len(support_cards),'Duplicate supporting card identity')
    candidate_ids={candidate['card_id'] for candidate in candidates}
    require(not candidate_ids & supporting.keys(),'Supporting history must not be ranked candidates')
    for card in supporting.values():
        require(card['policy_id']==policy,'Supporting history belongs to a foreign policy')
        decisions,_=legal_decisions(card['card_id'],card,support=True)
        require(any(card['moves']==candidate['metadata']['moves'][:len(card['moves'])]
            and card.get('start_fen',start_fen)==candidate['metadata']['start_fen']
            and len(card['moves'])<len(candidate['metadata']['moves']) for candidate in candidates),
            'Supporting history must support an actual candidate prefix')
        introduced=timestamp(card['introduced_at'])
        require(introduced<=as_of and card['state'] in ('learning','mature'),'Invalid supporting history state/time')
        if 'observation' in card:
            observation=card['observation']
            require(observation['decision_id'] in decisions,'Observation decision is not owned by its supporting card')
            require(observation['outcome'] in ('correct','incorrect')
                and introduced<=timestamp(observation['observed_at'])<=as_of,'Invalid supporting observation outcome/time')
    authored={}
    for candidate in candidates:
        metadata=candidate['metadata'];moves=metadata['moves']
        _,occurrences=legal_decisions(candidate['card_id'],metadata)
        for occurrence in occurrences:
            reply_offset=occurrence['move_offset']+1
            if reply_offset<len(moves):
                authored.setdefault(opening_position_key(occurrence['cut_fen']),set()).add(moves[reply_offset])
        for route in metadata['routes']:
            parent=supporting[route['parent_card_id']]
            require(parent['policy_id']==policy and len(parent['moves'])<len(moves)
                and moves[:len(parent['moves'])]==parent['moves'],'Route parent is not a true same-policy supporting prefix')
    evidence=source['opponent_evidence']
    require(isinstance(evidence,list) and bool(evidence),'Missing substantive opponent probability evidence')
    evidence_ids=set();positions=set()
    for item in evidence:
        require(item['cohort_id'] in source['cohorts'],'Opponent evidence belongs to a foreign cohort')
        require(item['evidence_id'] and item['evidence_id'] not in evidence_ids,'Missing/duplicate opponent evidence identity')
        evidence_ids.add(item['evidence_id'])
        identity=(item['cohort_id'],item['position_key'])
        require(identity not in positions,'Duplicate opponent evidence position/cohort')
        positions.add(identity)
        require(item['source_version'] and item['provenance'] and timestamp(item['captured_at'])<=as_of,
            'Missing or future opponent evidence provenance')
        distribution=MoveProbabilityDistribution.model_validate({'position':move_universe(item['fen']).model_dump(),
            'moves':item['moves'],'unknown_mass':item['unknown_mass']})
        require(distribution.position.fen_key==item['position_key'],'Evidence position identity mismatch')
        legal=set(distribution.position.legal_moves)
        require(bool(item['authored_replies']) and len(set(item['authored_replies']))==len(item['authored_replies'])
            and set(item['authored_replies'])==authored.get(item['position_key']),
            'Opponent evidence does not match actual authored candidate replies')
        require(set(item['authored_replies'])<=legal and item['outside_reply'] in legal
            and item['outside_reply'] not in item['authored_replies'],'Invalid authored/outside legal reply inventory')
        probabilities={move.move_uci:move.probability for move in distribution.moves}
        for move in item['unassigned_authored_replies']:
            require(move in item['authored_replies'] and probabilities[move] is None and distribution.unknown_mass>0,
                'Unassigned authored reply must retain unknown mass')
        for bound in item['bounds']:
            require(bound['move_uci'] in legal and 0<=bound['lower']<bound['upper']<=1,'Invalid historical sensitivity annotation')
    evidence_cohorts={cohort for cohort,position in positions}
    require(positions=={(cohort,position) for cohort in evidence_cohorts for position in authored},
        'Missing opponent evidence for authored positions/cohorts')
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
    if 'exact_probabilities' in coverage:require(bool(evidence) and all(item['unknown_mass']==0 and all(move['probability'] is not None for move in item['moves']) for item in evidence),'Exact scenario has unassigned probabilities')
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
