"""Small extension of the existing authored membership snapshot, not a new graph."""
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator
from tempo_game_review_contracts import RepertoireSnapshot as MembershipSnapshot

from . import Identifier, Revision, UciMove, UtcTimestamp, Value


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

    @model_validator(mode="after")
    def introduction_is_evidence(self):
        if self.introduction != "introduced" and self.introduced_at is not None:
            raise ValueError("unintroduced/unknown decisions cannot assert an introduction time")
        return self


class RouteBoundary(Value):
    route_id: Identifier
    terminal_ply: int = Field(ge=0, le=1000)
    deliberate: bool


class RepertoireSnapshot(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    membership: MembershipSnapshot
    variant: Identifier
    learner_color: Literal["white", "black"]
    boundaries: tuple[RouteBoundary, ...]
    decision_presentations: tuple[DecisionPresentation, ...]

    @field_validator("membership")
    @classmethod
    def independent_existing_value(cls, value):
        return value.model_copy(deep=True)

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
        return self


class RepertoireSnapshotPort(Protocol):
    def snapshot(self, repertoire_id: str) -> RepertoireSnapshot: ...
