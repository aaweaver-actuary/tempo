"""Finite represented-path estimates and one-time marginal coverage, without I/O."""
from __future__ import annotations

from collections import defaultdict
import heapq
import math
import chess
from .opening_segmentation import decision_identity, opening_position_key, stable_key

VERSION = 'frozen-maia-v1'


def anchor(scenario):
    board = chess.Board()
    for san in scenario.get('prefix', []):
        board.push_san(san)
    return board


def validate_distribution(fen, moves):
    board = chess.Board(fen)
    legal = {move.uci() for move in board.legal_moves}
    probabilities = {}
    for item in moves:
        move, probability = item['move_uci'], item['probability']
        if move in probabilities or move not in legal or not math.isfinite(probability) or probability < 0:
            raise ValueError('Invalid legal move distribution')
        probabilities[move] = probability
    if probabilities.keys() != legal or (legal and abs(math.fsum(probabilities.values()) - 1) > 1e-8):
        raise ValueError('Incomplete legal move distribution')
    return probabilities


def represented_reach(scenario, lines, predict):
    """A trie retains mutually exclusive histories; seen identities stop repeat credit."""
    root = {'children': {}, 'decisions': set()}
    trained_white = scenario['color'] == 'white'
    policy_id = scenario['repertoire_id']
    prefix_board = chess.Board()
    forced = []
    decision_metadata = {}
    for san in scenario.get('prefix', []):
        move = prefix_board.parse_san(san)
        if prefix_board.turn == trained_white:
            forced_identity = decision_identity(policy_id, prefix_board.fen(), scenario['color'], move.uci())
            forced.append(forced_identity)
            decision_metadata[forced_identity] = {'fen':prefix_board.fen(en_passant='fen'),'expected_uci':move.uci(),'trained_color':scenario['color'],'within_assumed_prefix':True}
        prefix_board.push(move)
    anchor_key = opening_position_key(prefix_board.fen())
    diagnostics, line_evidence, universe = [], [], set()
    for line in lines:
        try:
            board = chess.Board(line['start_fen'])
            if not board.is_valid():
                raise ValueError('Invalid start position')
            moves = line['moves_json']
            if isinstance(moves, str):
                import json
                moves = json.loads(moves)
            validation_board = board.copy()
            for uci in moves:
                move = chess.Move.from_uci(uci)
                if move not in validation_board.legal_moves:
                    raise ValueError(f'Illegal source move {uci}')
                if validation_board.turn == trained_white:
                    identity = decision_identity(policy_id, validation_board.fen(), scenario['color'], uci)
                    universe.add(identity)
                    decision_metadata.setdefault(identity, {'fen':validation_board.fen(en_passant='fen'),'expected_uci':uci,'trained_color':scenario['color'],'within_assumed_prefix':False})
                validation_board.push(move)
            # Require a legally recovered route from initial position or exact anchor.
            started = opening_position_key(board.fen()) == anchor_key
            node = root
            represented = []
            if opening_position_key(board.fen()) not in {opening_position_key(chess.STARTING_FEN), anchor_key}:
                raise ValueError('Missing initial/anchor lineage')
            for index, uci in enumerate(moves):
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError(f'Illegal move {uci} at {index}')
                if not started:
                    # The condition is a position, not the reference arrival order.
                    # Discard arrival history and begin the deduplicated suffix at 1.
                    board.push(move)
                    started = opening_position_key(board.fen()) == anchor_key
                    continue
                if scenario.get('exclude_first') == uci and not represented:
                    break
                node = node['children'].setdefault(uci, {'children': {}, 'decisions': set()})
                if board.turn == trained_white:
                    node['decisions'].add(decision_identity(policy_id, board.fen(), scenario['color'], uci))
                represented.append(uci)
                board.push(move)
            if started:
                line_evidence.append({'line_id': line['id'], 'moves': represented})
        except (ValueError, TypeError, KeyError) as error:
            diagnostics.append({'line_id': line.get('id'), 'reason': str(error)})
    reach = {decision: 0.0 for decision in universe}
    reach.update({decision: 1.0 for decision in forced})
    distributions = {}
    position_reach, position_fens = {}, {}
    alternative_assumptions = []
    stack = [(root, prefix_board.copy(), 1.0, frozenset(forced), frozenset())]
    while stack:
        node, board, mass, seen, seen_positions = stack.pop()
        position_key = opening_position_key(board.fen())
        position_fens.setdefault(position_key,board.fen(en_passant='fen'))
        if position_key not in seen_positions:
            position_reach[position_key] = position_reach.get(position_key,0) + mass
        next_seen_positions = seen_positions | {position_key}
        children = node['children']
        if not children:
            continue
        learner = board.turn == trained_white
        if learner:
            weights = scenario.get('choice_weights', {}).get(opening_position_key(board.fen()))
            if weights is None:
                weights = {move: 1 / len(children) for move in children}
                if len(children) > 1:
                    alternative_assumptions.append({'fen': board.fen(), 'moves': sorted(children), 'weight': 1 / len(children)})
            if set(weights) != set(children) or any(not math.isfinite(value) or value < 0 for value in weights.values()) or abs(math.fsum(weights.values()) - 1) > 1e-8:
                raise ValueError('Ambiguous learner policy weights')
        else:
            fen = board.fen(en_passant='fen')
            if fen not in distributions:
                distributions[fen] = validate_distribution(fen, predict(fen))
            weights = distributions[fen]
            if scenario.get('exclude_first') and not board.move_stack:
                excluded = scenario['exclude_first']
                denominator = 1 - weights[excluded]
                if not math.isfinite(denominator) or denominator <= 1e-12:
                    raise ValueError('Invalid first-move conditioning denominator')
                weights = {move: value / denominator for move, value in weights.items() if move != excluded}
        for move, child in children.items():
            branch_mass = mass * weights[move]
            new_decisions = child['decisions'] - seen
            for decision in new_decisions:
                reach[decision] = reach.get(decision, 0) + branch_mass
            next_board = board.copy()
            next_board.push_uci(move)
            stack.append((child, next_board, branch_mass, seen | child['decisions'],next_seen_positions))
    if any(value < 0 or value > 1 + 1e-8 for value in reach.values()):
        raise ValueError('Inflated first-hit reach')
    if any(value < 0 or value > 1 + 1e-8 for value in position_reach.values()):
        raise ValueError('Inflated first-hit position reach')
    return {'reach': reach, 'position_reach':position_reach,'position_fens':position_fens,'anchor_fen':prefix_board.fen(en_passant='fen'), 'diagnostics': diagnostics, 'routes': line_evidence,
            'alternative_assumptions': alternative_assumptions, 'distributions': distributions, 'decisions': decision_metadata}


