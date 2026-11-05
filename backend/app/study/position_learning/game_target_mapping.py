"""Map a saved Games first departure to its exact position-owned Study target.

This module consumes immutable Study command/qualification values and delegates
currentness to the Repertoire owner. It never rewrites the original game key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.contracts.product_v02.learning import (
    GameEncounter,
    FirstDepartureWitness,
    GameEffectKey,
)
from app.contracts.product_v02.games import FirstDepartureSelection
from tempo_evidence_math.position_learning import Hold, PositionDefinition, PositionKey


class CurrentPositionDefinitionPort(Protocol):
    """Source-owner capability that returns only a currently approved definition.

    Implementations must independently establish current source/publication
    authority. The mapper does not treat Games labels or a caller boolean as
    proof of currentness.
    """

    def current_definition(
        self, *, repertoire_id: str, position: str
    ) -> PositionDefinition | Hold: ...


@dataclass(frozen=True)
class PositionGameTarget:
    """Position identity plus the untouched original effect provenance."""

    original_effect_key: GameEffectKey
    definition: PositionDefinition
    source_digest: str


def map_first_departure(
    encounter: GameEncounter,
    qualification: FirstDepartureWitness | FirstDepartureSelection | None,
    *,
    current_source: CurrentPositionDefinitionPort | None = None,
) -> PositionGameTarget | Hold:
    """Resolve one saved own-first miss, or return a typed hold.

    An opponent-first boundary is a gap and does not call the current source.
    The current source port owns stale/current proof; this function only checks
    that its returned definition still matches the exact saved target.
    """
    if qualification is None:
        return Hold("first_departure_qualification_missing")
    selected = (
        qualification.selection
        if isinstance(qualification, FirstDepartureWitness)
        else qualification
    )
    if selected.status == "opponent_gap":
        return Hold("opponent_first_gap_not_a_lapse")
    if selected.status != "own_actionable" or selected.actor != "learner":
        return Hold("first_departure_not_actionable")
    if (
        not isinstance(qualification, FirstDepartureWitness)
        or encounter.observation != "missed"
        or not qualification.matches(encounter)
    ):
        return Hold("saved_first_departure_mismatch")

    decision = selected.decision
    if decision is None or len(selected.expected_uci) != 1:
        return Hold("first_departure_target_unavailable")
    route_occurrences = tuple(
        route
        for route in selected.surviving_routes
        if (route.repertoire_id, route.route_id, route.route_ply)
        == (decision.repertoire_id, decision.route_id, decision.route_ply)
    )
    if len(route_occurrences) != 1:
        return Hold("first_departure_route_ambiguous")
    authored_policy = route_occurrences[0].policy_id

    try:
        expected_key = PositionKey(decision.repertoire_id, selected.position_key)
    except (TypeError, ValueError):
        return Hold("saved_position_invalid")
    if current_source is None:
        return Hold("current_position_source_unavailable")

    current = current_source.current_definition(
        repertoire_id=decision.repertoire_id,
        position=expected_key.position,
    )
    if isinstance(current, Hold):
        return current
    if not isinstance(current, PositionDefinition):
        return Hold("current_position_definition_unavailable")

    if current.key != expected_key:
        return Hold("current_position_changed")
    if current.expected_uci != selected.expected_uci[0]:
        return Hold("current_response_changed")
    if current.policy_version != authored_policy:
        return Hold("current_policy_changed")

    return PositionGameTarget(
        original_effect_key=encounter.key,
        definition=current,
        source_digest=encounter.source.source_digest,
    )
