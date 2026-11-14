"""Named pure/compatibility regressions; native proofs use the disposable script."""

from app.services import frozen_opening_order as order


def candidate(identifier, repertoire="repertoire", **extra):
    return {
        "id": identifier,
        "repertoire_id": repertoire,
        "gameplay_priority_reason": None,
        "priority_date": None,
        "priority_score": 100 if identifier == "a" else 0,
        "completed_line_ids_json": "[]",
        "frontier_decisions_json": "[]",
        "moves_json": "[]",
        **extra,
    }


def test_frozen_capacity_uses_existing_parent_gate_without_disabling_expansion():
    from app.services.opening_capacity_admission import plan_frozen_with_capacity
    from app.study.openings.capacity_frontier import CapacityRoute

    rows = [
        candidate("parent", frozen_rank=2, frozen_valid=True, frozen_token=["run", 1]),
        candidate(
            "ordinary", frozen_rank=3, frozen_valid=True, frozen_token=["run", 1]
        ),
    ]
    child = candidate(
        "child", frozen_rank=1, frozen_valid=True, frozen_token=["run", 1]
    )
    routes = (("child", CapacityRoute("repertoire", 1, "line", 1, "parent", False)),)
    selected = plan_frozen_with_capacity(
        "today",
        rows,
        {},
        {"repertoire": 3},
        routes,
        [child],
        miss_reason=order.MISS_REASON,
    )
    assert [row["id"] for _, row in selected] == ["parent", "child", "ordinary"]
    assert "capacity_route" in selected[1][1]


def test_override_replacement_restarts_eligibility_before_reusing_old_checkpoint(
    monkeypatch,
):
    from contextlib import contextmanager

    from app.services import postgres_queue_refresh as worker
    from app.services import study_mode

    @contextmanager
    def connection(**kwargs):
        yield object()

    captured = {}
    unlocked = []
    locks = []
    monkeypatch.setattr(worker.postgres, "configured", lambda: True)
    monkeypatch.setattr(worker, "background_read_connection", connection)
    monkeypatch.setattr(worker, "connection", connection)
    monkeypatch.setattr(order, "activation", lambda database, **kwargs: ("new-run", 2))
    monkeypatch.setattr(study_mode, "study_only_enabled", lambda: True)
    monkeypatch.setattr(
        worker, "lock_current_slice", lambda *args: locks.append(True) or True
    )
    monkeypatch.setattr(worker.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(
        worker.queue_application,
        "_unlock_eligible_opening_cards",
        lambda *args, **kwargs: unlocked.append(True),
    )
    monkeypatch.setattr(
        worker,
        "advance_task_slice_in_transaction",
        lambda database, task, **kwargs: captured.update(kwargs) or True,
    )
    assert worker.execute_postgres_queue_refresh_slice(
        {
            "payload": {
                "queue_date": "2026-10-14",
                "_queue_phase": "study_opening_item",
                "frozen_token": ["old-run", 1],
                "_refresh_cycle_epoch": 7,
            }
        }
    )
    assert (
        locks == [True]
        and unlocked == [True]
        and captured["next_phase"] == "block_opening"
    )
    assert captured["next_payload"]["frozen_token"] == ["new-run", 2]
    assert captured["next_payload"]["_refresh_cycle_epoch"] == 7