def card_decisions(card, scenario, *, require_complete=True):
    board = chess.Board(card['start_fen'])
    if not board.is_valid():
        raise ValueError('Invalid card position')
    import json
    moves = json.loads(card['moves_json']) if isinstance(card['moves_json'], str) else card['moves_json']
    decisions = set()
    for uci in moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f'Illegal card move {uci}')
        if board.turn == (scenario['color'] == 'white'):
            decisions.add(decision_identity(scenario['repertoire_id'], board.fen(), scenario['color'], uci))
        board.push(move)
    if require_complete and board.turn == (scenario['color']=='white'):
        raise ValueError('Presentation ends before learner answer')
    if not decisions:
        raise ValueError('No learner decisions')
    return decisions


def greedy_order(cards, decision_values, covered=()):
    covered = set(covered)
    affected = defaultdict(set)
    remaining = {}
    heap = []
    for card in cards:
        identifiers = set(card['decisions'])
        if not identifiers <= decision_values.keys():
            raise ValueError('UNKNOWN decision reach')
        for decision in identifiers:
            affected[decision].add(card['id'])
        score = math.fsum(decision_values[decision] for decision in identifiers - covered)
        remaining[card['id']] = [card, score, 0]
        heapq.heappush(heap, (-score, card['baseline_order'], card['id'], 0))
    ordered = []
    while remaining:
        _, _, card_id, version = heapq.heappop(heap)
        if card_id not in remaining or version != remaining[card_id][2]:
            continue
        card, score, _ = remaining.pop(card_id)
        ordered.append({**card, 'rank': len(ordered) + 1, 'marginal_score': max(0, score)})
        newly_covered = set(card['decisions']) - covered
        covered.update(newly_covered)
        losses = defaultdict(list)
        for decision in newly_covered:
            for other_id in affected[decision] & remaining.keys():
                losses[other_id].append(decision_values[decision])
        for other_id, values in losses.items():
            other, previous_score, previous_version = remaining[other_id]
            updated = math.fsum(decision_values[decision] for decision in set(other['decisions']) - covered)
            remaining[other_id] = [other, updated, previous_version + 1]
            heapq.heappush(heap, (-updated, other['baseline_order'], other_id, previous_version + 1))
    return ordered


def content_fingerprint(card, memberships):
    return stable_key(VERSION, card['id'], card['revision'], card['start_fen'], card['moves_json'],
                      card.get('trained_color'), card.get('kind'), card.get('content_type'), card.get('source_ref'), card.get('source_fen'), sorted(set(memberships)))
