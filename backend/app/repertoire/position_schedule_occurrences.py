"""Pure authored-route projection; certification authority stays with the source owner.

The caller must obtain the certificate from the owner of the same snapshot and
fence it again at issuance. Constructing a certificate is not evidence of that
owner read. Physical presentation mappings are not route eligibility proofs.
"""

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType

from tempo_evidence_math.position_learning import (
    DecisionOccurrence,
    Hold,
    PositionDefinition,
    PositionKey,
    RouteOption,
    SourceCertificate,
)

from app.contracts.product_v02.repertoire import RepertoireSnapshot

from .position_index import PositionIndex, build_index


@dataclass(frozen=True)
class PositionSourceScopes:
    """Detached identity and scope of the captured public repertoire source."""

    snapshot_id: str
    source_revision: str
    source_digest: str
    membership_revision: int
    captured_at: str
    scope_id: str
    repertoire_ids: tuple[str, ...]


@dataclass(frozen=True)
class PositionRouteOccurrence:
    """One exact authored learner response occurrence, identified before the move."""

    route_id: str
    route_ply: int
    full_fen: str


@dataclass(frozen=True)
class PhysicalBindingFact:
    """Detached current physical-card reference; availability is never inferred."""

    decision_id: str
    card_id: str
    card_revision: int
    available: bool
    route_id: str | None
    route_ply: int | None


@dataclass(frozen=True)
class PositionBindingFacts:
    """Authored identity with all exact route occurrences and physical references."""

    definition: PositionDefinition
    occurrences: tuple[PositionRouteOccurrence, ...]
    physical_bindings: tuple[PhysicalBindingFact, ...]


@dataclass(frozen=True)
class PositionInventoryFacts:
    """Immutable public facts projected after source and legality gates pass."""

    source_certificate: SourceCertificate
    source_scopes: PositionSourceScopes
    positions: tuple[PositionBindingFacts, ...]
    distinct_position_count: int
    distinct_route_count: int
    route_occurrence_count: int
    physical_reference_count: int


def inventory_from_snapshot(
    snapshot: RepertoireSnapshot,
    *,
    source_certificate: SourceCertificate | None,
) -> PositionInventoryFacts | Hold:
    """Project public owner snapshot facts without exposing the private graph.

    The certificate must still come from the source owner; this pure projection
    does not create source authority or replace the writer's currentness fence.
    """
    try:
        index = build_index(snapshot)
    except (ValueError, TypeError) as exc:
        return Hold("authored_source_invalid", str(exc))
    return inventory_from_validated_index(
        snapshot, index, source_certificate=source_certificate
    )


def inventory_from_validated_index(
    snapshot: RepertoireSnapshot,
    index: PositionIndex,
    *,
    source_certificate: SourceCertificate | None,
) -> PositionInventoryFacts | Hold:
    """Project detached position, route-occurrence and physical-binding facts.

    The validated index stays inside the Repertoire adapter. The returned value
    contains only frozen shared-library/public value types and scalar facts.
    """
    routes = routes_from_validated_index(
        snapshot, index, source_certificate=source_certificate
    )
    if isinstance(routes, Hold):
        return routes
    if (
        source_certificate is None
    ):  # Kept explicit for type narrowing and authority clarity.
        return Hold("authored_source_unavailable_or_not_current")

    grouped = defaultdict(
        lambda: {"definition": None, "occurrences": [], "bindings": {}}
    )
    for (repertoire_id, route_id), occurrences in sorted(index.routes.items()):
        for item in occurrences:
            if not item.learner_turn or not item.next_move:
                continue
            key = PositionKey(repertoire_id, item.key.position)
            definition = PositionDefinition(key, item.next_move, str(item.policy_id))
            row = grouped[key]
            if row["definition"] is not None and row["definition"] != definition:
                return Hold("authored_position_definition_conflict")
            row["definition"] = definition
            row["occurrences"].append(
                PositionRouteOccurrence(route_id, item.ply, item.full_fen)
            )

    # Attach detached references by their declared authored position. The
    # source gate above has already checked selected responses and route-local
    # occurrence claims; retain unavailable and ambiguous mappings as facts.
    for mapping in snapshot.decision_presentations:
        key = PositionKey(mapping.repertoire_id, mapping.position_key)
        row = grouped.get(key)
        if row is None:
            continue
        fact = PhysicalBindingFact(
            mapping.decision_id,
            mapping.presentation_id,
            mapping.presentation_revision,
            mapping.available,
            mapping.route_id,
            mapping.route_ply,
        )
        identity = (
            fact.decision_id,
            fact.card_id,
            fact.card_revision,
            fact.available,
            fact.route_id,
            fact.route_ply,
        )
        row["bindings"][identity] = fact

    positions = tuple(
        PositionBindingFacts(
            row["definition"],
            tuple(
                sorted(
                    row["occurrences"], key=lambda item: (item.route_id, item.route_ply)
                )
            ),
            tuple(
                row["bindings"][key]
                for key in sorted(
                    row["bindings"],
                    key=lambda item: tuple("" if v is None else str(v) for v in item),
                )
            ),
        )
        for _key, row in sorted(grouped.items())
    )
    route_ids = {
        (position.definition.key.repertoire_id, item.route_id)
        for position in positions
        for item in position.occurrences
    }
    physical_count = sum(len(position.physical_bindings) for position in positions)
    membership = snapshot.membership
    return PositionInventoryFacts(
        source_certificate,
        PositionSourceScopes(
            membership.snapshot_id,
            membership.source_revision,
            membership.digest,
            membership.revision,
            membership.captured_at,
            membership.scope_id,
            tuple(membership.scope_repertoire_ids),
        ),
        positions,
        len(positions),
        len(route_ids),
        sum(len(position.occurrences) for position in positions),
        physical_count,
    )


