"""Game-bound owner preparation over complete authored scope and candidate reads.

This value owns no database, currentness, learning or provider authority. Complete
routes are retained even when only the elected boundary's physical bindings are
read. Legal execution uses exact in-memory position identity, never persisted IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from time import perf_counter
from types import MappingProxyType
from typing import Mapping

import chess

from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_game_review_contracts import CanonicalGame, replay_line

from app.contracts.product_v02.repertoire import RepertoireSnapshot

from .snapshot import _mappings, _project_repertoire, RepertoireSnapshotBoundExceeded


def _position(board):
    """Exact standard valid-board counterpart of the canonical legal-EP FEN.

    Public bitboards retain each piece/color, turn and cleaned castling rights.
    Clocks and promotion provenance are absent from ordinary repertoire identity.
    """
    return (board.pawns, board.knights, board.bishops, board.rooks, board.queens,
            board.kings, board.occupied_co[chess.WHITE], board.occupied_co[chess.BLACK],
            board.turn, board.clean_castling_rights(),
            board.ep_square if board.has_legal_en_passant() else None)


def _game_keys(game):
    board = chess.Board(game.starting_fen)
    frames = replay_line(game.starting_fen, game.moves)
    keys = {}
    for frame in frames:
        keys[_position(board)] = frame['comparison_key']
        if frame['move'] is not None:
            board.push_uci(frame['move'])
    return MappingProxyType(keys)


class _Node:
    __slots__ = ('children', 'position', 'turn')

    def __init__(self):
        self.children = {}
        self.position = None
        self.turn = None


def _legal_routes(routes, *, active):
    """One invocation-local prefix tree; retain no mutable Board in the result."""
    started = perf_counter()
    paths, roots = {}, {}
    input_plies = nodes = replayed = batches = 0
    def account(value):
        nonlocal batches
        # Enforce the existing source work ceiling inside long traversal too.
        if value > 100000:
            raise RepertoireSnapshotBoundExceeded(stage='projection', sample_complete=True,
                violations=[dict(dimension='authored_moves', observed=value, limit=100000,
                                 observation='exact')])
        if value and value % 128 == 0:
            batches += 1
    for route in routes:
        if not active:
            continue
        root = roots.setdefault(route.starting_fen, _Node())
        path, node = [root], root
        for move in route.moves:
            input_plies += 1
            account(input_plies)
            if move not in node.children:
                node.children[move] = _Node()
                nodes += 1
            node = node.children[move]
            path.append(node)
        paths[route.route_id] = tuple(path)
    for start, root in roots.items():
        try:
            board = chess.Board(start)
            if not board.is_valid():
                continue
        except ValueError:
            continue
        root.position, root.turn = _position(board), board.turn
        stack = [iter(root.children.items())]
        while stack:
            try:
                uci, node = next(stack[-1])
            except StopIteration:
                stack.pop()
                if stack:
                    board.pop()
                continue
            replayed += 1
            account(replayed)
            try:
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    continue
            except ValueError:
                continue
            board.push(move)
            node.position, node.turn = _position(board), board.turn
            stack.append(iter(node.children.items()))
    values = {}
    for route_id, path in paths.items():
        values[route_id] = (None if any(node.position is None for node in path) else
                           tuple((node.position, node.turn) for node in path))
    return MappingProxyType(values), MappingProxyType(dict(
        input_plies=input_plies, prefix_nodes=nodes, replayed_nodes=replayed,
        accounting_batches=batches, elapsed_seconds=perf_counter() - started,
        work_limit_plies=100000, accounting_interval_nodes=128))


def _comparison_routes(execution, routes, game_positions):
    """Intern detached read-only exact aliases once, retaining every route ID."""
    started = perf_counter()
    result, aliases = {}, {}
    count = 0
    for route in routes:
        frames = execution.get(route.route_id)
        if frames is None:
            result[route.route_id] = None
            continue
        key = (route.starting_fen, tuple(route.moves))
        if key not in aliases:
            aliases[key] = tuple(MappingProxyType(dict(
                comparison_key=game_positions.get(position, ''),
                turn='white' if turn else 'black',
                move=route.moves[ply] if ply < len(route.moves) else None))
                for ply, (position, turn) in enumerate(frames))
            count += len(aliases[key])
        result[route.route_id] = aliases[key]
    return MappingProxyType(result), count, perf_counter() - started


@dataclass(frozen=True, init=False)
class PreparedFirstBoundaryRepertoire:
    """Repertoire-owned exact-game scope; callers supply source facts, not proof.

    Complete authored source remains immutable. Candidate bindings are a separate
    complete owner read, explicitly named in the digest, never a whole-catalogue
    claim. The Study owner independently runs policy and fences current reads.
    """
    _authored_json: str
    _candidate_json: str | None
    _game_json: str
    _route_execution: Mapping
    _game_positions: Mapping
    _comparison_frames: Mapping
    _route_values: Mapping
    work: Mapping
    snapshot: RepertoireSnapshot

    def __init__(self, facts: Mapping, *, game: CanonicalGame, captured_at: str | None = None):
        started = perf_counter()
        game = CanonicalGame.model_validate(game.model_dump(mode='python'))
        encoded = json.dumps(facts, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        owned = json.loads(encoded)
        if set(owned) != {'repertoire', 'lines', 'card_count', 'membership_count', 'graph_count'}:
            raise ValueError('invalid_first_boundary_authored_scope')
        for field in ('card_count', 'membership_count', 'graph_count'):
            if type(owned[field]) is not int or owned[field] < 0:
                raise ValueError('invalid_first_boundary_authored_scope')
        for field, dimension in (('card_count', 'card_rows'), ('membership_count', 'membership_rows')):
            if owned[field] > 10000:
                raise RepertoireSnapshotBoundExceeded(stage='projection', sample_complete=True,
                    violations=[dict(dimension=dimension, observed=owned[field], limit=10000,
                                     observation='exact')])
        owned['lines'].sort(key=lambda row: row['id'])
        encoded = json.dumps(owned, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        raw = owned | dict(cards=[], memberships=[], graphs=[])
        snapshot = _project_repertoire(raw, captured_at=captured_at, published=True)
        if snapshot.membership.workspace_id != game.workspace_id:
            raise ValueError('first_boundary_workspace_mismatch')
        execution, work = _legal_routes(snapshot.membership.routes,
            active=snapshot.learner_color == game.learner_color)
        object.__setattr__(self, '_authored_json', encoded)
        object.__setattr__(self, '_candidate_json', None)
        object.__setattr__(self, '_game_json', game.model_dump_json())
        object.__setattr__(self, '_route_execution', execution)
        game_positions = _game_keys(game)
        comparison_frames, frame_count, frame_seconds = _comparison_routes(
            execution, snapshot.membership.routes, game_positions)
        object.__setattr__(self, '_game_positions', game_positions)
        object.__setattr__(self, '_comparison_frames', comparison_frames)
        object.__setattr__(self, '_route_values', MappingProxyType({
            route.route_id: route for route in snapshot.membership.routes}))
        object.__setattr__(self, 'snapshot', self._snapshot_identity(snapshot, owned, None, game))
        object.__setattr__(self, 'work', MappingProxyType(dict(work,
            route_replay_seconds=work['elapsed_seconds'], projected_frame_records=frame_count,
            frame_projection_seconds=frame_seconds, elapsed_seconds=perf_counter() - started)))

    @staticmethod
    def _snapshot_identity(snapshot, authored, candidate, game):
        digest = fingerprint(dict(kind='game_bound_first_boundary_repertoire', schema_version=1,
            authored=authored, candidate_bindings=candidate, game=game.model_dump(mode='json')))
        return snapshot.model_copy(update={'membership': snapshot.membership.model_copy(update={
            'snapshot_id': 'first-boundary:' + digest, 'source_revision': digest, 'digest': digest,
            'scope_id': 'first-boundary:' + authored['repertoire']['id']})})

    def authored_facts(self):
        return json.loads(self._authored_json)

    def candidate_facts(self):
        return None if self._candidate_json is None else json.loads(self._candidate_json)

    def for_game(self, game):
        return type(game) is CanonicalGame and game.model_dump_json() == self._game_json

    def accepts(self, snapshot):
        return (snapshot.membership == self.snapshot.membership
                and snapshot.variant == self.snapshot.variant
                and snapshot.learner_color == self.snapshot.learner_color
                and snapshot.boundaries == self.snapshot.boundaries)

    def replay_route(self, game, route):
        if not self.for_game(game) or self._route_values.get(route.route_id) != route:
            raise ValueError('first_boundary_prepared_scope_mismatch')
        frames = self._comparison_frames.get(route.route_id)
        if frames is None:
            raise ValueError('invalid_authored_scope')
        return frames


    def with_bindings(self, facts):
        """Bind a complete candidate read; missing candidate routes stay unknown."""
        encoded = json.dumps(facts, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        owned = json.loads(encoded)
        if set(owned) != {'repertoire', 'route_ids', 'cards', 'memberships', 'graphs'}:
            raise ValueError('invalid_first_boundary_candidate_scope')
        authored = self.authored_facts()
        if owned['repertoire'] != authored['repertoire']:
            raise ValueError('first_departure_selected_publication_changed')
        route_ids = owned['route_ids']
        if (not route_ids or route_ids != sorted(set(route_ids))
                or not set(route_ids) <= set(self._route_values)):
            raise ValueError('invalid_candidate_route_scope')
        owned['cards'].sort(key=lambda row: row['id'])
        owned['memberships'].sort(key=lambda row: (row['repertoire_id'], row['card_id']))
        owned['graphs'].sort(key=lambda row: (row['line_id'], row['decision_index']))
        encoded = json.dumps(owned, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        repertoire_id = authored['repertoire']['id']
        if any(graph['line_id'] not in route_ids or graph['repertoire_id'] != repertoire_id
               for graph in owned['graphs']):
            raise ValueError('first_departure_candidate_routes_changed')
        if len(owned['cards']) > 10000 or len(owned['memberships']) > 10000:
            raise ValueError('first_boundary_candidate_scope_exceeds_bound')
        mappings = _mappings(dict(tables=dict(cards=owned['cards'], repertoire_cards=owned['memberships']),
            graphs=owned['graphs'], authority=dict(graph_generation=authored['repertoire']['graph_generation'])),
            {(repertoire_id, route_id): route for route_id, route in self._route_values.items()},
            self.snapshot.membership.revision, include_unavailable=True)
        result = object.__new__(type(self))
        for field in ('_authored_json', '_game_json', '_route_execution', '_game_positions', '_comparison_frames', '_route_values', 'work'):
            object.__setattr__(result, field, getattr(self, field))
        object.__setattr__(result, '_candidate_json', encoded)
        snapshot = self.snapshot.model_copy(update={'decision_presentations': mappings})
        object.__setattr__(result, 'snapshot', self._snapshot_identity(snapshot, authored, owned,
            CanonicalGame.model_validate_json(self._game_json)))
        return result
