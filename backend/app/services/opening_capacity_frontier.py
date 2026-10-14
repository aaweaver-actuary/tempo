"""Pure capacity proposals and the existing opening-priority selection kernel.

No database, environment, clock, chess, or scheduling dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json


def priority_frontier_depth(priority_row) -> int:
    if not priority_row or not priority_row["frontier_decisions_json"]:
        return 0
    try:
        values = json.loads(priority_row["frontier_decisions_json"])
        return max((int(item[2].get("ply", 0)) for item in values), default=0)
    except (TypeError, ValueError, json.JSONDecodeError, IndexError):
        return 0


def plan_prioritized_opening_admissions(
    candidates,
    introduced_by_repertoire,
    day: str,
    limit: int | dict[str, int],
    *,
    miss_reason: str,
) -> list[tuple[str, dict]]:
    """Preserve gameplay, breadth, and global-card ordering outside a transaction."""

    candidates = [dict(row) for row in candidates]
    if any(row.get("frozen_token", [None])[0] for row in candidates):
        selected, consumed, planned = set(), dict(introduced_by_repertoire), []
        obligations = sorted((row for row in candidates if row["gameplay_priority_reason"] == miss_reason),
                             key=lambda row: (row["id"], row["repertoire_id"]))
        frozen = sorted((row for row in candidates if row.get("frozen_rank") is not None and row.get("frozen_valid") and row.get("frozen_route_valid", True)),
                        key=lambda row: (row["frozen_rank"], row["id"], row["repertoire_id"]))
        for row in obligations + frozen:
            repertoire_id = row["repertoire_id"]
            allowance = limit.get(repertoire_id, 0) if isinstance(limit, dict) else limit
            mandatory = row["gameplay_priority_reason"] == miss_reason
            if row["id"] in selected or (not mandatory and consumed.get(repertoire_id, 0) >= allowance):
                continue
            planned.append((repertoire_id, row))
            selected.add(row["id"])
            consumed[repertoire_id] = consumed.get(repertoire_id, 0) + 1
        fallback = [row for row in candidates if row["id"] not in selected and row.get("frozen_rank") is None]
        baseline = [{**row, "frozen_token": [None, 0]} for row in fallback]
        original = {(row["repertoire_id"], row["id"]): row for row in fallback}
        for repertoire_id, row in plan_prioritized_opening_admissions(baseline, consumed, day, limit, miss_reason=miss_reason):
            planned.append((repertoire_id, original[repertoire_id, row["id"]]))
        return planned

    by_repertoire: dict[str, list] = {}
    for row in candidates:
        by_repertoire.setdefault(row["repertoire_id"], []).append(row)
    globally_selected_ids: set[str] = set()
    planned: list[tuple[str, dict]] = []
    for repertoire_id, rows in by_repertoire.items():
        daily_limit = limit.get(repertoire_id, 0) if isinstance(limit, dict) else limit
        remaining = max(0, daily_limit - introduced_by_repertoire.get(repertoire_id, 0))
        selected_ids: set[str] = set()
        breadth_line_ids: set[str] = set()
        for obligation in sorted(
            (row for row in rows if row["gameplay_priority_reason"] == miss_reason),
            key=lambda row: row["id"],
        ):
            if obligation["id"] in globally_selected_ids:
                continue
            planned.append((repertoire_id, obligation))
            selected_ids.add(obligation["id"])
            globally_selected_ids.add(obligation["id"])
            remaining = max(0, remaining - 1)
        while remaining:
            available = [
                row
                for row in rows
                if row["id"] not in selected_ids
                and row["id"] not in globally_selected_ids
            ]
            if not available:
                break
            gameplay = [row for row in available if row["gameplay_priority_reason"]]
            if gameplay:
                choice = min(
                    gameplay, key=lambda row: (row["priority_date"] or day, row["id"])
                )
            else:

                def line_ids(row) -> set[str]:
                    try:
                        return set(json.loads(row["completed_line_ids_json"] or "[]"))
                    except json.JSONDecodeError:
                        return set()

                breadth_candidates = [
                    row
                    for row in available
                    if not line_ids(row) or not line_ids(row).issubset(breadth_line_ids)
                ]
                if not breadth_candidates:
                    breadth_line_ids.clear()
                    breadth_candidates = available
                choice = min(
                    breadth_candidates,
                    key=lambda row: (
                        -float(row["priority_score"] or 0),
                        -priority_frontier_depth(row),
                        len(json.loads(row["moves_json"])),
                        row["id"],
                    ),
                )
                try:
                    breadth_line_ids.update(
                        json.loads(choice["completed_line_ids_json"] or "[]")
                    )
                except json.JSONDecodeError:
                    pass
            planned.append((repertoire_id, choice))
            selected_ids.add(choice["id"])
            globally_selected_ids.add(choice["id"])
            remaining -= 1
    return planned


@dataclass(frozen=True)
class CapacityRoute:
    repertoire_id: str
    generation: int
    line_id: str
    decision_index: int
    parent_card_id: str
    parent_introduced: bool
    earlier_unreviewed_card_ids: tuple[str, ...] = ()
    current_ready_publication: bool = True
    structurally_valid: bool = True
    single_decision: bool = True


@dataclass(frozen=True)
class CapacityCandidate:
    card_id: str
    repertoire_id: str
    routes: tuple[CapacityRoute, ...]
    moves_json: str = "[]"
    gameplay_priority_reason: str | None = None
    priority_date: str | None = None
    priority_score: float | None = None
    completed_line_ids_json: str | None = None
    frontier_decisions_json: str | None = None
    admissible: bool = True
    frozen_rank: int | None = None
    frozen_valid: bool = True
    frozen_token: tuple = (None, 0)

    def priority_row(self) -> dict:
        return {
            "id": self.card_id,
            "repertoire_id": self.repertoire_id,
            "moves_json": self.moves_json,
            "gameplay_priority_reason": self.gameplay_priority_reason,
            "priority_date": self.priority_date,
            "priority_score": self.priority_score,
            "completed_line_ids_json": self.completed_line_ids_json,
            "frontier_decisions_json": self.frontier_decisions_json,
            "frozen_rank": self.frozen_rank, "frozen_valid": self.frozen_valid,
            "frozen_token": list(self.frozen_token),
        }


@dataclass(frozen=True)
class CapacitySnapshot:
    enabled: bool
    day: str
    normal_selections: tuple[tuple[str, str], ...]
    remaining_allowances: tuple[tuple[str, int], ...]
    remaining_capacity: int
    candidates: tuple[CapacityCandidate, ...] = ()


@dataclass(frozen=True)
class CapacityProposal:
    candidate: CapacityCandidate
    route: CapacityRoute


def propose_capacity_expansion(
    snapshot: CapacitySnapshot, *, miss_reason: str
) -> tuple[CapacityProposal, ...]:
    """One extra decision on one coherent route; admissions never become exposure."""
    if not snapshot.enabled or snapshot.remaining_capacity <= 0:
        return ()
    remaining_allowances = dict(snapshot.remaining_allowances)
    selected_card_ids = {card_id for _, card_id in snapshot.normal_selections}
    normal_parent_ids = frozenset(selected_card_ids)
    qualified_by_membership: dict[tuple[str, str], CapacityProposal] = {}
    for candidate in snapshot.candidates:
        candidate = replace(
            candidate, routes=tuple(sorted(set(candidate.routes), key=repr))
        )
        if (
            not candidate.admissible
            or candidate.card_id in selected_card_ids
            or remaining_allowances.get(candidate.repertoire_id, 0) <= 0
        ):
            continue
        qualifying_routes = [
            route
            for route in candidate.routes
            if route.repertoire_id == candidate.repertoire_id
            and route.current_ready_publication
            and route.structurally_valid
            and route.single_decision
            and route.decision_index > 0
            and not route.earlier_unreviewed_card_ids
            and route.parent_card_id != candidate.card_id
            and (route.parent_introduced or route.parent_card_id in normal_parent_ids)
        ]
        if not qualifying_routes:
            continue
        route = min(
            qualifying_routes,
            key=lambda route: (
                not route.parent_introduced,
                route.generation,
                route.line_id,
                route.decision_index,
                route.parent_card_id,
            ),
        )
        membership = candidate.repertoire_id, candidate.card_id
        proposal = CapacityProposal(candidate, route)
        previous = qualified_by_membership.get(membership)
        if previous and previous != proposal:
            raise ValueError(f"Conflicting capacity candidate {membership}")
        qualified_by_membership[membership] = proposal
    proposals: list[CapacityProposal] = []
    frozen_active = any(candidate.frozen_token[0] is not None for candidate in snapshot.candidates)
    for previously_introduced in ((None,) if frozen_active else (True, False)):
        tier = {
            membership: proposal
            for membership, proposal in qualified_by_membership.items()
            if (previously_introduced is None or proposal.route.parent_introduced == previously_introduced)
            and proposal.candidate.card_id not in selected_card_ids
        }
        # Stable repertoire order for the new policy only. Normal selection is untouched.
        priority_rows = [
            tier[membership].candidate.priority_row() for membership in sorted(tier)
        ]
        for repertoire_id, priority_row in plan_prioritized_opening_admissions(
            priority_rows,
            {},
            snapshot.day,
            remaining_allowances,
            miss_reason=miss_reason,
        ):
            if len(proposals) >= snapshot.remaining_capacity:
                return tuple(proposals)
            if (
                remaining_allowances.get(repertoire_id, 0) <= 0
                or priority_row["id"] in selected_card_ids
            ):
                continue
            proposal = tier[repertoire_id, priority_row["id"]]
            proposals.append(proposal)
            selected_card_ids.add(priority_row["id"])
            remaining_allowances[repertoire_id] -= 1
    return tuple(proposals)
