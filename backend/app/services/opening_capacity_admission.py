"""Bounded capacity evidence adapter; queue writes remain with their existing owners."""

from __future__ import annotations

from dataclasses import asdict
import os

from .opening_capacity_frontier import (
    CapacityCandidate,
    CapacityRoute,
    CapacitySnapshot,
    propose_capacity_expansion,
)

CAPACITY_READ_BATCH_SIZE = 16


def capacity_expansion_enabled() -> bool:
    return os.getenv("OPENING_CAPACITY_FRONTIER_EXPANSION", "0") == "1"


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


def _available_route_card(alias: str) -> str:
    return f"""{alias}.content_type='opening' AND {alias}.archived=0
        AND COALESCE({alias}.pending_validation,0)=0
        AND ({alias}.repertoire_id=step.repertoire_id OR EXISTS(
            SELECT 1 FROM repertoire_cards membership WHERE membership.card_id={alias}.id
              AND membership.repertoire_id=step.repertoire_id))
        AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id={alias}.id)
        AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks blocked
                       WHERE blocked.card_id={alias}.id AND blocked.repertoire_id=step.repertoire_id)"""


# Indexed occurrence-prefix checks on one published line, not card-identity traversal.
# In particular, a reviewed card on a different incoming route cannot fill a gap.
_CAPACITY_ROUTE_FROM = f"""FROM cards parent
    JOIN opening_graph_steps step ON step.parent_card_id=parent.id
    JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
      AND publication.generation=step.generation AND publication.state='ready'
    JOIN repertoires repertoire ON repertoire.id=step.repertoire_id
    JOIN repertoire_lines line ON line.id=step.line_id AND line.repertoire_id=step.repertoire_id
    JOIN opening_graph_steps parent_step ON parent_step.repertoire_id=step.repertoire_id
      AND parent_step.generation=step.generation AND parent_step.line_id=step.line_id
      AND parent_step.decision_index=step.decision_index-1 AND parent_step.card_id=parent.id
    JOIN cards candidate ON candidate.id=step.card_id
    WHERE {_available_route_card("candidate")} AND {_available_route_card("parent")}
      AND parent.state IN ('new','learning','mature')
      AND candidate.state='locked' AND candidate.introduced_at IS NULL
      AND step.segment_kind='decision' AND step.first_decision_index=step.last_decision_index
      AND step.first_decision_index=parent_step.last_decision_index+1
      AND step.trained_color=parent_step.trained_color
      AND step.card_id<>parent.id
      AND (SELECT COUNT(*) FROM opening_graph_steps earlier
           WHERE earlier.repertoire_id=step.repertoire_id AND earlier.generation=step.generation
             AND earlier.line_id=step.line_id AND earlier.decision_index<parent_step.decision_index
             AND earlier.decision_index>=0)=parent_step.decision_index
      AND EXISTS(SELECT 1 FROM opening_graph_steps root WHERE root.repertoire_id=step.repertoire_id
          AND root.generation=step.generation AND root.line_id=step.line_id
          AND root.decision_index=0 AND root.segment_kind='prefix'
          AND root.parent_card_id IS NULL AND root.first_decision_index=0)
      AND NOT EXISTS(
          SELECT 1 FROM opening_graph_steps earlier LEFT JOIN cards prerequisite ON prerequisite.id=earlier.card_id
          WHERE earlier.repertoire_id=step.repertoire_id AND earlier.generation=step.generation
            AND earlier.line_id=step.line_id AND earlier.decision_index<parent_step.decision_index
            AND (prerequisite.id IS NULL OR NOT ({_available_route_card("prerequisite")})
              OR NOT EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=earlier.card_id
                            AND review.source_kind='study' AND review.invalidated_at IS NULL)))
      AND NOT EXISTS(
          SELECT 1 FROM opening_graph_steps later LEFT JOIN opening_graph_steps earlier
            ON earlier.repertoire_id=later.repertoire_id AND earlier.generation=later.generation
            AND earlier.line_id=later.line_id AND earlier.decision_index=later.decision_index-1
          WHERE later.repertoire_id=step.repertoire_id AND later.generation=step.generation
            AND later.line_id=step.line_id AND later.decision_index BETWEEN 1 AND parent_step.decision_index
            AND (earlier.card_id IS NULL OR later.parent_card_id<>earlier.card_id
              OR later.parent_card_id IS NULL OR later.segment_kind<>'decision'
              OR later.first_decision_index<>later.last_decision_index
              OR later.first_decision_index<>earlier.last_decision_index+1
              OR later.trained_color<>earlier.trained_color))
"""


