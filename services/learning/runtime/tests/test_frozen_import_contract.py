from tempo_learning_runtime.study.frozen_order.interfaces import (
    FrozenAnnotationFacts,
    FrozenRankFacts,
)
from tempo_learning_runtime.study.frozen_order.policy import (
    annotate,
    order_queue_slots,
    proposal_valid,
)


TOKEN = ("run-a", 4)
MISS_REASON = "miss"


def _matching_fingerprint(_definition, _memberships):
    return "current"


def _rank(ordinal, *, stored_fingerprint="current"):
    return FrozenRankFacts(
        ordinal=ordinal,
        stored_fingerprint=stored_fingerprint,
        current_definition={"revision": 1},
        memberships=("repertoire-a",),
    )


def _row(entry_id, card_id, *, repertoire_id="repertoire-a", position=1):
    return {
        "id": entry_id,
        "card_id": card_id,
        "repertoire_id": repertoire_id,
        "position": position,
        "content_type": "opening",
        "admission_kind": "new",
        "gameplay_priority_reason": "fresh",
    }


def _annotate(
    rows,
    *,
    ranks=None,
    blocked_memberships=frozenset(),
    fingerprint=_matching_fingerprint,
):
    facts = FrozenAnnotationFacts(
        token=TOKEN,
        ranks={} if ranks is None else ranks,
        legacy_card_ids=frozenset(),
        blocked_memberships=blocked_memberships,
    )
    return annotate(rows, facts, fingerprint=fingerprint)


def _never_exposed(_card_id):
    return False


def _order(rows, *, card_exposed=_never_exposed, **kwargs):
    return order_queue_slots(
        rows,
        miss_reason=MISS_REASON,
        card_exposed=card_exposed,
        **kwargs,
    )


def test_absent_rank_remains_eligible_when_other_facts_are_valid():
    [planned] = _annotate([_row(1, "fresh-card")])

    assert planned["frozen_rank"] is None
    assert planned["frozen_valid"] is True
    assert proposal_valid(
        planned,
        TOKEN,
        miss_reason=MISS_REASON,
        has_outstanding_miss=lambda _card_id: False,
        current_definition_valid=lambda card_id: card_id == "fresh-card",
    )


def test_stale_fingerprint_cannot_control_introduction_order():
    rows = [_row(1, "current-card"), _row(2, "stale-card")]
    annotated = _annotate(
        rows,
        ranks={
            "current-card": _rank(2),
            "stale-card": _rank(1, stored_fingerprint="old"),
        },
    )

    assert annotated[0]["frozen_valid"] is True
    assert annotated[1]["frozen_valid"] is False
    assert [row["id"] for row in _order(annotated)] == [1, 2]


def test_blocked_membership_stays_route_invalid_even_with_a_valid_rank():
    [planned] = _annotate(
        [_row(1, "blocked-card")],
        ranks={"blocked-card": _rank(1)},
        blocked_memberships=frozenset({("blocked-card", "repertoire-a")}),
    )

    assert planned["frozen_valid"] is True
    assert planned["frozen_route_valid"] is False


def test_valid_captured_rank_keeps_its_preference():
    annotated = _annotate(
        [_row(1, "later-card"), _row(2, "earlier-card")],
        ranks={"later-card": _rank(2), "earlier-card": _rank(1)},
    )

    assert [row["card_id"] for row in _order(annotated)] == [
        "earlier-card",
        "later-card",
    ]


def test_unranked_cards_use_position_then_id_deterministically():
    rows = [
        _row(3, "same-position-later-id", position=2),
        _row(2, "earlier-position", position=1),
        _row(1, "same-position-earlier-id", position=2),
    ]

    first = _order(_annotate(rows))
    second = _order(_annotate(list(reversed(rows))))

    expected = [2, 1, 3]
    assert [row["id"] for row in first] == expected
    assert [row["id"] for row in second] == expected


def test_issued_and_exposed_slots_never_move():
    rows = [
        _row(1, "issued-card"),
        _row(2, "movable-card-later"),
        _row(3, "exposed-card"),
        _row(4, "movable-card-earlier"),
    ]
    annotated = _annotate(
        rows,
        ranks={
            "issued-card": _rank(4),
            "movable-card-later": _rank(3),
            "exposed-card": _rank(1),
            "movable-card-earlier": _rank(2),
        },
    )

    ordered = _order(
        annotated,
        preserve_through_entry_id=1,
        card_exposed=lambda card_id: card_id == "exposed-card",
    )

    assert [row["id"] for row in ordered] == [1, 4, 3, 2]
