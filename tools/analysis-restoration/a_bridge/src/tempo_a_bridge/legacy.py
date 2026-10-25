"""Offline translation of captured legacy authority into existing A documents.

No database or service implementation imports. Graphs are evidence, not permission
for transposition re-entry. Default aliases require exact executable compatibility;
--graph-only creates fresh authored definitions and emits no historical aliases.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import chess

from tempo_a_bridge.graph_fen_compatibility import recorded_graph_starting_fen


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def fingerprint(document):
    return hashlib.sha256(canonical(document).encode()).hexdigest()


def policy_root_key(starting_fen):
    # Same legal-position comparison used by the downstream comparison owner:
    # clocks cannot distinguish policy, while turn/castling/legal EP can.
    # Reuse for executable alignment; never rewrite stored card/route identity.
    return ' '.join(chess.Board(starting_fen).fen(en_passant='legal').split()[:4])


def _fresh_lineage_fact(document):
    """One reviewed factual disposition, never a generic availability waiver."""
    if document is None:
        return None
    def digest(value):
        return type(value) is str and len(value)==64 and all(item in '0123456789abcdef' for item in value)
    if (type(document) is not dict or set(document)!={'schema_version','capture_sha256','full_cards_absence_proof','facts'}
            or type(document['schema_version']) is not int or document['schema_version']!=1
            or not digest(document['capture_sha256'])):
        raise ValueError('graph_only_lineage_fact_contract_unresolved')
    proof=document['full_cards_absence_proof']
    if (type(proof) is not dict or set(proof)!={'report_path','report_sha256','stream_script_sha256','captured_card_count'}
            or type(proof['report_path']) is not str or not proof['report_path']
            or not digest(proof['report_sha256']) or not digest(proof['stream_script_sha256'])
            or type(proof['captured_card_count']) is not int or proof['captured_card_count']<1):
        raise ValueError('graph_only_lineage_absence_evidence_unresolved')
    facts=document['facts']
    if type(facts) is not list or len(facts)!=1:
        raise ValueError('graph_only_one_reviewed_lineage_fact_required')
    fact=facts[0]
    if (type(fact) is not dict or set(fact)!={'source_card_id','missing_target_card_id','source_record_sha256'}
            or any(type(fact[field]) is not str or not fact[field] for field in ('source_card_id','missing_target_card_id'))
            or fact['source_card_id']==fact['missing_target_card_id'] or not digest(fact['source_record_sha256'])):
        raise ValueError('graph_only_lineage_source_pair_unresolved')
    return fact


def _recorded_archived_split_continuation(card, owner, tables, cards, lines, publications, selected_steps, exclusions):
    """Recognize retained authoring data, never alias or create an exercise."""
    candidates=[row for row in tables.get('prefix_splits',[]) if row.get('continuation_card_id')==card['id']]
    if len(candidates)!=1:
        return None
    split=candidates[0]
    try:
        original=cards[split['source_card_id']];shortened=cards[split['shortened_card_id']]
        if (original.get('canonical_route_source')!=1 or original.get('archived')!=1
                or original['revision']!=split['source_revision']):
            return None
        original_moves=json.loads(original['moves_json']);short_moves=json.loads(shortened['moves_json']);tail=json.loads(card['moves_json'])
        if len(tail)!=2 or original_moves!=short_moves+tail:
            return None
        recorded_graph_starting_fen(original['start_fen'],shortened['start_fen'])
        boundary=chess.Board(original['start_fen'])
        for move in short_moves:
            boundary.push_uci(move)
        recorded_graph_starting_fen(boundary.fen(en_passant='legal'),card['start_fen'])
        raw=[row for row in selected_steps if row['repertoire_id']==owner and row['card_id']==original['id']]
        if not raw:
            return None
        publication=publications.get(owner)
        archived={(row['repertoire_id'],row['generation'],row['line_id'],row['decision_index']):row for row in exclusions
            if row['reason']=='explicit_authored_archive' and row['card_id']==original['id']}
        represented=[]
        for step in raw:
            color=step['trained_color'];line=lines[step['line_id']]
            if (publication is None or publication['state']!='ready' or publication['generation']!=step['generation']
                    or line['repertoire_id']!=owner or line['trained_color']!=color
                    or any(row.get('trained_color') not in (None,color) for row in (original,shortened,card))
                    or boundary.turn==(color=='white') or json.loads(step['moves_json'])!=original_moves):
                return None
            recorded_graph_starting_fen(original['start_fen'],step['starting_fen'])
            tail_board=boundary.copy();tail_board.push_uci(tail[0])
            if tail_board.turn!=(color=='white'):
                return None
            tail_board.push_uci(tail[1])
            key=(owner,step['generation'],step['line_id'],step['decision_index'])
            exclusion=archived.get(key)
            if exclusion is None:
                return None
            plies=exclusion['excluded_learner_plies']
            if not plies or json.loads(line['moves_json'])[plies[-1]-1:plies[-1]+1]!=tail:
                return None
            represented.append({field:step[field] for field in ('repertoire_id','generation','line_id','decision_index','card_id')}
                | {'represented_suffix_learner_plies':[plies[-1]]})
        return {'kind':'recorded_split_continuation_preserved_nonserving','source_card_id':card['id'],
            'source_record_sha256':fingerprint(card),'raw_source_flags':{field:card.get(field) for field in
                ('canonical_route_source','archived','pending_validation','superseded_by')},
            'split_record':dict(split),'raw_archived_source_card_id':original['id'],
            'raw_archived_source_revision':original['revision'],'raw_archived_source_occurrences':represented,
            'serving_exercise_created':False,'serving_binding_created':False}
    except (KeyError,ValueError,TypeError,IndexError):
        return None


def _graph_only_scope(tables, graphs, selected, fresh_dangling_lineage=None):
    """Project explicit published segments; never regenerate outside their scope."""
    for table in ('repertoires', 'repertoire_lines'):
        rows = tables[table]
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('graph_only_duplicate_source_identity')
    lines = {row['id']:row for row in tables['repertoire_lines']}
    selected_lines = {key:row for key,row in lines.items()
        if row['repertoire_id'] in selected and json.loads(row['moves_json'])}
    if not selected_lines:
        raise ValueError('graph_only_nonempty_authored_route_required')
    selected_steps = [step for step in graphs if step['repertoire_id'] in selected]
    if not selected_steps:
        raise ValueError('graph_only_published_graph_scope_required')
    retained = []
    serving_repertoires = {row['repertoire_id'] for row in selected_lines.values()}
    for row in tables['repertoires']:
        if row['id'] not in selected:
            continue
        fields = sorted(set(row) - {'id','name'})
        if fields or row['id'] not in serving_repertoires:
            retained.append({'table':'repertoires','key':row['id'],'fields':fields,
                'reason':'raw_source_metadata' if row['id'] in serving_repertoires else 'no_nonempty_authored_route'})
    for row in tables['repertoire_lines']:
        if row['repertoire_id'] not in selected:
            continue
        fields = sorted(set(row) - {'id','repertoire_id','trained_color','start_fen','moves_json'})
        if fields or row['id'] not in selected_lines:
            retained.append({'table':'repertoire_lines','key':row['id'],'fields':fields,
                'reason':'raw_source_metadata' if row['id'] in selected_lines else 'empty_authored_route'})
    for table, rows in sorted(tables.items()):
        if table not in ('repertoires','repertoire_lines'):
            retained.append({'table':table,'row_count':len(rows),'reason':'raw_graph_closure_metadata'})

    cards = {row['id']:row for row in tables.get('cards',[])}
    reviewed_lineage = _fresh_lineage_fact(fresh_dangling_lineage)
    if reviewed_lineage and fresh_dangling_lineage['full_cards_absence_proof']['captured_card_count']<len(cards):
        raise ValueError('graph_only_lineage_capture_count_unresolved')
    graph_references = frozenset(value for step in graphs for value in (step.get('card_id'),step.get('parent_card_id')) if value is not None)
    graph_occurrence_counts = defaultdict(int)
    for step in graphs:
        graph_occurrence_counts[step.get('card_id')] += 1
    graph_parent_references = frozenset(step.get('parent_card_id') for step in graphs if step.get('parent_card_id') is not None)
    split_references = frozenset(row[field] for row in tables.get('prefix_splits',[])
        for field in ('source_card_id','shortened_card_id','continuation_card_id') if row.get(field) is not None)
    fresh_exceptions, generated_resets, definition_fens = {}, {}, {}
    deleted = {row['card_id'] for row in tables.get('deleted_cards',[])}
    publications = {row['repertoire_id']:row for row in tables.get('opening_graph_publications',[])}
    occurrences, excluded, exercises, bindings = [], [], {}, []
    seen, bound_positions = set(), set()
    for step in sorted(selected_steps,key=canonical):
        required = {'repertoire_id','generation','line_id','decision_index','card_id','parent_card_id',
            'decision_fen_key','starting_fen','moves_json','trained_color','segment_kind',
            'first_decision_index','last_decision_index','decision_fen_keys_json'}
        if not required <= set(step):
            raise ValueError('graph_only_complete_published_step_required')
        if (type(step['generation']) is not int or step['generation'] < 1
                or type(step['decision_index']) is not int or step['decision_index'] < 0):
            raise ValueError('graph_only_graph_occurrence_identity_unresolved')
        identity = tuple(step[field] for field in ('repertoire_id','generation','line_id','decision_index'))
        if identity in seen:
            raise ValueError('graph_only_duplicate_graph_occurrence')
        seen.add(identity)
        occurrence = {field:step[field] for field in ('repertoire_id','generation','line_id','decision_index','card_id','parent_card_id')}
        publication = publications.get(step['repertoire_id'])
        if publications and (publication is None or step['generation'] != publication['generation'] or publication['state'] != 'ready'):
            raise ValueError('graph_only_published_generation_unresolved')
        if step['card_id'] in deleted:
            excluded.append({**occurrence,'reason':'captured_deleted_card'})
            continue
        line = selected_lines.get(step['line_id'])
        if line is None or line['repertoire_id'] != step['repertoire_id']:
            raise ValueError('graph_only_graph_line_scope_unresolved')
        color = line['trained_color']
        if color not in ('white','black') or step['trained_color'] != color:
            raise ValueError('graph_only_graph_color_unresolved')
        card = cards.get(step['card_id'])
        moves = json.loads(step['moves_json'])
        if not moves or len(moves) > 40:
            raise ValueError('graph_only_segment_executable_outside_contract')
        authored_archive = False
        generated_reset = None
        recorded_start_fen = definition_fens.get((step['repertoire_id'],step['card_id']),step['starting_fen'])
        if card is not None:
            generated = type(card.get('canonical_route_source')) is int and card['canonical_route_source']==0
            authored_archive = type(card.get('canonical_route_source')) is int and card['canonical_route_source']==1 and bool(card.get('archived'))
            if (card.get('content_type','opening')!='opening'
                    or (not generated and (card.get('pending_validation',0)
                        or (card.get('archived',0) and not authored_archive)
                        or (card.get('superseded_by') and not authored_archive)))):
                raise ValueError('graph_only_graph_card_availability_disagrees')
            if generated and any(card.get(field) for field in ('archived','pending_validation','superseded_by')):
                if publication is None or publication['state']!='ready' or publication['generation']!=step['generation']:
                    raise ValueError('graph_only_generated_reset_requires_ready_publication')
                generated_reset={field:card.get(field) for field in ('canonical_route_source','archived','pending_validation','superseded_by')}
            if card.get('superseded_by') and reviewed_lineage is not None and reviewed_lineage['source_card_id']==card['id']:
                target=card['superseded_by']
                if (reviewed_lineage is None or reviewed_lineage['source_card_id']!=card['id']
                        or reviewed_lineage['missing_target_card_id']!=target
                        or reviewed_lineage['source_record_sha256']!=fingerprint(card)
                        or any(type(card.get(field)) is not int or card[field]!=0
                               for field in ('canonical_route_source','archived','pending_validation'))
                        or card.get('kind')!='prefix' or card.get('content_type')!='opening'
                        or graph_occurrence_counts[card['id']]!=1 or card['id'] in graph_parent_references or card['id'] in split_references
                        or publication is None or publication['state']!='ready' or publication['generation']!=step['generation']
                        or target in cards or target in graph_references or target in split_references or target in deleted):
                    raise ValueError('graph_only_graph_card_availability_disagrees')
                fresh_exceptions[card['id']]={
                    'kind':'graph_only_dangling_generated_lineage_rebuilt',
                    'source_card_id':card['id'],'raw_superseded_by':target,
                    'source_record_sha256':reviewed_lineage['source_record_sha256'],
                    'capture_sha256':fresh_dangling_lineage['capture_sha256'],
                    'full_cards_absence_proof':dict(fresh_dangling_lineage['full_cards_absence_proof'])}
            if (moves != json.loads(card['moves_json'])
                    or (card['trained_color'] is not None and step['trained_color'] != card['trained_color'])):
                raise ValueError('graph_only_graph_card_definition_disagrees')
            try:
                recorded_start_fen=recorded_graph_starting_fen(card['start_fen'],step['starting_fen'])
            except ValueError:
                raise ValueError('graph_only_graph_card_definition_disagrees') from None
        else:
            recorded_start_fen=recorded_graph_starting_fen(recorded_start_fen,step['starting_fen'])
        board = chess.Board(line['start_fen'])
        if not board.is_valid():
            raise ValueError('invalid_authored_start')
        route_moves = json.loads(line['moves_json'])
        positions, learner_offsets = [], []
        for ply,move in enumerate(route_moves):
            positions.append(policy_root_key(board.fen(en_passant='fen')))
            if board.turn == (color == 'white'):
                learner_offsets.append(ply)
            board.push_uci(move)
        first,last = step['first_decision_index'],step['last_decision_index']
        if (type(first) is not int or type(last) is not int or not 0 <= first <= last < len(learner_offsets)
                or step['segment_kind'] not in ('prefix','decision') or (step['segment_kind']=='decision' and first != last)):
            raise ValueError('graph_only_graph_learner_scope_unresolved')
        covered = learner_offsets[first:last+1]
        candidates = [ply for ply,position in enumerate(positions)
            if position == policy_root_key(step['starting_fen']) and route_moves[ply:ply+len(moves)] == moves
            and [offset for offset in learner_offsets if ply <= offset < ply+len(moves)] == covered]
        if len(candidates) != 1:
            raise ValueError('graph_only_graph_ordered_path_unresolved')
        keys = [positions[ply] for ply in covered]
        if json.loads(step['decision_fen_keys_json']) != keys or step['decision_fen_key'] != keys[-1]:
            raise ValueError('graph_only_graph_decision_keys_disagree')
        start_ply = candidates[0]
        key = 'graph:' + fingerprint([step['repertoire_id'],step['card_id']])
        if authored_archive:
            excluded.append({**occurrence,'reason':'explicit_authored_archive','fresh_exercise_key':key,
                'excluded_learner_plies':covered,'raw_source_flags':{field:card.get(field) for field in
                    ('canonical_route_source','archived','pending_validation','superseded_by')}})
            continue
        definition_fens[(step['repertoire_id'],step['card_id'])]=recorded_start_fen
        if generated_reset is not None:
            reset=generated_resets.setdefault(key,{'source_card_id':card['id'],'fresh_exercise_key':key,
                'reason':'new_graph_definition_does_not_adopt_generated_availability','raw_source_flags':generated_reset,
                'affected_graph_occurrences':0})
            reset['affected_graph_occurrences']+=1
        start = chess.Board(recorded_start_fen)
        if not start.is_valid():
            raise ValueError('graph_only_invalid_graph_start')
        accepted = list(moves)
        prelude = None
        if start.turn != (color == 'white'):
            prelude = {'starting_fen':recorded_start_fen,'moves':[accepted[0]]}
            start.push_uci(accepted.pop(0))
            if not accepted:
                raise ValueError('graph_only_segment_has_no_learner_response')
        key = 'graph:' + fingerprint([step['repertoire_id'],step['card_id']])
        exercise = {'key':key,'decision_key':key,'policy_key':step['repertoire_id'],
            'starting_fen':start.fen(en_passant='fen') if prelude else recorded_start_fen,
            'prompt':'Play your authored repertoire continuation',
            'specification':{'type':'move_line','mode':'single' if len(accepted)==1 else 'stepwise_line',
                'grading_policy':'reference','accepted_lines':[accepted]}}
        if prelude:
            exercise['execution_prelude'] = prelude
        if key in exercises and exercises[key] != exercise:
            raise ValueError('graph_only_shared_graph_definition_disagrees')
        exercises[key] = exercise
        projected = []
        for ply in covered:
            offset = ply-start_ply-(1 if prelude else 0)
            binding = {'route_key':line['id'],'policy_key':step['repertoire_id'],'ply':ply,'exercise_key':key}
            if offset:
                binding['exercise_ply'] = offset
            position = (line['id'],step['repertoire_id'],ply)
            if position in bound_positions:
                raise ValueError('graph_only_graph_learner_coverage_overlaps')
            bound_positions.add(position)
            bindings.append(binding)
            projected.append({'ply':ply,'exercise_ply':offset})
        projection={**occurrence,'exercise_key':key,'learner_bindings':projected}
        if step['starting_fen']!=recorded_start_fen:
            projection.update(raw_graph_starting_fen=step['starting_fen'],recorded_definition_starting_fen=recorded_start_fen)
        occurrences.append(projection)

    # Canonical cards can also be authored sources. Every effective source needs
    # an explicit accepted occurrence; no line association is guessed from FEN.
    links = {(row['repertoire_id'],row['card_id']):row for row in tables.get('repertoire_cards',[])}
    memberships_by_card = defaultdict(dict)
    for (owner,card_id),link in links.items():
        memberships_by_card[card_id][owner] = link
    # Packet-local indexes are read-only during source validation. Preserve the
    # composite membership key and explicit zero; no truthy fallback/aliasing.
    memberships_by_card = {key:tuple(rows.items()) for key,rows in memberships_by_card.items()}
    occurrence_scopes = frozenset((row['repertoire_id'],row['card_id']) for row in occurrences)
    canonical_dispositions=[]
    for card in cards.values():
        if card.get('content_type') != 'opening' or card.get('archived') != 0 or card['id'] in deleted:
            continue
        owners = {owner for owner,link in memberships_by_card.get(card['id'],())
            if owner in selected and link.get('canonical_route_source') == 1}
        owner = card.get('repertoire_id')
        if owner in selected and (owner,card['id']) not in links and card.get('canonical_route_source') == 1:
            owners.add(owner)
        for owner in sorted(owners):
            if (owner,card['id']) in occurrence_scopes:
                continue
            disposition=_recorded_archived_split_continuation(card,owner,tables,cards,
                {row['id']:row for row in tables['repertoire_lines']},publications,selected_steps,excluded)
            if disposition is None:
                raise ValueError('graph_only_canonical_source_occurrence_required')
            canonical_dispositions.append(disposition)
    if not exercises:
        raise ValueError('graph_only_no_serving_graph_decision')
    if reviewed_lineage and reviewed_lineage['source_card_id'] not in fresh_exceptions:
        raise ValueError('graph_only_lineage_fact_unused')
    return selected_lines, retained, exercises, bindings, occurrences, excluded, list(fresh_exceptions.values()), list(generated_resets.values()), canonical_dispositions


def translate(tables, graphs, *, workspace, repertoire_ids, source_key, preserve_physical_execution=False, graph_only=False, fresh_dangling_lineage=None):
    if graph_only and preserve_physical_execution:
        raise ValueError('graph_only_cannot_adopt_physical_execution')
    if fresh_dangling_lineage is not None and not graph_only:
        raise ValueError('graph_only_lineage_facts_require_graph_only')
    # Establish policy only after the actual ordered route distinguishes it
    # from every other same-color authored repertoire in this capture.
    catalog={}
    for line in tables['repertoire_lines']:
        if line['trained_color'] not in ('white','black'):
            continue
        node=catalog.setdefault((line['trained_color'],policy_root_key(line['start_fen'])),{'policies':set(),'moves':{}})
        node['policies'].add(line['repertoire_id'])
        for move in json.loads(line['moves_json']):
            node=node['moves'].setdefault(move,{'policies':set(),'moves':{}})
            node['policies'].add(line['repertoire_id'])

    selected = set(repertoire_ids)
    repertoires = {row['id']:row for row in tables['repertoires'] if row['id'] in selected}
    if len(selected) != len(repertoire_ids) or set(repertoires) != selected:
        raise ValueError('explicit_unique_existing_repertoires_required')
    lines = {row['id']:row for row in tables['repertoire_lines'] if row['repertoire_id'] in selected}
    non_serving, graph_exercises, graph_bindings, graph_occurrences, excluded_graph, fresh_exceptions, generated_resets, canonical_dispositions = [], {}, [], [], [], [], [], []
    if graph_only:
        lines, non_serving, graph_exercises, graph_bindings, graph_occurrences, excluded_graph, fresh_exceptions, generated_resets, canonical_dispositions = _graph_only_scope(tables, graphs, selected, fresh_dangling_lineage)
    cards = {} if graph_only else {row['id']:row for row in tables['cards']}
    links = set() if graph_only else {(row['repertoire_id'],row['card_id']) for row in tables['repertoire_cards']}
    diagnostics, routes, memberships, bindings, exercises, aliases = [], [], [], [], {}, {}
    by_line = defaultdict(list)
    for graph in graphs:
        if not graph_only and graph['repertoire_id'] in selected:
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
        comparison_positions = [policy_root_key(fen) for fen in fens]
        positions[identifier] = fens
        node=catalog[(color,policy_root_key(line['start_fen']))]
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
            elif card['archived'] or card['pending_validation'] or card.get('integrity_blocked') or card.get('deleted'):
                reason = 'historical_card_unavailable'
            elif card['trained_color'] != color or graph['trained_color'] != color:
                reason = 'historical_color_unresolved'
            elif card['revision'] != 1:
                reason = 'historical_revision_requires_explicit_successor'
            elif (not chess.Board(card['start_fen']).is_valid() or not chess.Board(graph['starting_fen']).is_valid()
                    or policy_root_key(card['start_fen']) != policy_root_key(graph['starting_fen'])
                    or json.loads(card['moves_json']) != json.loads(graph['moves_json'])):
                reason = 'historical_executable_changed'
            else:
                physical_position = policy_root_key(card['start_fen'])
                possible = [ply for ply, position in enumerate(comparison_positions) if position == physical_position and moves[ply:ply+len(json.loads(card['moves_json']))] == json.loads(card['moves_json'])]
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
                        prelude={'starting_fen':card['start_fen'],'moves':[accepted[0]]}
                        start.push_uci(accepted[0])
                        accepted = accepted[1:]
                        if not accepted:
                            diagnostics.append({'route_id':identifier,'card_id':card['id'],'reason':'opponent_prelude_without_learner_response'})
                            continue
                        executable.update(start_fen=start.fen(en_passant='fen'),moves_json=json.dumps(accepted))
                        if preserve_physical_execution:
                            executable['execution_prelude']=prelude
                            reason='opponent_prelude_preserved_in_executable'
                        else:
                            executable['id']='normalized:'+card['id']
                            historical_unknown.add(executable['id'])
                            normalized_cards[executable['id']] = card['id']
                            reason='opponent_prelude_historical_adoption_unsupported'
                        binding_ply += 1
                        diagnostics.append({'route_id':identifier,'card_id':card['id'],'reason':reason,'normalized_key':executable['id']})
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
            if card.get('execution_prelude'):
                exercises[key]['execution_prelude']=card['execution_prelude']
            bindings.append({'route_key':identifier,'policy_key':line['repertoire_id'],'ply':ply,'exercise_key':key})
    if graph_only:
        exercises.update(graph_exercises)
        bindings.extend(sorted(graph_bindings,key=canonical))
    # Long legacy cards contain multiple authored learner decisions. Preserve
    # those routes completely without pretending each interior decision is an
    # independently adoptable historical card. Identity includes ordered prefix,
    # so transposed arrivals are never merged by FEN alone.
    bound={(binding['route_key'],binding['ply']) for binding in bindings}
    roots_by_route=defaultdict(list)
    if preserve_physical_execution:
        for binding in bindings:
            if binding['exercise_key'] not in historical_unknown:
                roots_by_route[binding['route_key']].append(binding)
    unmapped_authored={}
    for membership in memberships:
        route=next(item for item in routes if item['key']==membership['route_key'])
        policy=membership['policy_key'];color=policies[policy]
        for ply,move in enumerate(route['moves']):
            fen=positions[route['key']][ply]
            if graph_only or chess.Board(fen).turn!=(color=='white') or (route['key'],ply) in bound:
                continue
            if preserve_physical_execution:
                possible={}
                for root_binding in roots_by_route[route['key']]:
                    offset=ply-root_binding['ply']
                    exercise=exercises[root_binding['exercise_key']]
                    line=exercise['specification']['accepted_lines'][0]
                    if 0<offset<len(line) and line[:offset]==route['moves'][root_binding['ply']:ply] and line[offset]==move:
                        possible[root_binding['exercise_key']]=offset
                if len(possible)==1:
                    exercise_key,offset=next(iter(possible.items()))
                    bindings.append({'route_key':route['key'],'policy_key':policy,'ply':ply,'exercise_key':exercise_key,'exercise_ply':offset})
                    continue
                if len(possible)>1:
                    diagnostics.append({'route_id':route['key'],'ply':ply,'reason':'ambiguous_historical_interior_binding','card_ids':sorted(possible)})
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
    if graph_only:
        manifest.update(legacy_aliases={}, historical_mapping_unknown=[])
        manifest['provenance']['description']='Fresh authored graph projection only; raw graph metadata retained separately; no historical execution or aliases adopted.'
        report.update(mode='graph_only_fresh_start', mapped_identities=[],
            normalized_without_historical_adoption={}, unmapped_authored_decisions={},
            non_serving_source_entities=sorted(non_serving,key=canonical), raw_graph_recovery_copy_required=True,
            serving_repertoires=sorted({row['repertoire_id'] for row in graph_occurrences}),
            serving_routes=sorted({row['line_id'] for row in graph_occurrences}),
            source_graph_bindings=sorted(graph_occurrences,key=canonical),
            excluded_graph_occurrences=sorted(excluded_graph,key=canonical),
            fresh_policy_exceptions=sorted(fresh_exceptions,key=canonical),
            generated_availability_resets=sorted(generated_resets,key=canonical),
            authored_archive_excluded_definition_count=len({row['fresh_exercise_key'] for row in excluded_graph if row['reason']=='explicit_authored_archive'}),
            authored_archive_excluded_occurrence_count=sum(row['reason']=='explicit_authored_archive' for row in excluded_graph),
            authored_archive_excluded_binding_count=len({(row['repertoire_id'],row['line_id'],ply) for row in excluded_graph
                if row['reason']=='explicit_authored_archive' for ply in row['excluded_learner_plies']}))
        if canonical_dispositions:
            report['canonical_source_dispositions']=sorted(canonical_dispositions,key=canonical)
        if generated_resets:
            manifest['provenance']['description']+=' Legacy generated availability remains raw evidence and is not adopted by these new graph definitions.'
        if fresh_exceptions:
            manifest['provenance']['description']+=' One reviewed generated dangling-lineage marker was rebuilt under explicit fresh policy; raw lineage remains retained.'
            manifest['provenance']['evidence_refs'].append('fresh-lineage-facts:'+fingerprint(fresh_dangling_lineage))
    if preserve_physical_execution:
        manifest['provenance']['description']='Complete selected authored scope with original physical executables and explicit learner-decision projections; unresolved identities remain unknown.'
        report.update(physical_execution_preserved=True,historical_prelude_count=sum('execution_prelude' in exercise for exercise in exercises.values()),historical_interior_binding_count=sum(binding.get('exercise_ply',0)>0 for binding in bindings))
    return source_bytes, manifest, report


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--graphs',type=Path)
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--source-key',required=True)
    parser.add_argument('--repertoire-id',action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fresh-dangling-lineage',type=Path,help='One reviewed full-capture factual disposition for fresh graph-only projection')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--preserve-physical-execution',action='store_true')
    modes.add_argument('--graph-only',action='store_true',help='Project fresh authored routes without adopting legacy cards or history')
    args = parser.parse_args(arguments)
    if not args.graph_only and args.graphs is None:
        parser.error('--graphs is required unless --graph-only is selected')
    if args.fresh_dangling_lineage is not None and not args.graph_only:
        parser.error('--fresh-dangling-lineage requires --graph-only')
    if args.fresh_dangling_lineage is not None and args.fresh_dangling_lineage.stat().st_size>16384:
        raise ValueError('graph_only_lineage_fact_file_bound')
    lineage = json.loads(args.fresh_dangling_lineage.read_text()) if args.fresh_dangling_lineage is not None else None
    capture = json.loads(args.capture.read_text())
    graphs = json.loads(args.graphs.read_text()) if args.graphs is not None else capture.get('graphs',[])
    source, manifest, report = translate(capture['tables'],graphs,workspace=args.workspace,
        repertoire_ids=args.repertoire_id,source_key=args.source_key,preserve_physical_execution=args.preserve_physical_execution,graph_only=args.graph_only,fresh_dangling_lineage=lineage)
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
