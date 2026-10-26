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
class DecisionBinding:
    decision_id: str | None
    presentation_id: str | None
    card_id: str | None
    card_revision: int | None
    introduced_at: str | None
    introduced_before_game: bool | None
    reentry_authority_id: str | None


@dataclass(frozen=True)
class RouteOccurrence:
    key: PositionKey
    route_id: str
    source_revision: str
    learner_color: str
    ply: int
    full_fen: str
    starting_fen: str
    history: tuple[str, ...]
    next_move: str | None
    learner_turn: bool
    terminal: bool
    route_complete: bool
    bindings: tuple[DecisionBinding, ...]

    @property
    def binding(self) -> DecisionBinding | None:
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
    complete: bool
    nodes: Mapping[PositionKey, PositionNode]
    routes: Mapping[tuple[str, str], tuple[RouteOccurrence, ...]]
    diagnostics: tuple[Diagnostic, ...]
    adjacency: tuple[RouteAdjacency, ...]

    def lookup(self, repertoire_id: str, fen: str, variant: str = "standard") -> PositionNode | None:
        return self.nodes.get(position_key(repertoire_id, fen, variant))

    def reentry_candidate(self, repertoire_id: str, fen: str,
                          variant: str = "standard") -> RouteOccurrence | None:
        node = self.lookup(repertoire_id, fen, variant)
        if node is None or node.conflict or node.selected_response is None:
            return None
        candidates = [item for item in node.occurrences if item.learner_turn and item.next_move]
        # Reentry requires one route occurrence and one explicit current mapping.
        if len(candidates) != 1:
            return None
        item = candidates[0]
        binding = item.binding
        if (not item.route_complete or binding is None or not binding.card_id
                or not binding.decision_id or not binding.presentation_id
                or binding.card_revision is None or not binding.reentry_authority_id):
            return None
        return item


def _document(value):
    """Accept the contract model or a local fixture; retain no mutable inputs."""
    return value.model_dump(mode="python") if hasattr(value, "model_dump") else value


def _binding(raw) -> DecisionBinding:
    return DecisionBinding(**{name: raw.get(name) for name in DecisionBinding.__dataclass_fields__})


def build_index(snapshot) -> PositionIndex:
    source = _document(snapshot)
    nodes = defaultdict(list)
    routes = {}
    diagnostics = []
    adjacency = []
    source_revision = str(source["source_revision"])
    for raw in sorted(source["routes"], key=lambda row: (row["repertoire_id"], row["route_id"])):
        raw = _document(raw)
        repertoire_id, route_id = raw["repertoire_id"], raw["route_id"]
        identity = repertoire_id, route_id
        variant = raw.get("variant", "standard")
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
        complete = raw.get("complete", True) and len(valid_moves) == len(moves)
        if not raw.get("complete", True):
            diagnose("incomplete_route", len(valid_moves))
        raw_bindings = defaultdict(list)
        for binding in raw.get("bindings", ()):
            binding = _document(binding)
            ply = binding["ply"]
            if type(ply) is not int or not 0 <= ply < len(valid_moves):
                diagnose("invalid_binding_ply")
                continue
            raw_bindings[ply].append(_binding(binding))
        occurrences = []
        for ply, fen in enumerate(frames):
            current = chess.Board(fen)
            own_turn = current.turn == (raw["learner_color"] == "white")
            bindings = tuple(sorted(set(raw_bindings[ply]), key=repr))
            if len(bindings) > 1:
                diagnose("ambiguous_physical_mapping", ply)
            if bindings and not own_turn:
                diagnose("binding_on_opponent_turn", ply)
                bindings = ()
            occurrence = RouteOccurrence(
                position_key(repertoire_id, fen, variant), route_id,
                str(raw.get("source_revision", source_revision)), raw["learner_color"], ply,
                fen, raw["starting_fen"], tuple(valid_moves[:ply]),
                valid_moves[ply] if ply < len(valid_moves) else None, own_turn,
                complete and raw.get("terminal", True) and ply == len(valid_moves),
                complete, bindings)
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
    return PositionIndex(source["snapshot_id"], source_revision,
        bool(source.get("complete", True) and source.get("available", True) and not diagnostics),
        MappingProxyType(frozen_nodes), MappingProxyType(routes),
        tuple(sorted(set(diagnostics))), tuple(adjacency))
