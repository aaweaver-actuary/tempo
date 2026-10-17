"""Offline translation of captured legacy authority into existing A documents.

No database or service implementation imports. Graphs are evidence, not permission
for transposition re-entry. Every alias requires exact executable compatibility.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import chess


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def fingerprint(document):
    return hashlib.sha256(canonical(document).encode()).hexdigest()


def translate(tables, graphs, *, workspace, repertoire_ids, source_key):
    # Establish policy only after the actual ordered route distinguishes it
    # from every other same-color authored repertoire in this capture.
    catalog={}
    for line in tables['repertoire_lines']:
        if line['trained_color'] not in ('white','black'):
            continue
        node=catalog.setdefault((line['trained_color'],line['start_fen']),{'policies':set(),'moves':{}})
        node['policies'].add(line['repertoire_id'])
        for move in json.loads(line['moves_json']):
            node=node['moves'].setdefault(move,{'policies':set(),'moves':{}})
            node['policies'].add(line['repertoire_id'])

    selected = set(repertoire_ids)
    repertoires = {row['id']:row for row in tables['repertoires'] if row['id'] in selected}
    if len(selected) != len(repertoire_ids) or set(repertoires) != selected:
        raise ValueError('explicit_unique_existing_repertoires_required')
    lines = {row['id']:row for row in tables['repertoire_lines'] if row['repertoire_id'] in selected}
    cards = {row['id']:row for row in tables['cards']}
    links = {(row['repertoire_id'],row['card_id']) for row in tables['repertoire_cards']}
    diagnostics, routes, memberships, bindings, exercises, aliases = [], [], [], [], {}, {}
    by_line = defaultdict(list)
    for graph in graphs:
        if graph['repertoire_id'] in selected:
            by_line[graph['line_id']].append(graph)
    historical_unknown = set()
    normalized_cards = {}
    policies = {}
    positions = {}
    for identifier, line in sorted(lines.items()):
        color = line['trained_color']
        if color not in ('white','black'):
            raise ValueError('authored_route_color_unknown')
        previous = policies.setdefault(line['repertoire_id'], color)
        if previous != color:
            raise ValueError('mixed_repertoire_policy_color')
        board = chess.Board(line['start_fen'])
        if not board.is_valid():
            raise ValueError('invalid_authored_start')
        moves = json.loads(line['moves_json'])
        fens = []
        for move in moves:
            fens.append(board.fen(en_passant='fen'))
            board.push_uci(move)
        positions[identifier] = fens
        node=catalog[(color,line['start_fen'])]
        authority=0 if node['policies']=={line['repertoire_id']} else None
        for ply,move in enumerate(moves,1):
            node=node['moves'][move]
            if authority is None and node['policies']=={line['repertoire_id']}:
                authority=ply
        routes.append({'key':identifier,'starting_fen':line['start_fen'],'moves':moves,'authority_start_ply':authority})
        memberships.append({'repertoire_key':line['repertoire_id'],'policy_key':line['repertoire_id'],'route_key':identifier})
        candidates = defaultdict(list)
        for graph in by_line[identifier]:
            card = cards.get(graph['card_id'])
            reason = None
            if card is None or (line['repertoire_id'],graph['card_id']) not in links:
                reason = 'historical_card_or_membership_missing'
            elif card['archived'] or card['pending_validation']:
                reason = 'historical_card_unavailable'
            elif card['trained_color'] != color or graph['trained_color'] != color:
                reason = 'historical_color_unresolved'
            elif card['revision'] != 1:
                reason = 'historical_revision_requires_explicit_successor'
            elif card['start_fen'] != graph['starting_fen'] or json.loads(card['moves_json']) != json.loads(graph['moves_json']):
                reason = 'historical_executable_changed'
            else:
                possible = [ply for ply, fen in enumerate(fens) if fen == card['start_fen'] and moves[ply:ply+len(json.loads(card['moves_json']))] == json.loads(card['moves_json'])]
                if len(possible) != 1:
                    reason = 'exact_route_binding_unresolved'
                elif len(json.loads(card['moves_json'])) == 0 or len(json.loads(card['moves_json'])) > 40:
                    reason = 'historical_executable_outside_contract'
                else:
                    binding_ply = possible[0]
                    executable = dict(card)
                    start = chess.Board(card['start_fen'])
                    accepted = json.loads(card['moves_json'])
                    if start.turn != (color == 'white'):
                        # Preserve the original card as history evidence. This
                        # normalized definition is a NEW disposable executable,
                        # never an alias claiming historical compatibility.
                        start.push_uci(accepted[0])
                        accepted = accepted[1:]
                        if not accepted:
                            diagnostics.append({'route_id':identifier,'card_id':card['id'],'reason':'opponent_prelude_without_learner_response'})
                            continue
                        executable.update(id='normalized:'+card['id'],start_fen=start.fen(en_passant='fen'),moves_json=json.dumps(accepted))
                        historical_unknown.add(executable['id'])
                        normalized_cards[executable['id']] = card['id']
                        binding_ply += 1
                        diagnostics.append({'route_id':identifier,'card_id':card['id'],'reason':'opponent_prelude_historical_adoption_unsupported','normalized_key':executable['id']})
                    candidates[binding_ply].append(executable)
            if reason:
                diagnostics.append({'route_id':identifier,'card_id':graph['card_id'],'reason':reason})
        for ply, choices in sorted(candidates.items()):
            distinct = {card['id']:card for card in choices}
            if len(distinct) != 1:
                diagnostics.append({'route_id':identifier,'ply':ply,'reason':'ambiguous_physical_binding','card_ids':sorted(distinct)})
                continue
            card = next(iter(distinct.values()))
            owner = aliases.get(card['id'])
            if owner is not None and owner != line['repertoire_id']:
                raise ValueError('shared_physical_card_requires_explicit_policy_mapping')
            aliases[card['id']] = line['repertoire_id']
            key = card['id']
            exercises[key] = {'key':key,'decision_key':key,'policy_key':line['repertoire_id'],
                'starting_fen':card['start_fen'],'prompt':'Play your authored repertoire continuation',
                'specification':{'type':'move_line','mode':'single' if len(json.loads(card['moves_json'])) == 1 else 'stepwise_line',
                    'grading_policy':'reference','accepted_lines':[json.loads(card['moves_json'])]}}
            bindings.append({'route_key':identifier,'policy_key':line['repertoire_id'],'ply':ply,'exercise_key':key})
    # Long legacy cards contain multiple authored learner decisions. Preserve
    # those routes completely without pretending each interior decision is an
    # independently adoptable historical card. Identity includes ordered prefix,
    # so transposed arrivals are never merged by FEN alone.
    bound={(binding['route_key'],binding['ply']) for binding in bindings}
    unmapped_authored={}
    for membership in memberships:
        route=next(item for item in routes if item['key']==membership['route_key'])
        policy=membership['policy_key'];color=policies[policy]
        for ply,move in enumerate(route['moves']):
            fen=positions[route['key']][ply]
            if chess.Board(fen).turn!=(color=='white') or (route['key'],ply) in bound:
                continue
            key='authored:'+fingerprint([policy,route['starting_fen'],route['moves'][:ply]])
            exercise=exercises.setdefault(key,{'key':key,'decision_key':key,'policy_key':policy,'starting_fen':fen,
                'prompt':'Play your authored repertoire move','specification':{'type':'move_line','mode':'single','grading_policy':'reference','accepted_lines':[]}})
            if [move] not in exercise['specification']['accepted_lines']:
                exercise['specification']['accepted_lines'].append([move]);exercise['specification']['accepted_lines'].sort()
            bindings.append({'route_key':route['key'],'policy_key':policy,'ply':ply,'exercise_key':key})
            historical_unknown.add(key)
            unmapped_authored.setdefault(key,[]).append({'route_id':route['key'],'ply':ply,'reason':'historical_route_decision_not_independently_bound'})

    source = {'schema_version':1,'workspace_id':workspace,'source_key':source_key,'title':'Captured authored Tempo repertoire',
        'repertoires':[{'key':key,'title':repertoires[key]['name'],'learner_color':color} for key,color in sorted(policies.items())],
        'policies':[{'key':key,'repertoire_key':key} for key in sorted(policies)],
        'routes':routes,'memberships':memberships,'exercises':[exercises[key] for key in sorted(exercises)],'bindings':bindings}
    source_bytes = (canonical(source)+'\n').encode()
    manifest = {'schema_version':1,'kind':'authored_import','workspace_id':workspace,'source_key':source_key,'title':source['title'],'complete':True,
        'provenance':{'kind':'authored_export','source_sha256':hashlib.sha256(source_bytes).hexdigest(),
            'description':'Complete explicitly selected authored route scope; unresolved historical bindings remain unbound and unknown.',
            'evidence_refs':['capture:'+fingerprint(tables),'graph:'+fingerprint(graphs)]},
        'legacy_aliases':{key:key for key in sorted(exercises) if key not in historical_unknown},
        'historical_mapping_unknown':sorted(historical_unknown & exercises.keys()),'reentry':[]}
    report = {'schema_version':1,'source_repertoire_count':len(repertoires),'authored_route_count':len(routes),'definition_count':len(exercises),
        'mapped_identities':sorted(key for key in exercises if key not in historical_unknown),
        'normalized_without_historical_adoption':normalized_cards,'unmapped_authored_decisions':unmapped_authored,'diagnostics':sorted(diagnostics,key=canonical),'source_sha256':hashlib.sha256(source_bytes).hexdigest(),
        'capture_digest':fingerprint(tables),'graph_digest':fingerprint(graphs),'policy_authority_anchors':{route['key']:route['authority_start_ply'] for route in routes},'reentry':'not_authorized','complete_scope':sorted(selected),
        'excluded_repertoires':sorted(row['id'] for row in tables['repertoires'] if row['id'] not in selected and not row['id'].startswith('__'))}
    return source_bytes, manifest, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--graphs',type=Path,required=True)
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--source-key',required=True)
    parser.add_argument('--repertoire-id',action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text())
    source, manifest, report = translate(capture['tables'],json.loads(args.graphs.read_text()),workspace=args.workspace,
        repertoire_ids=args.repertoire_id,source_key=args.source_key)
    args.output.mkdir(parents=True,exist_ok=True)
    documents = {'source.json':source,'import-manifest.json':(canonical(manifest)+'\n').encode(),'identity-mapping.json':(canonical(report)+'\n').encode()}
    for name, raw in documents.items():
        path = args.output/name
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError('immutable_translation_output_conflict')
        else:
            with path.open('xb') as output:
                output.write(raw)
    print(json.dumps({'definition_count':report['definition_count'],'route_count':report['authored_route_count'],'diagnostic_count':len(report['diagnostics'])}))


if __name__ == '__main__':
    main()
