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


def _spread(total, count, *, minimum=1, maximum=None):
    assert count and total >= count * minimum
    values = [total // count + (index < total % count) for index in range(count)]
    if maximum is not None:
        change = maximum - values[0]
        values[0] = maximum
        for index in range(1, count):
            amount = min(change, values[index] - minimum)
            values[index] -= amount
            change -= amount
            if not change:
                break
        assert change == 0
    assert sum(values) == total and min(values) >= minimum
    return values


def _route(length, color, seed):
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
    lengths = _spread(shape.authored_plies - offset, shape.routes - offset,
                      minimum=12, maximum=shape.max_route_plies)
    graph_counts = [max(6, round(length * shape.graphs / shape.authored_plies)) for length in lengths]
    difference = shape.graphs - offset - sum(graph_counts)
    cursor = 0
    while difference:
        index = cursor % len(graph_counts)
        if difference > 0 and graph_counts[index] < lengths[index]:
            graph_counts[index] += 1
            difference -= 1
        elif difference < 0 and graph_counts[index] > 6:
            graph_counts[index] -= 1
            difference += 1
        cursor += 1
        assert cursor < len(graph_counts) * 100
    assert sum(graph_counts) == shape.graphs - offset
    average_graphs = shape.graphs / shape.routes
    unique = (_variations if _variations is not None else
              min(len(lengths), max(1, math.ceil(shape.memberships / max(average_graphs - 2, 1)))))
    longest = [max(lengths[index::unique]) for index in range(unique)]
    sources = [_route(length, shape.color, ordinal * 10_000 + index + 1)
               for index, length in enumerate(longest)]
    lines, graphs, cards = [], [], {}
    target = None
    for index, (length, count) in enumerate(zip(lengths, graph_counts)):
        moves = sources[index % unique][:length]
        route_id = f'{repertoire_id}-route-{index:05d}'
        lines.append(dict(id=route_id, repertoire_id=repertoire_id, trained_color=shape.color,
                          start_fen=chess.STARTING_FEN, moves_json=json.dumps(moves)))
        # Every alias uses the same first-nine-ply partition. A standard-root
        # miss at ply9 therefore has one physical decision, not manufactured
        # uniqueness from dropping competing route bindings.
        chunks = [2, 2, 2, 2, 1] + _spread(length - 9, count - 5,
            maximum=shape.max_card_plies if index == 0 else None)
        assert sum(chunks) == length and len(chunks) == count
        board, ply = chess.Board(), 0
        for decision, size in enumerate(chunks):
            executable = moves[ply:ply + size]
            fen, key = board.fen(en_passant='legal'), position(board)
            identity = hashlib.sha256(json.dumps((shape.color, key, executable)).encode()).hexdigest()[:32]
            card_id = namespace + '-physical-' + identity
            cards.setdefault(card_id, dict(id=card_id, repertoire_id=repertoire_id, start_fen=fen,
                moves_json=json.dumps(executable), trained_color=shape.color))
            decision_keys = []
            for uci in executable:
                if board.turn == (shape.color == 'white'):
                    decision_keys.append(position(board))
                board.push_uci(uci)
            graphs.append(dict(repertoire_id=repertoire_id, line_id=route_id,
                decision_index=decision, card_id=card_id, starting_fen=fen,
                moves_json=json.dumps(executable), trained_color=shape.color,
                decision_fen_key=decision_keys[0] if decision_keys else key,
                decision_fen_keys_json=json.dumps(decision_keys)))
            if target is None and shape.color == 'white' and ply == 8:
                before = chess.Board(fen)
                wrong = next(move.uci() for move in before.legal_moves if move.uci() != executable[0])
                target = dict(card_id=card_id, repertoire_id=repertoire_id, occurrence_ply=9,
                    prefix=list(moves[:8]), expected=executable[0], wrong=wrong, starting_fen=chess.STARTING_FEN)
            ply += size
    if shape.routes >= 100 and _iteration < 3 and not .85 <= (len(cards) + offset) / shape.memberships <= 1.15:
        adjusted = min(len(lengths), max(1, round(unique * shape.memberships / (len(cards) + offset))))
        if adjusted != unique:
            return publication(shape, repertoire_id, namespace, ordinal=ordinal,
                existing_custom=existing_custom, _variations=adjusted, _iteration=_iteration + 1)
    assert len(cards) + offset <= 10_000, 'synthetic_publication_exceeds_existing_owner_card_bound'
    return dict(lines=lines, cards=list(cards.values()), graphs=graphs, target=target,
        observed=shape.__dict__, generated=dict(routes=len(lines) + offset,
            authored_plies=sum(lengths) + offset, memberships=len(cards) + offset,
            graphs=len(graphs) + offset, graph_plies=sum(lengths) + offset,
            max_card_plies=max(len(json.loads(row['moves_json'])) for row in cards.values()),
            unique_authored_variations=unique))
