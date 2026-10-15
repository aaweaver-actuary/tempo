"""Explicit local snapshot adapter; this is not an extension of shared wire v1."""
from typing import Literal

import chess
from pydantic import BaseModel, ConfigDict, Field, model_validator
from tempo_chess_core import canonical_fen_key
from tempo_content_publication_contracts.v1 import Publication, ActivationAcknowledgement


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Binding(StrictModel):
    ply: int = Field(ge=0, le=1000)
    card_id: str = Field(min_length=1)
    reentry: bool = False


class Route(StrictModel):
    repertoire_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    learner_color: Literal["white", "black"]
    starting_fen: str
    moves: list[str] = Field(max_length=1000)
    bindings: list[Binding] = Field(max_length=1000)


class ComparisonSnapshot(StrictModel):
    schema_version: Literal[1]
    complete: bool = False
    snapshot_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    publication: Publication
    activation: ActivationAcknowledgement | None
    routes: list[Route] = Field(max_length=10000)

    @model_validator(mode="after")
    def verified_routes(self):
        publication = self.publication
        if self.activation and (
            self.activation.status != "activated" or
            (self.activation.workspace_id, self.activation.publication_id, self.activation.publication_generation)
            != (publication.workspace_id, publication.publication_id, publication.publication_generation)
        ):
            raise ValueError("snapshot_activation_does_not_match_publication")
        definitions = {item.card_id: item for item in publication.definitions}
        seen_routes = set()
        for route in self.routes:
            route_key = (route.repertoire_id, route.route_id)
            if route_key in seen_routes:
                raise ValueError("duplicate_route_identity")
            seen_routes.add(route_key)
            if len({item.ply for item in route.bindings}) != len(route.bindings):
                raise ValueError("ambiguous_route_binding")
            board = chess.Board(route.starting_fen)
            if not board.is_valid():
                raise ValueError("invalid_route_board")
            bindings = {item.ply: item for item in route.bindings}
            for ply in range(len(route.moves) + 1):
                if ply in bindings:
                    definition = definitions.get(bindings[ply].card_id)
                    if definition is None or canonical_fen_key(board.fen()) != canonical_fen_key(chess.Board(definition.starting_fen).fen()):
                        raise ValueError("binding_missing_definition_or_wrong_position")
                    if board.turn != (route.learner_color == "white"):
                        raise ValueError("binding_is_opponent_move")
                    spec = definition.specification
                    if spec.type == "move_line":
                        if (spec.grading_policy == "reference" and ply<len(route.moves)
                                and route.moves[ply] not in {line[0] for line in spec.accepted_lines}):
                            raise ValueError("route_conflicts_with_accepted_policy")
                        for line in spec.accepted_lines:
                            alternative_board = board.copy()
                            for uci in line:
                                alternative_board.push_uci(uci)
                if ply < len(route.moves):
                    board.push_uci(route.moves[ply])
            if any(binding.ply > len(route.moves) for binding in route.bindings):
                raise ValueError("binding_outside_route")
        return self

    def compiled_routes(self):
        compiled = []
        for route in self.routes:
            board = chess.Board(route.starting_fen)
            keys = [canonical_fen_key(board.fen(en_passant="legal"))]
            for uci in route.moves:
                board.push_uci(uci)
                keys.append(canonical_fen_key(board.fen(en_passant="legal")))
            compiled.append((route, keys, {item.ply: item for item in route.bindings}))
        return compiled
