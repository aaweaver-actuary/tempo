"""Executable synthetic publication shapes for the owned recovery acceptance.

Only authored fixture data is generated here. No accepted effects, scheduling
receipts, review results, or issuance authority are manufactured.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import random

import chess

from app.services.opening_graph import decision_segments


@dataclass(frozen=True)
class Shape:
    color: str
    routes: int
    authored_plies: int
    max_route_plies: int
    memberships: int
    graphs: int
    graph_plies: int
    max_card_plies: int


# Counts/length aggregates from the coordinator's bounded read-only G0 receipts.
# Private repertoire names, moves and game content are not inputs to this fixture.
OBSERVED = (
    Shape('white', 243, 7052, 71, 1322, 3160, 7049, 5),
    Shape('white', 197, 7261, 75, 1452, 3139, 7259, 7),
    Shape('black', 735, 16119, 60, 3070, 5864, 16112, 8),
    Shape('white', 675, 25163, 77, 4394, 10883, 25141, 7),
    Shape('white', 722, 25091, 77, 5280, 10742, 25073, 7),
    Shape('white', 1573, 31206, 49, 1266, 10748, 31194, 13),
    Shape('white', 271, 10737, 71, 1891, 4424, 10735, 9),
    Shape('black', 1032, 31578, 62, 5160, 10697, 31562, 12),
    Shape('white', 613, 19582, 129, 2807, 7672, 19581, 9),
    Shape('black', 2207, 68446, 66, 9616, 27600, 68434, 8),
)


def position(board):
    return ' '.join(board.fen(en_passant='legal').split()[:4])


def _route_lengths(shape, *, existing_custom=False):
    """Match authored/graph totals, including observed trailing opponent cues."""
    offset = int(existing_custom)
    count = shape.routes - offset
    trailing = shape.authored_plies - shape.graph_plies
    # Two observed aggregates include an additional unfinished/reused segment
    # beyond a single trailing opponent cue. Use the nearest coherent parity
    # (one fewer graph ply) and retain the explicit reported payload delta.
    trailing += (shape.authored_plies + (shape.routes if shape.color == 'white' else 0) - trailing) % 2
    # The retained custom white control is one learner move and graph use.
    authored = shape.authored_plies - offset
    own_end_parity = 1 if shape.color == 'white' else 0
    maximum_is_trailing = int(shape.max_route_plies % 2 != own_end_parity)
    assert maximum_is_trailing <= trailing < count
    values = [shape.max_route_plies]
    for index in range(1, count):
        is_trailing = index <= trailing - maximum_is_trailing
        parity = 1 - own_end_parity if is_trailing else own_end_parity
        values.append(12 + int(12 % 2 != parity))
    remaining = authored - sum(values)
    assert remaining >= 0 and remaining % 2 == 0
    cursor = 1
    while remaining:
        index = cursor % count
        if index and values[index] + 2 <= shape.max_route_plies:
            values[index] += 2
            remaining -= 2
        cursor += 1
        assert cursor < count * shape.max_route_plies
    assert sum(values) == authored and max(values) == shape.max_route_plies
    assert sum(value % 2 != own_end_parity for value in values) == trailing
    return values


def _prefix_decisions(shape, lengths, *, existing_custom=False):
    owns = [(length + (shape.color == 'white')) // 2 for length in lengths]
    extras = sum(owns) - (shape.graphs - int(existing_custom))
    maximum = (shape.max_card_plies + int(shape.color == 'white')) // 2
    values = [1] * len(lengths)
    overflow = max(0, extras - len(values) * (maximum - 1))
    # The retained one-move custom control replaces an observed multi-own
    # prefix. Preserve exact route/graph totals with at most two real prefixes
    # one learner decision longer; report the two-ply payload maximum delta.
    assert not overflow or (existing_custom and overflow <= 2)
    caps = [maximum + int(index < overflow) for index in range(len(values))]
    assert caps[0] - 1 <= extras <= sum(cap - 1 for cap in caps)
    values[0] = caps[0]
    extras -= caps[0] - 1
    cursor = 0
    while extras:
        index = cursor % len(values)
        if values[index] < min(caps[index], owns[index]):
            values[index] += 1
            extras -= 1
        cursor += 1
    return values


def _route(length, color, seed, repertoire_ordinal):
    """One-own-response policy, with legal opponent branches and no repeated root."""
    for retry in range(100):
        rng = random.Random(seed * 101 + retry)
        board, moves, seen = chess.Board(), [], set()
        try:
            for ply in range(length):
                key = position(board)
                seen.add(key)
                if ply < 4:
                    uci = ('e2e4', 'e7e5', 'g1f3', 'b8c6')[ply]
                    move = chess.Move.from_uci(uci)
                    assert move in board.legal_moves
                else:
                    choices = list(board.legal_moves)
                    assert choices
                    own = board.turn == (color == 'white')
                    # Own choices depend only on exact position; aliases and
                    # different routes cannot introduce conflicting responses.
                    if own:
                        choices.sort(key=lambda move: (
                            board.is_capture(move),
                            hashlib.sha256((key + ':' + move.uci()).encode()).digest()))
                    else:
                        rng.shuffle(choices)
                        choices.sort(key=board.is_capture)
                        if ply == (5 if color == 'white' else 4):
                            # Retain a real standard-root shared prefix, then
                            # distinguish selected repertoires by one authored
                            # opponent response. All routes remain present;
                            # another repertoire cannot accidentally bind this
                            # fixture's target to a different physical owner.
                            choices.sort(key=lambda candidate: (board.is_capture(candidate), candidate.uci()))
                            assert repertoire_ordinal < len(choices)
                            choices = [choices[repertoire_ordinal]]
                    move = None
                    for candidate in choices:
                        board.push(candidate)
                        acceptable = not board.is_game_over() and (own or position(board) not in seen)
                        board.pop()
                        if acceptable:
                            move = candidate
                            break
                    assert move is not None
                    board.push(move)
                    repeated = position(board) in seen
                    board.pop()
                    assert not repeated
                board.push(move)
                moves.append(move.uci())
            return tuple(moves)
        except AssertionError:
            continue
    raise AssertionError('synthetic_legal_route_generation_exhausted')


def publication(shape, repertoire_id, namespace, *, ordinal=0, existing_custom=False,
                _variations=None, _iteration=0):
    """Retain complete executable scope; aliases share physical short cards.

    Route and graph counts/total authored plies match the observed shape. Exact
    physical membership count is reported rather than padded with inert cards.
    The custom control is one retained route, not a replacement for the standard
    root target selected below.
    """
    offset = int(existing_custom)
    lengths = _route_lengths(shape, existing_custom=existing_custom)
    prefixes = _prefix_decisions(shape, lengths, existing_custom=existing_custom)
    average_graphs = shape.graphs / shape.routes
    unique = (_variations if _variations is not None else
              min(len(lengths), max(1, math.ceil(shape.memberships / max(average_graphs - 2, 1)))))
    longest = [max(lengths[index::unique]) for index in range(unique)]
    sources = [_route(length, shape.color, ordinal * 10_000 + index + 1, ordinal)
               for index, length in enumerate(longest)]
    lines, graphs, cards = [], [], {}
    target = None
    for index, (length, prefix_decisions) in enumerate(zip(lengths, prefixes)):
        moves = sources[index % unique][:length]
        route_id = f'{repertoire_id}-route-{index:05d}'
        lines.append(dict(id=route_id, repertoire_id=repertoire_id, trained_color=shape.color,
                          start_fen=chess.STARTING_FEN, moves_json=json.dumps(moves)))
        segments = decision_segments(chess.STARTING_FEN, moves, shape.color, prefix_decisions)
        ply = 0
        for segment in segments:
            executable = segment.moves
            card_id = segment.card_id
            cards.setdefault(card_id, dict(id=card_id, repertoire_id=repertoire_id,
                start_fen=segment.starting_fen, moves_json=json.dumps(executable),
                trained_color=shape.color, kind='prefix' if segment.segment_kind == 'prefix' else 'response'))
            graphs.append(dict(repertoire_id=repertoire_id, line_id=route_id,
                decision_index=segment.decision_index, card_id=card_id, starting_fen=segment.starting_fen,
                moves_json=json.dumps(executable), trained_color=shape.color,
                segment_kind=segment.segment_kind, first_decision_index=segment.first_decision_index,
                last_decision_index=segment.last_decision_index, parent_card_id=segment.parent_card_id,
                decision_fen_key=segment.decision_fen_keys[-1],
                decision_fen_keys_json=json.dumps(segment.decision_fen_keys)))
            if target is None and shape.color == 'white' and ply <= 8 < ply + len(executable):
                offset_in_card = 8 - ply
                before = chess.Board(segment.starting_fen)
                for uci in executable[:offset_in_card]:
                    before.push_uci(uci)
                wrong = next(move.uci() for move in before.legal_moves if move.uci() != executable[offset_in_card])
                target = dict(card_id=card_id, repertoire_id=repertoire_id, occurrence_ply=9,
                    prefix=list(moves[:8]), expected=executable[offset_in_card], wrong=wrong,
                    decision_offset=offset_in_card, decision_prefix=list(executable[:offset_in_card]),
                    starting_fen=chess.STARTING_FEN)
            ply += len(executable)
    if shape.routes >= 100 and _iteration < 3 and (len(cards) + offset > 10_000
            or not .85 <= (len(cards) + offset) / shape.memberships <= 1.15):
        adjusted = min(len(lengths), max(1, round(unique * min(shape.memberships, 9500) / (len(cards) + offset))))
        if adjusted != unique:
            return publication(shape, repertoire_id, namespace, ordinal=ordinal,
                existing_custom=existing_custom, _variations=adjusted, _iteration=_iteration + 1)
    assert len(graphs) + offset == shape.graphs
    assert abs(sum(len(json.loads(row['moves_json'])) for row in graphs) + offset - shape.graph_plies) <= 1
    assert len(cards) + offset <= 10_000, 'synthetic_publication_exceeds_existing_owner_card_bound'
    return dict(lines=lines, cards=list(cards.values()), graphs=graphs, target=target,
        observed=shape.__dict__, generated=dict(routes=len(lines) + offset,
            authored_plies=sum(lengths) + offset, memberships=len(cards) + offset,
            graphs=len(graphs) + offset, graph_plies=sum(len(json.loads(row['moves_json'])) for row in graphs) + offset,
            max_card_plies=max(len(json.loads(row['moves_json'])) for row in cards.values()),
            unique_authored_variations=unique,
            longer_prefix_occurrences=sum(2 * prefix - int(shape.color == 'white') > shape.max_card_plies for prefix in prefixes)))
