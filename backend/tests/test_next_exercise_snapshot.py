from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.product_composition.study import read_queue_snapshot


def test_snapshot_materializes_limited_rows_and_closes_optional_failure():
    active = []
    events = []
    rows = [
        dict(
            queue_entry_id=2,
            id=20,
            revision=1,
            moves_json="[]",
            latest_failed_review_id=1,
            prefix_split_rejected_after_review_id=0,
            content_type="opening",
            start_fen="a b c d e f",
        )
    ]

    @contextmanager
    def read_connection():
        scope = len(events)
        active.append(scope)
        events.append(("open", scope))
        try:
            yield scope
        except Exception:
            events.append(("rollback", scope))
            raise
        finally:
            active.pop()
            events.append(("close", scope))

    def evidence(db, cards, cutoff):
        assert active == [db]
        raise RuntimeError("optional read failed")

    storage = SimpleNamespace(
        evidence_timezone=lambda db: ("UTC",),
        queue_projection=lambda db, day: {"state": "ready"},
        queue_diagnostics=lambda db, day: iter([{"card_id": 20, "message": "hold"}]),
        queue_rows=lambda db, day, limit, miss_reason: iter(rows),
        position_pending_modes=lambda db, cards: {2: True},
        available_count=lambda db, day, miss_reason: 7,
        encounter_badges=evidence,
    )
    snapshot = read_queue_snapshot(
        1,
        read_connection=read_connection,
        current_study_day=lambda db: "2026-11-10",
        queue_projection_storage=storage,
        miss_reason="miss",
    )
    assert not active
    assert events[-2][0] == "rollback" and events[-1][0] == "close"
    assert snapshot.total_count == 7 and [c["id"] for c in snapshot.cards] == [20]
    assert snapshot.cards[0]["position_study_hold"] == "position_not_issued"
    assert snapshot.cards[0]["encounter_evidence_status"] == "unavailable"
    assert "encounter_count_30d" not in snapshot.cards[0]
    rows[0]["id"] = 999
    assert snapshot.cards[0]["id"] == 20
    assert snapshot.diagnostics == [{"card_id": 20, "message": "hold"}]


def test_empty_unlimited_snapshot_skips_optional_and_count_reads():
    @contextmanager
    def read_connection():
        yield object()

    def unexpected(*args, **kwargs):
        pytest.fail("unnecessary read")

    storage = SimpleNamespace(
        evidence_timezone=lambda db: (None,),
        queue_projection=lambda db, day: None,
        queue_diagnostics=lambda db, day: [],
        queue_rows=lambda db, day, limit, miss_reason: [],
        position_pending_modes=lambda db, cards: {},
        available_count=unexpected,
        encounter_badges=unexpected,
    )
    snapshot = read_queue_snapshot(
        None,
        read_connection=read_connection,
        current_study_day=lambda db: "day",
        queue_projection_storage=storage,
        miss_reason="miss",
    )
    assert snapshot.cards == [] and snapshot.total_count == 0


def test_core_read_failure_propagates_and_closes_scope():
    closed = []

    @contextmanager
    def read_connection():
        try:
            yield object()
        finally:
            closed.append(True)

    def fail(db):
        raise ValueError("core failure")

    with pytest.raises(ValueError, match="core failure"):
        read_queue_snapshot(
            None,
            read_connection=read_connection,
            current_study_day=lambda db: "day",
            queue_projection_storage=SimpleNamespace(evidence_timezone=fail),
            miss_reason="miss",
        )
    assert len(closed) == 2


@pytest.mark.parametrize("limit,count", [(None, 2), (2, 9)])
def test_snapshot_preserves_order_and_detaches_available_evidence(limit, count):
    @contextmanager
    def read_connection():
        yield object()

    rows = [
        dict(
            queue_entry_id=i,
            id=i,
            revision=1,
            moves_json="[]",
            latest_failed_review_id=0,
            prefix_split_rejected_after_review_id=0,
            content_type="opening",
            start_fen=f"{i} b c d e f",
        )
        for i in (4, 2)
    ]
    evidence = {"4 b c d": {"encounter_count": 3, "last_seen_at": None}}
    storage = SimpleNamespace(
        evidence_timezone=lambda db: ("UTC",),
        queue_projection=lambda db, day: {"state": "ready"},
        queue_diagnostics=lambda db, day: [],
        queue_rows=lambda db, day, requested, miss_reason: iter(rows),
        position_pending_modes=lambda db, cards: {},
        available_count=lambda db, day, miss_reason: 9,
        encounter_badges=lambda db, cards, cutoff: evidence,
    )
    snapshot = read_queue_snapshot(
        limit,
        read_connection=read_connection,
        current_study_day=lambda db: "day",
        queue_projection_storage=storage,
        miss_reason="miss",
    )
    assert [card["id"] for card in snapshot.cards] == [4, 2]
    assert snapshot.total_count == count
    assert snapshot.cards[0]["encounter_count_30d"] == 3
    assert snapshot.cards[0]["encounter_badges"] == ["Frequent"]
    assert snapshot.cards[1]["encounter_count_30d"] == 0
    evidence["4 b c d"]["encounter_count"] = 99
    assert snapshot.cards[0]["encounter_count_30d"] == 3
