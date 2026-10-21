"""Existing frozen preference decisions over supplied facts; no I/O or SQL."""
from .interfaces import CardPredicate, ContentFingerprint, FrozenAnnotationFacts


def annotate(candidates, facts: FrozenAnnotationFacts, *, fingerprint: ContentFingerprint):
    run_id, epoch = facts.token
    result = [dict(row) for row in candidates]
    if run_id is None:
        return [{**row, "frozen_token": [None, epoch]} for row in result]
    ranks = {
        card_id: (rank.ordinal, rank.current_definition is not None and
                  fingerprint(rank.current_definition, rank.memberships) == rank.stored_fingerprint)
        for card_id, rank in facts.ranks.items()
    }
    for row in result:
        row["frozen_token"] = [run_id, epoch]
        ordinal, valid = ranks.get(row.get("card_id", row.get("id")), (None, True))
        row["frozen_rank"] = ordinal
        row["frozen_legacy_protected"] = (
            row.get("card_id", row.get("id")) in facts.legacy_card_ids
        )
        row["frozen_valid"] = valid and not row["frozen_legacy_protected"]
        row["frozen_route_valid"] = (
            row.get("card_id", row.get("id")),
            row.get("repertoire_id"),
        ) not in facts.blocked_memberships
    return result


def proposal_valid(planned, token, *, miss_reason: str,
                   has_outstanding_miss: CardPredicate, current_definition_valid: CardPredicate):
    token = list(token)
    if planned.get("frozen_token", [None, 0]) != token:
        return False
    if token[0] is None:
        return True
    if planned.get("reason") == miss_reason:
        return has_outstanding_miss(planned["card_id"])
    return current_definition_valid(planned["card_id"])


def order_queue_slots(annotated, *, miss_reason: str, card_exposed: CardPredicate,
                      preserve_through_entry_id=None, exposed_ids=None):
    """Swap only valid captured unstarted introductions in their existing slots."""
    movable_indices = []
    for index, row in enumerate(annotated):
        if (
            row.get("content_type") != "opening"
            or row.get("admission_kind") != "new"
            or row.get("gameplay_priority_reason") == miss_reason
        ):
            continue
        if not row.get("frozen_valid"):
            continue
        if (
            preserve_through_entry_id is not None
            and row["id"] <= preserve_through_entry_id
        ):
            continue
        exposed = (
            row["card_id"] in exposed_ids
            if exposed_ids is not None
            else card_exposed(row["card_id"])
        )
        if exposed:
            continue
        movable_indices.append(index)
    ranked = sorted(
        (annotated[index] for index in movable_indices),
        key=lambda row: (
            row["frozen_rank"] if row["frozen_rank"] is not None else float("inf"),
            row.get("position", row["id"]),
            row["id"],
        ),
    )
    for index, row in zip(movable_indices, ranked):
        annotated[index] = row
    return annotated