def routes_from_snapshot(
    snapshot: RepertoireSnapshot,
    *,
    source_certificate: SourceCertificate | None,
) -> tuple[RouteOption, ...] | Hold:
    """Project certified snapshot routes without exporting the graph type."""
    try:
        index = build_index(snapshot)
    except (ValueError, TypeError) as exc:
        return Hold("authored_source_invalid", str(exc))
    return routes_from_validated_index(
        snapshot, index, source_certificate=source_certificate
    )


def routes_from_validated_index(
    snapshot: RepertoireSnapshot,
    index: PositionIndex,
    *,
    source_certificate: SourceCertificate | None,
) -> tuple[RouteOption, ...] | Hold:
    """Project bounded authored facts, requiring owner-proven membership and integrity."""
    membership = snapshot.membership
    if (
        source_certificate is None
        or not source_certificate.complete
        or not source_certificate.current_integrity
        or not membership.complete
        or not membership.available
        or not index.available
    ):
        return Hold("authored_source_unavailable_or_not_current")
    if (
        source_certificate.snapshot_id,
        source_certificate.source_revision,
        source_certificate.source_digest,
    ) != (membership.snapshot_id, membership.source_revision, membership.digest):
        return Hold("source_certificate_mismatch")
    try:
        # Do not trust a detached index with the same revision but different content.
        if build_index(snapshot) != index:
            return Hold("source_index_mismatch")
    except (ValueError, TypeError) as exc:
        return Hold("authored_source_invalid", str(exc))
    fatal = sorted(
        {d.code for d in index.diagnostics if d.code != "ambiguous_physical_mapping"}
    )
    if fatal:
        return Hold("authored_source_diagnostics", ",".join(fatal))
    if any(
        route.learner_color != snapshot.learner_color for route in membership.routes
    ):
        return Hold("learner_side_conflict")
    # An explicit selected answer is source content, even if no physical mapping
    # binds to the authored move and PositionIndex would otherwise ignore it.
    nodes_by_position = {
        (key.repertoire_id, key.position): node for key, node in index.nodes.items()
    }
    for mapping in snapshot.decision_presentations:
        node = nodes_by_position.get((mapping.repertoire_id, mapping.position_key))
        if node is None or node.selected_response != mapping.selected_response_uci:
            return Hold("selected_response_conflict")
        if mapping.route_id is not None:
            occurrences = index.routes.get(
                (mapping.repertoire_id, mapping.route_id), ()
            )
            if (
                mapping.route_ply >= len(occurrences)
                or occurrences[mapping.route_ply].key.position != mapping.position_key
                or not occurrences[mapping.route_ply].learner_turn
            ):
                return Hold("selected_response_conflict")
    out = []
    for (repertoire_id, route_id), occurrences in sorted(index.routes.items()):
        if not occurrences or any(not item.route_complete for item in occurrences):
            return Hold("route_incomplete", route_id)
        first = occurrences[0]
        try:
            decisions = tuple(
                DecisionOccurrence(
                    item.ply,
                    PositionDefinition(
                        PositionKey(repertoire_id, item.key.position),
                        item.next_move,
                        str(item.policy_id),
                    ),
                    item.full_fen,
                )
                for item in occurrences
                if item.learner_turn and item.next_move
            )
            if decisions:
                out.append(
                    RouteOption(
                        route_id,
                        first.starting_fen,
                        first.route_moves,
                        first.learner_color,
                        decisions,
                        source_certificate,
                        True,
                    )
                )
        except (ValueError, TypeError) as exc:
            return Hold("published_route_contract_invalid", str(exc))
    return tuple(out)


def index_routes(
    routes: tuple[RouteOption, ...], *, max_routes: int = 10000, max_uses: int = 100000
):
    """Index detached options; repeat occurrences remain in each route's decisions."""
    if len(routes) > max_routes or max_routes < 0:
        return Hold("route_inventory_too_large")
    candidates = defaultdict(list)
    definitions = defaultdict(set)
    uses = 0
    for route in routes:
        for decision in route.decisions:
            uses += 1
            if uses > max_uses:
                return Hold("decision_inventory_too_large")
            candidates[decision.definition.key].append(route)
            definitions[decision.definition.key].add(decision.definition)
    return MappingProxyType(
        {
            key: Hold("own_response_conflict")
            if len(definitions[key]) != 1
            else tuple(sorted(set(values), key=lambda route: route.route_id))
            for key, values in candidates.items()
        }
    )


def options_for_target(index, key: PositionKey):
    if isinstance(index, Hold):
        return index
    return index.get(key, Hold("position_not_in_current_graph"))
