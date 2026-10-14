"""Read-only snapshot interpretation and inspectable plan production."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from zoneinfo import ZoneInfo
import chess
from .frozen_maia_math import anchor, card_decisions, represented_reach, greedy_order, content_fingerprint, VERSION
from .opening_segmentation import opening_position_key, decision_identity, stable_key


def recover_lines(lines, prefix_evidence=(), anchor_positions=None):
    """Stitch only existing same-policy histories reaching a stored source root."""
    rooted, missing, index = [], [], defaultdict(set)
    diagnostics = []
    # Existing published graph/card prefixes are ancestry evidence, never new
    # search routes. Only exact legal positions within the same policy can join.
    for evidence in prefix_evidence:
        if not evidence['start_fen'].startswith(chess.STARTING_FEN.split(' w ')[0]+' w '):
            continue
        board=chess.Board(evidence['start_fen'])
        if opening_position_key(board.fen())!=opening_position_key(chess.STARTING_FEN):
            continue
        prefix=[]
        positions=[]
        try:
            for uci in json.loads(evidence['moves_json']):
                positions.append((opening_position_key(board.fen()),tuple(prefix)))
                board.push_uci(uci);prefix.append(uci)
            positions.append((opening_position_key(board.fen()),tuple(prefix)))
            for position,history in positions:
                index[evidence['repertoire_id'],position].add(history)
        except ValueError:
            continue
    for line in lines:
        board = chess.Board(line['start_fen'])
        starting_key = opening_position_key(board.fen())
        prefix = []
        positions = []
        try:
            if not board.is_valid():raise ValueError('Invalid source position')
            for uci in json.loads(line['moves_json']):
                positions.append((opening_position_key(board.fen()),tuple(prefix)))
                board.push_uci(uci)
                prefix.append(uci)
            positions.append((opening_position_key(board.fen()),tuple(prefix)))
            if starting_key == opening_position_key(chess.STARTING_FEN):
                for position,history in positions:index[line['repertoire_id'],position].add(history)
                rooted.append(line)
            elif starting_key == (anchor_positions or {}).get(line['repertoire_id']):
                rooted.append(line)  # No pre-anchor ancestry is needed for FEN conditioning.
            else:missing.append(line)
        except ValueError as error:
            diagnostics.append({'line_id': line['id'], 'reason': str(error)})
    for line in missing:
        histories = index[line['repertoire_id'], opening_position_key(line['start_fen'])]
        if not histories:
            diagnostics.append({'line_id': line['id'], 'reason': 'Missing initial/anchor lineage'})
            continue
        for history in sorted(histories):
            rooted.append({**line, 'id': line['id'], 'start_fen': chess.STARTING_FEN,
                           'moves_json': json.dumps([*history, *json.loads(line['moves_json'])]),
                           'recovered_prefix': list(history)})
    return rooted, diagnostics


def inventory(snapshot, config):
    scenarios = {row['repertoire_id']: row for row in config['scenarios']}
    if len(scenarios)!=len(config['scenarios']) or any(not math.isfinite(row['weight']) or row['weight']<0 for row in scenarios.values()) or abs(math.fsum(row['weight'] for row in scenarios.values())-1)>1e-9:
        raise ValueError('Ambiguous scenario identities or invalid weights')
    repertoires = {row['id']: row for row in snapshot['repertoires']}
    for identifier, scenario in scenarios.items():
        if identifier not in repertoires or repertoires[identifier]['name'] != scenario['name']:
            raise ValueError(f'Unverified repertoire mapping: {identifier}')
        colors = {row['trained_color'] for row in snapshot['lines'] if row['repertoire_id'] == identifier}
        if colors != {scenario['color']}:
            raise ValueError(f'Ambiguous trained color: {identifier}')
        anchor(scenario)
    memberships = defaultdict(set)
    for row in snapshot['memberships']:
        memberships[row['card_id']].add(row['repertoire_id'])
    exposure = defaultdict(set)
    for row in snapshot['reviews']:
        exposure[row['card_id']].add('review')
    for row in snapshot['attempts']:
        exposure[row['card_id']].add('presentation/attempt')
    for row in snapshot['presented_teaching']:
        exposure[row['card_id']].add('teaching')
    for row in snapshot['origins']:
        if row.get('issued_as_head') or row['attempt_failed'] or row['review_result_json'] or row['last_status'] == 'complete':
            exposure[row['card_id']].add('issued/attempted')
    for row in snapshot['queue']:
        if row['attempt_state'] not in ('clean', 'unstarted') or row['attempt_failed'] or row['review_result_json'] or row['status'] == 'complete':
            exposure[row['card_id']].add('queue attempt')
    deleted = {row['card_id'] for row in snapshot['deleted']}
    blocked = defaultdict(set)
    for row in snapshot['blocks']:
        blocked[row['card_id']].add(row['repertoire_id'])
    routes = defaultdict(list)
    line_creation = {row['id']:row['created_at'] for row in snapshot['lines']}
    for row in snapshot['graphs']:
        routes[row['card_id']].append(row)
    queued = {row['card_id'] for row in snapshot['queue'] if row['status'] in ('queued','blocked')}
    day = datetime.fromisoformat(snapshot.get('cutoff', datetime.now(timezone.utc).isoformat())).astimezone(ZoneInfo(config.get('study_timezone','America/New_York'))).date().isoformat()
    published = {row['repertoire_id']:row for row in snapshot['publications']}
    practiced = {row['card_id'] for row in snapshot['reviews'] if row['source_kind']=='study' and row['invalidated_at'] is None}
    result = []
    for card in snapshot['cards']:
        identifier = card['id']
        memberships[identifier].add(card['repertoire_id'])
        family_memberships = memberships[identifier] & scenarios.keys()
        reasons = []
        if card['archived']: reasons.append('archived')
        if card['superseded_by']: reasons.append('superseded')
        if identifier in deleted: reasons.append('deleted')
        if card['pending_validation']: reasons.append('pending_validation')
        if not family_memberships: reasons.append('unmapped')
        classification = 'already_studied' if exposure[identifier] else 'admitted_unstarted' if card['introduced_at'] or identifier in queued else 'not_admitted'
        if classification == 'already_studied': reasons.append('already_shown_attempted_reviewed')
        status = 'excluded' if reasons else 'included'
        if classification == 'admitted_unstarted' and any(origin['card_id']==identifier and origin['legacy'] for origin in snapshot['origins']):
            status='unresolved';reasons.append('uncertain_legacy_exposure')
        decisions = set()
        try:
            for family in sorted(family_memberships):
                if card.get('trained_color') not in (None, scenarios[family]['color']):
                    raise ValueError('Conflicting card/membership trained color')
                decisions.update(card_decisions(card, scenarios[family]))
        except (ValueError, TypeError) as error:
            reasons.append('invalid_content:' + str(error));status = 'excluded'
        blocking = []
        if card['state'] == 'locked': blocking.append('locked')
        if card['due_date'] > day: blocking.append('future_due')
        if family_memberships and family_memberships <= blocked[identifier]: blocking.append('integrity_blocked')
        route_rows = routes[identifier]
        if not route_rows:
            blocking.append('no_published_graph_route')
        elif not any(published.get(route['repertoire_id'],{}).get('state')=='ready' and route['repertoire_id'] not in blocked[identifier] and (route.get('parent_card_id') is None or route['parent_card_id'] in practiced) for route in route_rows):
            blocking.append('no_current_practiced_parent_route')
        baseline = min((tuple([repertoires[row['repertoire_id']]['created_at'], row['repertoire_id'],
                    line_creation.get(row['line_id'],''),row['line_id'], row['decision_index'], identifier]) for row in route_rows if row['repertoire_id'] in scenarios), default=('', '', '', '', 0, identifier))
        result.append({**card, 'memberships': sorted(memberships[identifier]), 'families': sorted(family_memberships),
                       'classification': classification, 'status': status, 'reasons': reasons,
                       'blocking_reasons': blocking, 'eligible': not blocking and status == 'included',
                       'line_ids': sorted({row['line_id'] for row in route_rows}),
                       'decisions': sorted(decisions), 'baseline_order': baseline,
                       'effective_trained_colors': {family:scenarios[family]['color'] for family in sorted(family_memberships)},
                       'fingerprint': content_fingerprint(card, memberships[identifier])})
    return result


def create_plan(snapshot, config, predict=None, provenance=None):
    records = inventory(snapshot, config)
    prefix_evidence=[{'repertoire_id':row['repertoire_id'],'start_fen':row['starting_fen'],'moves_json':row['moves_json']} for row in snapshot['graphs'] if 'starting_fen' in row]
    for card in records:
        if card['archived'] or card['pending_validation'] or card['superseded_by'] or any(reason.startswith('invalid_content:') for reason in card['reasons']):
            continue
        for family in card['families']:
            prefix_evidence.append({**card,'repertoire_id':family})
    anchor_positions={scenario['repertoire_id']:opening_position_key(anchor(scenario).fen()) for scenario in config['scenarios']}
    lines, lineage_diagnostics = recover_lines(snapshot['lines'],prefix_evidence,anchor_positions)
    decision_values, evidence, covered = {}, {}, set()
    records_by_id = {row['id']: row for row in records}
    presentations_by_card = defaultdict(list)
    for presentation in snapshot['presentations']:
        presentations_by_card[presentation['card_id']].append(presentation)
    represented_suffixes = defaultdict(set)
    for line in lines:
        board = chess.Board(line['start_fen'])
        moves = tuple(json.loads(line['moves_json']))
        for index, uci in enumerate(moves):
            represented_suffixes[line['repertoire_id'], opening_position_key(board.fen())].add(moves[index:])
            board.push_uci(uci)
    for row in records:
        if row['status'] != 'included':
            continue
        moves = tuple(json.loads(row['moves_json']))
        for family in row['families']:
            suffixes = represented_suffixes[family, opening_position_key(row['start_fen'])]
            if not any(suffix[:len(moves)] == moves for suffix in suffixes):
                row['status']='unresolved';row['reasons'].append('missing_exact_card_lineage:' + family)
                break
    unknown = [row for row in records if row['status'] == 'unresolved']
    # Actual partial evidence only; starting a presentation does not cover all its decisions.
    for row in snapshot['observations']:
        covered.add(row['decision_id'])
    presentations = {row['id']: row for row in snapshot['presentations']}
    scenarios = {row['repertoire_id']: row for row in config['scenarios']}
    for attempt in snapshot['attempts']:
        manifest = json.loads(attempt['manifest_json'])
        if attempt['state'] == 'complete':
            covered.update(row['decision_id'] for row in manifest.get('decisions', []))
    # Teaching checkpoints identify the shown learner position by exact card ply.
    for teaching in snapshot['presented_teaching']:
        matching=[row for row in presentations_by_card[teaching['card_id']] if row['revision']==teaching['revision']]
        if len(matching)!=1:
            continue
        record=records_by_id.get(teaching['card_id'])
        if not record:
            continue
        presentation=matching[0]
        for family in record['families']:
            scenario=scenarios[family]
            board=chess.Board(presentation['start_fen'])
            for offset,uci in enumerate(json.loads(presentation['moves_json'])):
                if offset==teaching['ply'] and board.turn==(scenario['color']=='white'):
                    covered.add(decision_identity(family,board.fen(),scenario['color'],uci))
                board.push_uci(uci)
    # Legacy completed study is credited only against a proven historical presentation.
    for review in snapshot['reviews']:
        if review['source_kind'] != 'study' or review['invalidated_at']:
            continue
        matching = [row for row in presentations_by_card[review['card_id']] if row['captured_at'] <= review['reviewed_at']]
        if not matching:
            continue  # No verified presentation; cannot invent overlap credit.
        presentation = max(matching, key=lambda row: row['captured_at'])
        for scenario in config['scenarios']:
            record = records_by_id.get(review['card_id'])
            if record and scenario['repertoire_id'] in record['memberships']:
                try:
                    covered.update(card_decisions(presentation, scenario))
                except ValueError:
                    pass
    unresolved_line_ids={item['line_id'] for item in lineage_diagnostics}
    uncertain_decisions=set()
    for line in snapshot['lines']:
        if line['id'] not in unresolved_line_ids or line['repertoire_id'] not in scenarios:
            continue
        try:
            uncertain_decisions.update(card_decisions(line,scenarios[line['repertoire_id']],require_complete=False))
        except ValueError:
            pass
    for row in records:
        if row['status']=='included' and set(row['decisions']) & uncertain_decisions:
            row['status']='unresolved';row['reasons'].append('unknown_route_mass')
            unknown.append(row)
    if predict is not None:
        for scenario in config['scenarios']:
            family = scenario['repertoire_id']
            family_lines = [line for line in lines if line['repertoire_id'] == family]
            try:
                result = represented_reach(scenario, family_lines, predict)
                evidence[family] = result
                for decision, probability in result['reach'].items():
                    decision_values[decision] = probability * scenario['weight']
            except (ValueError, KeyError, RuntimeError) as error:
                evidence[family] = {'error': str(error)}
        for row in records:
            if row['status'] == 'included' and not set(row['decisions']) <= decision_values.keys():
                row['status'] = 'unresolved';row['reasons'].append('unknown_decision_reach');unknown.append(row)
    else:
        for row in records:
            if row['status'] == 'included':
                row['status'] = 'unresolved';row['reasons'].append('inference_not_run');unknown.append(row)
    ranked = greedy_order([row for row in records if row['status'] == 'included'], decision_values, covered)
    plan = {'version': VERSION, 'target': snapshot['metadata'][0], 'cutoff': snapshot.get('cutoff'),
            'configuration': config, 'provenance': provenance, 'inventory': records, 'ranks': ranked,
            'decision_evidence': evidence, 'covered_decisions': sorted(covered), 'lineage_diagnostics': lineage_diagnostics,
            'source_fingerprint': source_fingerprint(snapshot), 'input_checksum': stable_key(snapshot), 'complete': not unknown and not lineage_diagnostics,
            'unknown_count': len(unknown), 'unmapped_repertoires': [row for row in snapshot['repertoires'] if row['id'] not in scenarios and not row['id'].startswith('__')],
            'semantics': 'represented paths only; no unrepresented departures/returns; no mastery estimate',
            'per_repertoire_allowances': preview_allowances(snapshot, records, config),
            'previous_order': {'generated': snapshot['generated'], 'legacy': snapshot['priorities'], 'queue': snapshot['queue']}}
    plan['checksum'] = stable_key(plan)
    plan['run_id'] = plan['checksum']
    return plan


def source_fingerprint(snapshot):
    return stable_key({name: sorted(snapshot[name], key=stable_key) for name in
        ('lines','graphs','publications','memberships','repertoires','blocks','deleted')}, sorted([{field:card.get(field) for field in ('id','repertoire_id','revision','start_fen','moves_json','trained_color','archived','pending_validation','superseded_by','source_ref','source_fen')} for card in snapshot['cards']],key=stable_key))


def preview_allowances(snapshot, records, config):
    day=datetime.fromisoformat(snapshot.get('cutoff',datetime.now(timezone.utc).isoformat())).astimezone(ZoneInfo(config.get('study_timezone','America/New_York'))).date().isoformat()
    cards={row['id']:row for row in records}
    consumed=defaultdict(set)
    for queued in snapshot['queue']:
        card=cards.get(queued['card_id'])
        if card and queued.get('queue_date')==day and queued.get('cycle',0)==0 and card['introduced_at']==day:
            consumed[queued.get('admission_repertoire_id') or card['repertoire_id']].add(card['id'])
    return {row['id']:{'limit':row['daily_limit'],'consumed':len(consumed[row['id']]),'remaining':max(0,row['daily_limit']-len(consumed[row['id']]))} for row in snapshot.get('limits',[])}
