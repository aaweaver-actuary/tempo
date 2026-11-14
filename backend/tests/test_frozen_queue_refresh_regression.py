"""The real refresh planner retains bounded frozen-order annotation."""

from contextlib import contextmanager

from app.services import postgres_queue_refresh as refresh


def test_active_frozen_order_refresh_annotates_pages_before_queue_plan(monkeypatch):
    rows = [
        {"id": n, "position": n, "content_type": "opening", "admission_kind": "study"}
        for n in range(1, 19)
    ]
    active = []
    batches = []

    @contextmanager
    def connection():
        active.append(True)
        try:
            yield object()
        finally:
            active.pop()

    def page(_db, _day, *, read, after_entry_id, limit):
        assert not active
        return [row for row in rows if row["id"] > after_entry_id][:limit]

    def annotate(_database, candidates):
        assert active
        batches.append(len(candidates))
        return [{**row, "frozen_valid": True} for row in candidates]

    def plan(candidates, *_args, **_kwargs):
        assert not active
        assert all(row["frozen_valid"] for row in candidates)
        return 7, "ignored", candidates

    monkeypatch.setattr(refresh, "background_read_connection", connection)
    monkeypatch.setattr(
        refresh.frozen_opening_order, "activation", lambda *_: ("run", 1)
    )
    monkeypatch.setattr(refresh.frozen_opening_order, "annotate", annotate)
    monkeypatch.setattr(refresh.presentation_storage, "saved_plan", lambda *_: None)
    monkeypatch.setattr(refresh.queue_application, "presentation_rows", page)
    monkeypatch.setattr(
        refresh.queue_ordering, "_queue_membership_hash", lambda *_: "hash"
    )
    monkeypatch.setattr(
        refresh.queue_ordering, "_queue_presentation_state", lambda *_: []
    )
    monkeypatch.setattr(refresh.queue_ordering, "_plan_daily_queue_order", plan)
    result = refresh._prepare_queue_randomization("2026-11-11")
    assert batches == [16, 2]
    assert [entry["id"] for entry in result["entries"]] == list(range(1, 19))
