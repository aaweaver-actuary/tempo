"""Small extension of the existing authored membership snapshot, not a new graph."""
import json
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator
from tempo_game_review_contracts import AuthoredRoute, ReentryAuthority, RepertoireSnapshot as MembershipSnapshot

from . import Digest, Identifier, Revision, UciMove, UtcTimestamp, Value


class ImmutableAuthoredRoute(AuthoredRoute):
    """Wire-compatible adapter; donor types/behavior remain unchanged."""
    moves: tuple[str, ...] = Field(max_length=1000)
    reentries: tuple[ReentryAuthority, ...] = Field(default=(), max_length=1000)


class ImmutableMembershipSnapshot(MembershipSnapshot):
    scope_repertoire_ids: tuple[str, ...] = Field(min_length=1, max_length=10000)
    routes: tuple[ImmutableAuthoredRoute, ...] = Field(max_length=10000)


class IntroductionGameScope(Value):
    """The exact captured source to which Study's chronology boolean applies."""
    provider: Literal["lichess", "chesscom"]
    game_id: Identifier
    source_revision: Revision
    source_digest: Digest


class DecisionPresentation(Value):
    decision_id: Identifier
    repertoire_id: Identifier
    position_key: Identifier
    selected_response_uci: UciMove
    presentation_id: Identifier  # Actual existing physical card/current presentation ID.
    presentation_revision: Revision
    repertoire_revision: Revision
    introduction: Literal["introduced", "unintroduced", "unknown"]
    introduced_at: UtcTimestamp | None
    available: bool = False
    introduction_matches_current_content: bool = False
    route_id: Identifier | None = None
    route_ply: int | None = Field(default=None, ge=0, le=1000)
    introduced_before_game: bool | None = None
    introduction_game: IntroductionGameScope | None = None

    @model_validator(mode="after")
    def introduction_is_evidence(self):
        if self.introduction != "introduced" and self.introduced_at is not None:
            raise ValueError("unintroduced/unknown decisions cannot assert an introduction time")
        if (self.route_id is None) != (self.route_ply is None):
            raise ValueError("route-local mapping requires paired route identity and zero-based ply")
        if (self.introduced_before_game is None) != (self.introduction_game is None):
            raise ValueError("Study chronology boolean requires the exact captured game source")
        if self.introduced_before_game is True and self.introduction != "introduced":
            raise ValueError("a known pre-game introduction cannot be unintroduced/unknown")
        return self


class RouteBoundary(Value):
    route_id: Identifier
    terminal_ply: int = Field(ge=0, le=1000)
    deliberate: bool


class RepertoireSnapshot(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    membership: ImmutableMembershipSnapshot
    variant: Identifier
    learner_color: Literal["white", "black"]
    boundaries: tuple[RouteBoundary, ...]
    decision_presentations: tuple[DecisionPresentation, ...]

    @field_validator("membership", mode="before")
    @classmethod
    def independent_existing_value(cls, value):
        payload = value.model_dump(mode="json") if isinstance(value, MembershipSnapshot) else value
        return ImmutableMembershipSnapshot.model_validate_json(json.dumps(payload))

    @model_validator(mode="after")
    def bind_revision_and_routes(self):
        routes = {route.route_id: route for route in self.membership.routes}
        if len(routes) != len(self.membership.routes):
            raise ValueError("route IDs must be unique within this snapshot")
        if {item.route_id for item in self.boundaries} != set(routes) or len(self.boundaries) != len(routes):
            raise ValueError("each route needs its explicit terminal boundary")
        for boundary in self.boundaries:
            if boundary.terminal_ply != len(routes[boundary.route_id].moves):
                raise ValueError("terminal boundary must match the ordered authored route")
        for mapping in self.decision_presentations:
            if mapping.repertoire_revision != self.membership.revision or mapping.repertoire_id not in self.membership.scope_repertoire_ids:
                raise ValueError("mapping conflicts with supplied repertoire revision/scope")
            if mapping.route_id is not None:
                route = routes.get(mapping.route_id)
                if (route is None or route.repertoire_id != mapping.repertoire_id
                        or mapping.route_ply >= len(route.moves)
                        or route.moves[mapping.route_ply] != mapping.selected_response_uci):
                    raise ValueError("route-local mapping must identify its supplied authored response occurrence")
        return self


class RepertoireSnapshotPort(Protocol):
    def snapshot(self, repertoire_id: str) -> RepertoireSnapshot: ...
