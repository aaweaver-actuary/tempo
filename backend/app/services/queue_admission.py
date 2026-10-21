"""Queue introduction decisions over semantic, caller-owned capabilities.

The store borrows the existing writer transaction. It owns queries, bindings and
witness validation; this module never opens a connection or controls a lock.
Opening preference and capacity policy remain the existing pure planner.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import NotRequired, Protocol, TypedDict

from app.study.openings.capacity_frontier import CapacityCandidate, CapacityRoute, CapacitySnapshot, propose_capacity_expansion, plan_prioritized_opening_admissions


class OpeningCapacityRoutePayload(TypedDict):
    repertoire_id: str
    generation: int
    line_id: str
    decision_index: int
    parent_card_id: str
    parent_introduced: bool
    earlier_unreviewed_card_ids: tuple[str, ...]
    current_ready_publication: bool
    structurally_valid: bool
    single_decision: bool


class AdmittedOpeningProposal(TypedDict):
    """Existing durable shape; changing owners does not change checkpoints."""
    repertoire_id: str
    card_id: str
    reason: str | None
    capacity_route: NotRequired[OpeningCapacityRoutePayload]


class OpeningCandidateFacts(TypedDict):
    """Legacy planner facts; JSON fields keep the existing planner contract."""
    id: str
    repertoire_id: str
    moves_json: str
    gameplay_priority_reason: str | None
    priority_date: str | None
    priority_score: float | None
    completed_line_ids_json: str | None
    frontier_decisions_json: str | None
    due_date: NotRequired[str]
    frozen_token: NotRequired[list]
    frozen_rank: NotRequired[int | None]
    frozen_valid: NotRequired[bool]
    frozen_route_valid: NotRequired[bool]
    capacity_route: NotRequired[OpeningCapacityRoutePayload]


class UnseenOpeningFacts(TypedDict):
    id: int
    card_id: str
    position: int
    repertoire_id: str


@dataclass(frozen=True)
class OpeningCandidatePage:
    candidates: tuple[OpeningCandidateFacts, ...]
    after_card_id: str | None


@dataclass(frozen=True)
class IntroductionCountPage:
    counts: tuple[tuple[str, int], ...]
    after_repertoire_id: str | None


class QueueAdmissionStore(Protocol):
    """Bounded reads and effects on one borrowed writer transaction.

    Read methods return materialized facts, never cursors or query fragments.
    Mutations and witness checks use the same transaction as their caller. The
    caller retains card/day locks, commit, rollback, and durable slice authority.
    """
    miss_reason: str

    def frozen_active(self) -> bool: ...
    def unseen_openings(self, day: str) -> list[UnseenOpeningFacts]: ...
    def introduction_count_page(self, day: str, after_repertoire_id: str | None, *, reviewed_only: bool = False) -> IntroductionCountPage: ...
    def has_outstanding_miss(self, card_id: str) -> bool: ...
    def retain_unseen(self, day: str, card_id: str) -> None: ...
    def remove_unseen(self, entry_id: int, card_id: str) -> None: ...
    def opening_candidate_page(self, day: str, after_card_id: str) -> OpeningCandidatePage: ...
    def annotate_openings(self, candidates: list[OpeningCandidateFacts]) -> list[OpeningCandidateFacts]: ...
    def opening_limits(self, default: int | dict[str, int]) -> dict[str, int]: ...
    def capacity_enabled(self) -> bool: ...
    def study_only(self) -> bool: ...
    def capacity_routes(self, repertoire_id: str, parent_ids: tuple[str, ...]) -> tuple[tuple[str, CapacityRoute], ...]: ...
    def capacity_candidates(self, day: str, card_ids: tuple[str, ...]) -> list[OpeningCandidateFacts]: ...
    def frozen_proposal_valid(self, proposal: AdmittedOpeningProposal) -> bool: ...
    def capacity_route_valid(self, proposal: AdmittedOpeningProposal, day: str) -> bool: ...
    def introduced_count(self, day: str, repertoire_id: str) -> int: ...
    def current_limit(self, repertoire_id: str) -> int | None: ...
    def introduce_opening(self, day: str, proposal: AdmittedOpeningProposal, position: int) -> None: ...


def read_opening_candidates(store: QueueAdmissionStore, day: str) -> list[OpeningCandidateFacts]:
    result, after = [], ''
    while True:
        page = store.opening_candidate_page(day, after)
        result.extend(page.candidates)
        if page.after_card_id is None:
            return result
        after = page.after_card_id


def read_introduction_counts(store: QueueAdmissionStore, day: str, *, reviewed_only=False) -> dict[str, int]:
    result, after = {}, None
    while True:
        page = store.introduction_count_page(day, after, reviewed_only=reviewed_only)
        result.update(page.counts)
        if page.after_repertoire_id is None:
            return result
        after = page.after_repertoire_id


def reconcile_unseen_queue(store: QueueAdmissionStore, day: str, limit: int, repertoire_limits=None) -> None:
    if store.frozen_active():
        return
    rows = store.unseen_openings(day)
    introduced = read_introduction_counts(store, day, reviewed_only=True)
    for row in rows:
        repertoire_id = row['repertoire_id']
        consumed = introduced.get(repertoire_id, 0)
        allowance = repertoire_limits.get(repertoire_id, limit) if repertoire_limits is not None else limit
        if consumed < allowance or store.has_outstanding_miss(row['card_id']):
            store.retain_unseen(day, row['card_id'])
            introduced[repertoire_id] = consumed + 1
        else:
            store.remove_unseen(row['id'], row['card_id'])


def plan_opening_admissions(store: QueueAdmissionStore, day: str, limit: int | dict[str, int]):
    candidates = read_opening_candidates(store, day)
    counts = read_introduction_counts(store, day)
    # One frozen preference snapshot for the complete normal candidate set,
    # matching the old reader even though candidate transfer is now paged.
    candidates = store.annotate_openings(candidates)
    normal_plan = plan_prioritized_opening_admissions(candidates, counts, day, limit, miss_reason=store.miss_reason)
    if not store.capacity_enabled() or store.study_only():
        return normal_plan
    limits = store.opening_limits(limit)
    frozen_active = store.frozen_active()
    remaining = remaining_opening_allowances([] if frozen_active else normal_plan, counts, limits)
    if not any(remaining.values()):
        return normal_plan
    parents = tuple(row['id'] for row in candidates) if frozen_active else tuple(row['id'] for _, row in normal_plan)
    routes = tuple(route for repertoire_id in sorted(remaining) if remaining[repertoire_id] > 0
                   for route in store.capacity_routes(repertoire_id, parents))
    capacity_rows = store.capacity_candidates(day, tuple(sorted({card_id for card_id, _ in routes})))
    if frozen_active:
        return plan_frozen_with_capacity(
            day, candidates, counts, limits, routes, store.annotate_openings(capacity_rows), miss_reason=store.miss_reason,
        )
    return list(normal_plan) + [(proposal.candidate.repertoire_id, {
        **proposal.candidate.priority_row(), **capacity_proposal_payload(proposal),
    }) for proposal in propose_capacity_expansion_from_priority_rows(
        day, normal_plan, remaining, routes, capacity_rows, miss_reason=store.miss_reason,
    )]


def admit_prioritized_opening_cards(store: QueueAdmissionStore, day: str,
                                   limit: int | dict[str, int], maximum: int) -> int:
    next_position = maximum
    for repertoire_id, choice in plan_opening_admissions(store, day, limit):
        proposal = {**choice, 'card_id': choice['id'], 'repertoire_id': repertoire_id,
                    'reason': choice['gameplay_priority_reason']}
        if not store.frozen_proposal_valid(proposal):
            continue
        if 'capacity_route' in choice:
            if not store.capacity_route_valid(proposal, day):
                continue
            consumed = store.introduced_count(day, repertoire_id)
            current_limit = store.current_limit(repertoire_id)
            requested_limit = limit.get(repertoire_id, 0) if isinstance(limit, dict) else limit
            if current_limit is None or consumed >= min(current_limit, requested_limit):
                continue
        next_position += 1
        store.introduce_opening(day, proposal, next_position)
    return next_position


def remaining_opening_allowances(
    normal_plan, introduced_counts, limits
) -> dict[str, int]:
    remaining = {
        repertoire_id: max(0, allowance - introduced_counts.get(repertoire_id, 0))
        for repertoire_id, allowance in limits.items()
    }
    for repertoire_id, _ in normal_plan:
        remaining[repertoire_id] = max(0, remaining.get(repertoire_id, 0) - 1)
    return remaining


def propose_capacity_expansion_from_priority_rows(
    day, normal_plan, remaining, routes, priority_rows, *, miss_reason
):
    routes_by_membership = {
        (route.repertoire_id, card_id): route for card_id, route in routes
    }
    candidates = tuple(
        CapacityCandidate(
            row["id"],
            row["repertoire_id"],
            (routes_by_membership[row["repertoire_id"], row["id"]],),
            frozen_rank=dict(row).get("frozen_rank"),
            frozen_valid=dict(row).get("frozen_valid", True),
            frozen_token=tuple(dict(row).get("frozen_token", (None, 0))),
            **{
                field: row[field]
                for field in (
                    "moves_json",
                    "gameplay_priority_reason",
                    "priority_date",
                    "priority_score",
                    "completed_line_ids_json",
                    "frontier_decisions_json",
                )
            },
        )
        for row in priority_rows
        if (row["repertoire_id"], row["id"]) in routes_by_membership
    )
    return propose_capacity_expansion(
        CapacitySnapshot(
            True,
            day,
            tuple((repertoire_id, row["id"]) for repertoire_id, row in normal_plan),
            tuple(sorted(remaining.items())),
            sum(remaining.values()),
            candidates,
        ),
        miss_reason=miss_reason,
    )


def capacity_proposal_payload(proposal) -> dict:
    return {
        "repertoire_id": proposal.candidate.repertoire_id,
        "card_id": proposal.candidate.card_id,
        "reason": proposal.candidate.gameplay_priority_reason,
        "capacity_route": asdict(proposal.route),
        **({"frozen_token": list(proposal.candidate.frozen_token)} if proposal.candidate.frozen_token != (None, 0) else {}),
    }


def plan_frozen_with_capacity(day, candidates, counts, limits, routes, capacity_rows, *, miss_reason):
    """Preserve existing bounded capacity eligibility; prefer ranks after each choice."""
    selected, consumed = [], dict(counts)
    chosen_ids = set()
    while True:
        remaining = remaining_opening_allowances([], consumed, limits)
        normal_parents = [(repertoire_id, row) for repertoire_id, row in selected if 'capacity_route' not in row]
        proposals = propose_capacity_expansion_from_priority_rows(day, normal_parents, remaining, routes, capacity_rows, miss_reason=miss_reason)
        pool = [row for row in candidates if row['id'] not in chosen_ids]
        for proposal in proposals:
            payload = capacity_proposal_payload(proposal)
            pool.append({**proposal.candidate.priority_row(), **payload})
        choices = plan_prioritized_opening_admissions(pool, consumed, day, limits, miss_reason=miss_reason)
        if not choices:
            return selected
        repertoire_id, row = choices[0]
        selected.append((repertoire_id, row))
        chosen_ids.add(row['id'])
        consumed[repertoire_id] = consumed.get(repertoire_id, 0) + 1
        candidates = [row for row in candidates if row['id'] not in chosen_ids]
        capacity_rows = [row for row in capacity_rows if row['id'] not in chosen_ids]
