"""Bounded capacity evidence adapter; queue writes remain with their existing owners."""

from __future__ import annotations

import os

from tempo_legacy_database.adapters.queue_refresh import capacity as capacity_storage
from tempo_legacy_database.adapters.queue_refresh.capacity import _available_route_card, _CAPACITY_ROUTE_FROM, capacity_priority_statement

from app.study.openings.capacity_frontier import CapacityRoute
# Compatibility exports: policy has one pure owner; existing consumers keep
# their imports while the storage adapter retains route queries and validation.
from .queue_admission import (
    remaining_opening_allowances, propose_capacity_expansion_from_priority_rows,
    capacity_proposal_payload, plan_frozen_with_capacity,
)

CAPACITY_READ_BATCH_SIZE = 16


def capacity_expansion_enabled() -> bool:
    return os.getenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "0") == "1"





# Indexed occurrence-prefix checks on one published line, not card-identity traversal.
# In particular, a reviewed card on a different incoming route cannot fill a gap.



def read_capacity_routes(
    read, repertoire_id: str, normal_parent_ids: tuple[str, ...]
) -> tuple[tuple[str, CapacityRoute], ...]:
    """Page unique direct children, then retain one deterministic route per card.

    read closes each connection before returning; only frontier metadata escapes.
    """
    return capacity_routes_from_rows(capacity_storage.read_capacity_route_rows(
        read, repertoire_id, normal_parent_ids, CAPACITY_READ_BATCH_SIZE,
    ))


def capacity_routes_from_rows(rows) -> tuple[tuple[str, CapacityRoute], ...]:
    """Retain the existing deterministic route selection over lazy row facts."""
    routes_by_card = {}
    for row in rows:
        route = CapacityRoute(
            row[1], int(row[2]), row[3], int(row[4]), row[5], bool(row[6])
        )
        previous = routes_by_card.get(row[0])
        if previous is None or (
            not route.parent_introduced,
            route.line_id,
            route.decision_index,
            route.parent_card_id,
        ) < (
            not previous.parent_introduced,
            previous.line_id,
            previous.decision_index,
            previous.parent_card_id,
        ):
            routes_by_card[row[0]] = route

    return tuple(sorted(routes_by_card.items()))





def capacity_route_still_valid(database, planned: dict, day: str) -> bool:
    """Revalidate the exact proposed route through the existing admission transaction."""
    if not capacity_expansion_enabled():
        return False
    from .study_mode import study_only_enabled

    if study_only_enabled(database):
        return False
    route = planned["capacity_route"]
    if route["repertoire_id"] != planned["repertoire_id"]:
        return False
    if capacity_storage.queued_capacity_card(database, day, planned["card_id"]):
        return False
    priority_current = capacity_storage.current_capacity_priority(database, planned["card_id"], day, planned["repertoire_id"])
    if not priority_current:
        return False
    return (
        capacity_storage.published_capacity_route(database, planned["card_id"], planned["repertoire_id"], route["generation"], route["line_id"], route["decision_index"], route["parent_card_id"])
        is not None
    )
