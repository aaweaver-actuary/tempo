"""Pure repertoire lookup over supplied immutable source values.

Position identity is deliberately separate from physical card identity. Invalid
routes retain their legal prefix with a diagnostic, never a complete certificate.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import chess
from tempo_evidence_math import opening_position_key

from app.contracts.product_v02.repertoire import DecisionPresentation, RepertoireSnapshot


@dataclass(frozen=True, order=True)
class PositionKey:
    repertoire_id: str
    variant: str
    position: str


def position_key(repertoire_id: str, fen: str, variant: str = "standard") -> PositionKey:
    if variant != "standard":
        raise ValueError("unsupported_variant")
    board = chess.Board(fen)
    if not board.is_valid():
        raise ValueError("invalid_position")
    return PositionKey(repertoire_id, variant, opening_position_key(fen))


@dataclass(frozen=True, order=True)
class Diagnostic:
    repertoire_id: str
    route_id: str
    ply: int
    code: str


@dataclass(frozen=True)
class RouteOccurrence:
    key: PositionKey
    route_id: str
    policy_id: str
    source_revision: str
    learner_color: str
    ply: int
    full_fen: str
    starting_fen: str
    route_moves: tuple[str, ...]
    next_move: str | None
    learner_turn: bool
    terminal: bool
    route_complete: bool
    bindings: tuple[DecisionPresentation, ...]
    reentry_authority_id: str | None

    @property
    def history(self) -> tuple[str, ...]:
        return self.route_moves[:self.ply]

    @property
    def binding(self) -> DecisionPresentation | None:
        return self.bindings[0] if len(self.bindings) == 1 else None


@dataclass(frozen=True)
class RouteAdjacency:
    before: RouteOccurrence
    after: RouteOccurrence
    intervening_moves: tuple[str, ...]


@dataclass(frozen=True)
class PositionNode:
    key: PositionKey
    occurrences: tuple[RouteOccurrence, ...]
    own_responses: tuple[str, ...]
    opponent_edges: tuple[str, ...]

    @property
    def conflict(self) -> bool:
        return len(self.own_responses) > 1

    @property
    def selected_response(self) -> str | None:
        return self.own_responses[0] if len(self.own_responses) == 1 else None


@dataclass(frozen=True)
class PositionIndex:
    snapshot_id: str
    source_revision: str
    revision: int
    digest: str
    captured_at: str
    scope_id: str
    scope_repertoire_ids: tuple[str, ...]
    available: bool
    complete: bool
    nodes: Mapping[PositionKey, PositionNode]
    routes: Mapping[tuple[str, str], tuple[RouteOccurrence, ...]]
    diagnostics: tuple[Diagnostic, ...]
    adjacency: tuple[RouteAdjacency, ...]

    def lookup(self, repertoire_id: str, fen: str, variant: str = "standard") -> PositionNode | None:
        return self.nodes.get(position_key(repertoire_id, fen, variant))

    def reentry_candidate(self, repertoire_id: str, fen: str,
                          variant: str = "standard") -> RouteOccurrence | None:
        if not self.complete:
            return None
        node = self.lookup(repertoire_id, fen, variant)
        if node is None or node.conflict or node.selected_response is None:
            return None
        candidates = [item for item in node.occurrences if item.learner_turn and item.next_move]
        # Reentry requires one route occurrence and one explicit current mapping.
        if len(candidates) != 1:
            return None
        item = candidates[0]
        binding = item.binding
        if (not item.route_complete or binding is None or not binding.decision_id or not binding.presentation_id
                or not item.reentry_authority_id):
            return None
        return item


def build_index(snapshot: RepertoireSnapshot) -> PositionIndex:
    """Copy the public T01 snapshot into a deeply immutable exact-position index."""
    if (len(snapshot.membership.routes) > 10000
            or sum(len(route.moves) for route in snapshot.membership.routes) > 100000):
        raise ValueError("snapshot_index_exceeds_bound")
    # Revalidate even frozen models: legacy membership contains mutable lists.
    source = RepertoireSnapshot.model_validate(snapshot.model_dump(mode="python"))
    membership = source.membership
    boundaries = {item.route_id: item for item in source.boundaries}
    mappings = defaultdict(list)
    for item in source.decision_presentations:
        mappings[item.repertoire_id, item.position_key, item.selected_response_uci].append(item)
    nodes = defaultdict(list)
    routes = {}
    diagnostics = []
    adjacency = []
    source_revision = membership.source_revision
    for route in sorted(membership.routes, key=lambda item: (item.repertoire_id, item.route_id)):
        raw = route.model_dump(mode="python")
        repertoire_id, route_id = raw["repertoire_id"], raw["route_id"]
        identity = repertoire_id, route_id
        variant = source.variant
        def diagnose(code, ply=0):
            diagnostics.append(Diagnostic(repertoire_id, route_id, ply, code))
        if identity in routes:
            diagnose("duplicate_route")
            continue
        routes[identity] = ()
        if variant != "standard":
            diagnose("unsupported_variant")
            continue
        try:
            board = chess.Board(raw["starting_fen"])
            if not board.is_valid() or raw["learner_color"] not in {"white", "black"}:
                raise ValueError("invalid route")
        except (ValueError, KeyError, TypeError):
            diagnose("invalid_source_position")
            continue
        moves = tuple(raw["moves"])
        if len(moves) > 1000:
            diagnose("route_exceeds_bound")
            continue
        frames = [raw["starting_fen"]]
        valid_moves = []
        for ply, uci in enumerate(moves):
            try:
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError("illegal move")
                board.push(move)
            except (ValueError, TypeError):
                diagnose("illegal_move", ply)
                break
            valid_moves.append(uci)
            frames.append(board.fen(en_passant="fen"))
        complete = boundaries[route_id].deliberate and len(valid_moves) == len(moves)
        if not boundaries[route_id].deliberate:
            diagnose("incomplete_route", len(valid_moves))
        authorities = defaultdict(list)
        for authority in route.reentries:
            if not 0 <= authority.ply < len(valid_moves):
                diagnose("invalid_reentry_ply", authority.ply)
            else:
                authorities[authority.ply].append(authority.authority_id)
        occurrences = []
        legal_moves = tuple(valid_moves)
        for ply, fen in enumerate(frames):
            current = chess.Board(fen)
            own_turn = current.turn == (raw["learner_color"] == "white")
            next_move = valid_moves[ply] if ply < len(valid_moves) else None
            raw_bindings = mappings[repertoire_id, opening_position_key(fen), next_move]
            bindings = tuple({item.model_dump_json(): item for item in raw_bindings}.values())
            if len(bindings) > 1:
                diagnose("ambiguous_physical_mapping", ply)
            if bindings and not own_turn:
                diagnose("binding_on_opponent_turn", ply)
                bindings = ()
            occurrence = RouteOccurrence(
                position_key(repertoire_id, fen, variant), route_id, route.policy_id,
                source_revision, raw["learner_color"], ply,
                fen, raw["starting_fen"], legal_moves,
                next_move, own_turn, complete and ply == len(valid_moves),
                complete, bindings, authorities[ply][0] if len(authorities[ply]) == 1 else None)
            occurrences.append(occurrence)
            nodes[occurrence.key].append(occurrence)
        routes[identity] = tuple(occurrences)
        decisions = [item for item in occurrences if item.learner_turn and item.next_move]
        adjacency.extend(RouteAdjacency(before, after,
            tuple(valid_moves[before.ply:after.ply])) for before, after in zip(decisions, decisions[1:]))
    frozen_nodes = {}
    for key, occurrences in sorted(nodes.items()):
        own = tuple(sorted({item.next_move for item in occurrences if item.learner_turn and item.next_move}))
        opponent = tuple(sorted({item.next_move for item in occurrences if not item.learner_turn and item.next_move}))
        node = PositionNode(key, tuple(occurrences), own, opponent)
        frozen_nodes[key] = node
        if node.conflict:
            diagnostics.extend(Diagnostic(key.repertoire_id, item.route_id, item.ply, "own_response_conflict")
                               for item in occurrences if item.learner_turn and item.next_move)
    return PositionIndex(membership.snapshot_id, source_revision, membership.revision,
        membership.digest, membership.captured_at, membership.scope_id,
        tuple(membership.scope_repertoire_ids), membership.available,
        bool(membership.complete and membership.available and not diagnostics),
        MappingProxyType(frozen_nodes), MappingProxyType(routes),
        tuple(sorted(set(diagnostics))), tuple(adjacency))