def read_capacity_routes(
    read, repertoire_id: str, normal_parent_ids: tuple[str, ...]
) -> tuple[tuple[str, CapacityRoute], ...]:
    """Page unique direct children, then retain one deterministic route per card.

    read closes each connection before returning; only frontier metadata escapes.
    """
    parent_ids = set(normal_parent_ids)
    after_parent_id = ""
    while True:
        parent_page = [
            row[0]
            for row in read(
                "SELECT parent.id FROM cards parent WHERE parent.content_type='opening' "
                "AND parent.introduced_at IS NOT NULL AND parent.state IN ('new','learning','mature') "
                "AND parent.archived=0 AND COALESCE(parent.pending_validation,0)=0 "
                "AND (parent.repertoire_id=? OR EXISTS(SELECT 1 FROM repertoire_cards membership "
                "WHERE membership.card_id=parent.id AND membership.repertoire_id=?)) "
                "AND NOT EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=parent.id "
                "AND review.source_kind='study' AND review.invalidated_at IS NULL) "
                "AND parent.id>? ORDER BY parent.id LIMIT ?",
                (
                    repertoire_id,
                    repertoire_id,
                    after_parent_id,
                    CAPACITY_READ_BATCH_SIZE,
                ),
            )
        ]
        if not parent_page:
            break
        parent_ids.update(parent_page)
        after_parent_id = parent_page[-1]
    routes_by_card = {}
    ordered_parent_ids = sorted(parent_ids)
    for offset in range(0, len(ordered_parent_ids), CAPACITY_READ_BATCH_SIZE):
        parent_page = ordered_parent_ids[offset : offset + CAPACITY_READ_BATCH_SIZE]
        parameters = (repertoire_id, *parent_page)
        route_from = (
            _CAPACITY_ROUTE_FROM
            + " AND step.repertoire_id=? AND parent.id IN ("
            + ",".join("?" for _ in parent_page)
            + ")"
        )
        after_card_id = ""
        while True:
            identifiers = [
                row[0]
                for row in read(
                    "SELECT DISTINCT candidate.id "
                    + route_from
                    + " AND candidate.id>? ORDER BY candidate.id LIMIT ?",
                    (*parameters, after_card_id, CAPACITY_READ_BATCH_SIZE),
                )
            ]
            if not identifiers:
                break
            rows = read(
                "SELECT card_id,repertoire_id,generation,line_id,decision_index,parent_card_id,parent_introduced FROM ("
                "SELECT candidate.id card_id,step.repertoire_id,step.generation,step.line_id,step.decision_index,"
                "parent.id parent_card_id,CASE WHEN parent.introduced_at IS NOT NULL THEN 1 ELSE 0 END parent_introduced,"
                "ROW_NUMBER() OVER(PARTITION BY candidate.id ORDER BY CASE WHEN parent.introduced_at IS NOT NULL "
                "THEN 0 ELSE 1 END,step.line_id,step.decision_index,parent.id) route_order "
                + route_from
                + " AND candidate.id IN ("
                + ",".join("?" for _ in identifiers)
                + ")) ranked WHERE route_order=1",
                (*parameters, *identifiers),
            )
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
            after_card_id = identifiers[-1]
    return tuple(sorted(routes_by_card.items()))


def capacity_priority_statement(statement: str) -> str:
    # Reuse every existing priority/due/exclusion join, relaxing only locked exposure.
    existing_state_clause = "(c.state='new' OR (c.state='locked' AND (opportunity.id IS NOT NULL OR active_miss.card_id IS NOT NULL)))"
    if existing_state_clause not in statement:
        raise ValueError(
            "Opening candidate state clause changed; review capacity adapter"
        )
    return statement.replace(existing_state_clause, "c.state='locked'")


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
    }


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
    if database.execute(
        "SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=?",
        (day, planned["card_id"]),
    ).fetchone():
        return False
    priority_current = database.execute(
        "SELECT 1 FROM cards c WHERE c.id=? AND (c.due_date<=? OR EXISTS("
        "SELECT 1 FROM current_gameplay_card_priorities p WHERE p.card_id=c.id AND p.priority_date<=?) "
        "OR EXISTS(SELECT 1 FROM current_repertoire_opportunities opportunity WHERE opportunity.card_id=c.id "
        "AND opportunity.repertoire_id=? AND opportunity.kind='weak_known_decision' "
        "AND opportunity.status='active' AND json_extract(opportunity.evidence_json,'$.analysis_based') IS NULL))",
        (planned["card_id"], day, day, planned["repertoire_id"]),
    ).fetchone()
    if not priority_current:
        return False
    return (
        database.execute(
            "SELECT 1 " + _CAPACITY_ROUTE_FROM + " AND parent.introduced_at IS NOT NULL"
            " AND step.card_id=? AND step.repertoire_id=? AND step.generation=?"
            " AND step.line_id=? AND step.decision_index=? AND parent.id=?",
            (
                planned["card_id"],
                planned["repertoire_id"],
                route["generation"],
                route["line_id"],
                route["decision_index"],
                route["parent_card_id"],
            ),
        ).fetchone()
        is not None
    )
