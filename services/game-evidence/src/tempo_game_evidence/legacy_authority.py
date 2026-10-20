"""Observed legacy graph authority, without a Content publication or activation.

This is a comparison input only. The backend owner must capture coherent rows
and recheck the token at acceptance; validating an offline document grants no
permission to deliver, issue an attempt, or change Study state.
"""
from datetime import datetime
import hashlib
import json
from typing import Annotated, Literal

import chess
from pydantic import Field, model_validator
from tempo_chess_core import canonical_fen_key
from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken

from .snapshot import StrictModel, RouteV2, compile_routes, verify_route_definitions


Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Move = Annotated[str, Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")]


def authority_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class PhysicalCard(StrictModel):
    card_id: Identifier
    card_revision: int = Field(ge=1)
    decision_id: Identifier
    policy_id: Identifier
    trained_color: Literal["white", "black"]
    starting_fen: str = Field(min_length=1, max_length=120)
    moves: list[Move] = Field(min_length=1, max_length=40)
    repertoire_ids: list[Identifier] = Field(min_length=1, max_length=100)
    introduced: bool

    def definition(self):
        # Keep the complete physical executable, including an opponent prelude.
        # Shared projected_definition selects the particular learner decision.
        return {"card_id": self.card_id, "decision_id": self.decision_id,
                "policy_id": self.policy_id, "starting_fen": self.starting_fen,
                "specification": {"type": "move_line", "grading_policy": "reference",
                    "mode": "single" if len(self.moves) == 1 else "stepwise_line",
                    "accepted_lines": [self.moves]}}


class CatalogueRoute(StrictModel):
    repertoire_id: Identifier
    route_id: Identifier
    policy_id: Identifier
    learner_color: Literal["white", "black"]
    starting_fen: str = Field(min_length=1, max_length=120)
    moves: list[Move] = Field(max_length=1000)


def route_identity(route):
    return route.repertoire_id, route.route_id, route.policy_id


def policy_anchors(catalogue):
    """Ordered-prefix controls, including competing policies outside the target."""
    roots = {}
    for route in catalogue:
        board = chess.Board(route.starting_fen)
        if not board.is_valid():
            raise ValueError("invalid_catalogue_board")
        root = (route.learner_color, canonical_fen_key(board.fen(en_passant="legal")))
        node = roots.setdefault(root, {"policies": set(), "moves": {}})
        node["policies"].add((route.repertoire_id, route.policy_id))
        for move in route.moves:
            board.push_uci(move)
            node = node["moves"].setdefault(move, {"policies": set(), "moves": {}})
            node["policies"].add((route.repertoire_id, route.policy_id))
    anchors = {}
    for route in catalogue:
        board = chess.Board(route.starting_fen)
        node = roots[(route.learner_color, canonical_fen_key(board.fen(en_passant="legal")))]
        policy = {(route.repertoire_id, route.policy_id)}
        anchor = 0 if node["policies"] == policy else None
        for ply, move in enumerate(route.moves, 1):
            node = node["moves"][move]
            if anchor is None and node["policies"] == policy:
                anchor = ply
        anchors[route_identity(route)] = anchor
    return anchors


class LegacyComparisonSnapshot(StrictModel):
    kind: Literal["legacy_authority_snapshot"]
    schema_version: Literal[1]
    captured_at: str
    authority: LegacyAuthorityToken
    observed_after: LegacyAuthorityToken
    catalogue_complete: bool
    catalogue: list[CatalogueRoute] = Field(min_length=1, max_length=10000)
    cards: list[PhysicalCard] = Field(max_length=10000)
    routes: list[RouteV2] = Field(max_length=10000)
    capture_digest: Digest

    def comparison_definitions(self):
        return {card.card_id: card.definition() for card in self.cards}

    def compiled_routes(self):
        return compile_routes(self.routes)

    def applicability_reason(self):
        if self.authority != self.observed_after:
            return "legacy_authority_changed_during_capture"
        if self.authority.graph_state != "ready":
            return "legacy_graph_not_ready"
        if not self.catalogue_complete:
            return "legacy_catalogue_incomplete"
        return None

    def definition_unavailable_reason(self, card_id):
        card = next(card for card in self.cards if card.card_id == card_id)
        return None if card.introduced else "legacy_card_not_introduced"

    def finding_authority(self, card_id):
        card = next(card for card in self.cards if card.card_id == card_id)
        return {"learning_card_id": card.card_id, "card_revision": card.card_revision,
                "authority_kind": "legacy_graph", "authority": self.authority.model_dump(mode="json"),
                "authority_digest": authority_digest(self.authority.model_dump(mode="json")),
                "capture_digest": self.capture_digest,
                "executable_digest": authority_digest({"starting_fen": card.starting_fen,
                    "moves": card.moves, "trained_color": card.trained_color})}

    def finding_context(self, game):
        if game["workspace_id"] != self.authority.workspace_id:
            raise ValueError("legacy_game_workspace_mismatch")
        return {"target_id": self.authority.target_id, "workspace_id": self.authority.workspace_id,
                "repertoire_id": self.authority.repertoire_id,
                "applicability": "current_at_capture" if self.applicability_reason() is None else "unknown_or_stale",
                "captured_at": self.captured_at}

    def finding_route_context(self, route_contexts):
        # Authored authority includes routes with no eligible physical binding.
        # Filtering those routes before comparison would invent repertoire misses.
        applicable = [(route, ply) for route, ply in route_contexts
            if route.authority_start_ply is not None and ply >= route.authority_start_ply
            and ply < len(route.moves)]
        return {"authored_expected_moves": sorted({route.moves[ply] for route, ply in applicable}),
                "physical_binding_complete": bool(applicable) and all(
                    any(binding.ply == ply for binding in route.bindings) for route, ply in applicable)}

    def physical_comparison_reason(self, selected, observed_move, route_context):
        physical_moves = set(selected[3])
        if observed_move not in physical_moves and (
                not route_context["physical_binding_complete"]
                or set(route_context["authored_expected_moves"]) != physical_moves):
            return "legacy_authored_authority_exceeds_physical_binding"
        return None

    def comparison_scope_reason(self, game, position):
        return None

    @model_validator(mode="after")
    def coherent_physical_authority(self):
        captured_at = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError("legacy_capture_requires_timezone")
        before, after = self.authority, self.observed_after
        if (before.target_id, before.workspace_id, before.repertoire_id) != (
                after.target_id, after.workspace_id, after.repertoire_id):
            raise ValueError("legacy_capture_target_changed")
        if authority_digest([route.model_dump(mode="json") for route in self.catalogue]) != before.catalogue_digest:
            raise ValueError("legacy_catalogue_digest_mismatch")
        if authority_digest(self.model_dump(mode="json", exclude={"capture_digest"})) != self.capture_digest:
            raise ValueError("legacy_capture_digest_mismatch")
        return verify_legacy_physical_routes(self, policy_anchors(self.catalogue))


def verify_legacy_physical_routes(snapshot, anchors):
    catalogue = {route_identity(route): route for route in snapshot.catalogue}
    if len(catalogue) != len(snapshot.catalogue):
        raise ValueError("duplicate_catalogue_route")
    cards = {card.card_id: card for card in snapshot.cards}
    if len(cards) != len(snapshot.cards):
        raise ValueError("duplicate_physical_card")
    for card in snapshot.cards:
        if card.repertoire_ids != [snapshot.authority.repertoire_id]:
            raise ValueError("legacy_shared_physical_card_unsupported")
        board = chess.Board(card.starting_fen)
        if not board.is_valid():
            raise ValueError("invalid_physical_card_board")
        for move in card.moves:
            board.push_uci(move)
    selected = {identity for identity in catalogue if identity[0] == snapshot.authority.repertoire_id}
    if {route_identity(route) for route in snapshot.routes} != selected:
        raise ValueError("legacy_selected_route_inventory_incomplete")
    for route in snapshot.routes:
        identity = route_identity(route)
        source = catalogue[identity]
        if (route.learner_color, route.starting_fen, route.moves) != (
                source.learner_color, source.starting_fen, source.moves):
            raise ValueError("legacy_route_source_mismatch")
        if route.authority_start_ply != anchors[identity]:
            raise ValueError("legacy_policy_anchor_mismatch")
        board = chess.Board(route.starting_fen)
        positions = [canonical_fen_key(board.fen(en_passant="legal"))]
        for move in route.moves:
            board.push_uci(move)
            positions.append(canonical_fen_key(board.fen(en_passant="legal")))
        physical_roots = {}
        for binding in route.bindings:
            card = cards.get(binding.card_id)
            root = binding.ply - binding.exercise_ply
            if card is not None and card.card_id not in physical_roots:
                physical_position = canonical_fen_key(chess.Board(card.starting_fen).fen(en_passant="legal"))
                physical_roots[card.card_id] = [index for index, position in enumerate(positions)
                    if position == physical_position and route.moves[index:index + len(card.moves)] == card.moves]
            if (card is None or binding.reentry or root < 0 or root >= len(positions)
                    or card.policy_id != route.policy_id or card.trained_color != route.learner_color
                    or physical_roots[card.card_id] != [root]):
                raise ValueError("legacy_exact_physical_route_binding_required")
    verify_route_definitions(snapshot.routes, snapshot.comparison_definitions(), route_identity)
    return snapshot
