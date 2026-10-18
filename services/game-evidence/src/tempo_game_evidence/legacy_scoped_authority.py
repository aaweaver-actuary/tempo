"""Decision-scoped legacy input backed by a complete owner traversal proof."""
from datetime import datetime
from typing import Literal

import chess
from pydantic import Field, model_validator
from tempo_chess_core import canonical_fen_key
from tempo_game_evidence_contracts.legacy_scope import LegacyScopedCatalogueProof

from .legacy_authority import (LegacyComparisonSnapshot, LegacyAuthorityToken,
    PhysicalCard, CatalogueRoute, authority_digest, route_identity, verify_legacy_physical_routes)
from .snapshot import StrictModel, RouteV2


class ScopedPhysicalCard(PhysicalCard):
    introduced_before_game: bool


class LegacyScopedComparisonSnapshot(StrictModel):
    kind: Literal["legacy_authority_snapshot"]
    schema_version: Literal[2]
    captured_at: str
    authority: LegacyAuthorityToken
    observed_after: LegacyAuthorityToken
    catalogue_scope: LegacyScopedCatalogueProof
    # These are retained candidates, never labelled a complete global catalogue.
    catalogue: list[CatalogueRoute] = Field(max_length=64)
    cards: list[ScopedPhysicalCard] = Field(max_length=128)
    routes: list[RouteV2] = Field(max_length=64)
    capture_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    comparison_definitions = LegacyComparisonSnapshot.comparison_definitions
    compiled_routes = LegacyComparisonSnapshot.compiled_routes
    finding_authority = LegacyComparisonSnapshot.finding_authority
    finding_route_context = LegacyComparisonSnapshot.finding_route_context
    physical_comparison_reason = LegacyComparisonSnapshot.physical_comparison_reason

    def definition_unavailable_reason(self, card_id):
        card = next(card for card in self.cards if card.card_id == card_id)
        if not card.introduced:
            return "legacy_card_not_introduced"
        return None if card.introduced_before_game else "legacy_introduction_chronology_unknown"

    def applicability_reason(self):
        if self.authority != self.observed_after:
            return "legacy_authority_changed_during_capture"
        return None if self.authority.graph_state == "ready" else "legacy_graph_not_ready"

    def finding_context(self, game):
        scope = self.catalogue_scope.scope
        if (game["game_id"], game["observed_color"], game["moves"], canonical_fen_key(
                chess.Board(game["starting_fen"]).fen(en_passant="legal"))) != (
                scope.game_id, scope.learner_color, scope.moves, scope.starting_position_key):
            raise ValueError("legacy_game_outside_decision_scope")
        if (game.get("played_at") is None or datetime.fromisoformat(game["played_at"].replace("Z", "+00:00")) !=
                datetime.fromisoformat(scope.occurred_at.replace("Z", "+00:00"))):
            raise ValueError("legacy_game_time_outside_decision_scope")
        return LegacyComparisonSnapshot.finding_context(self, game)

    def comparison_scope_reason(self, game, position):
        return None if position["ply"] in self.catalogue_scope.scope.decision_plies else "legacy_decision_outside_scope"

    @model_validator(mode="after")
    def coherent_scoped_authority(self):
        captured_at = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError("legacy_capture_requires_timezone")
        if (self.authority.target_id, self.authority.workspace_id, self.authority.repertoire_id) != (
                self.observed_after.target_id, self.observed_after.workspace_id, self.observed_after.repertoire_id):
            raise ValueError("legacy_capture_target_changed")
        proof = self.catalogue_scope
        digests = proof.source_identity_digests()
        if (proof.authority_id != self.authority.storage_identity()[0]
                or proof.catalogue_generation != self.authority.catalogue_generation
                or proof.integrity_certificate.graph_task_generation != self.authority.graph_generation
                or any(getattr(self.authority, key) != value for key, value in digests.items())):
            raise ValueError("legacy_scoped_authority_digest_mismatch")
        if set(proof.candidate_route_ids) != {route.route_id for route in self.catalogue}:
            raise ValueError("legacy_scoped_candidate_inventory")
        if authority_digest(self.model_dump(mode="json", exclude={"capture_digest"})) != self.capture_digest:
            raise ValueError("legacy_capture_digest_mismatch")
        scope = proof.scope
        for route in self.catalogue:
            board = chess.Board(route.starting_fen)
            if (not board.is_valid() or route.learner_color != scope.learner_color
                    or canonical_fen_key(board.fen(en_passant="legal")) != scope.starting_position_key
                    or not any(ply < len(route.moves) and route.moves[:ply] == scope.moves[:ply]
                               for ply in scope.decision_plies)):
                raise ValueError("legacy_route_outside_decision_scope")
        anchors = {route_identity(route): proof.authority_anchor(route.policy_id) for route in self.catalogue}
        return verify_legacy_physical_routes(self, anchors)
