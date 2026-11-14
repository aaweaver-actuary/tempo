"""Bounded essential opening selection and durable phase policy without PostgreSQL."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime

import chess
import pytest
from tempo_chess_core import card_id

from app.services import postgres_queue_refresh as worker
from app.services import study_mode


def candidate(moves=None):
    moves = moves or ["e2e4"]
    return {
        "card_id": card_id(chess.STARTING_FEN, moves),
        "repertoire_id": "rep",
        "reason": None,
        "study_route": {
            "generation": 1,
            "line_id": "line",
            "decision_index": 0,
            "content": [
                chess.STARTING_FEN,
                json.dumps(moves),
                "white",
                chess.STARTING_FEN,
                json.dumps(moves),
            ],
        },
    }


def test_study_opening_validation_is_pure_and_preserves_card_identity():
    planned = candidate()
    assert worker._study_opening_content_valid(planned)
    planned["study_route"]["content"][3] = chess.STARTING_FEN.replace("0 1", "7 4")
    assert worker._study_opening_content_valid(planned)
    planned["card_id"] = "unrelated"
    assert not worker._study_opening_content_valid(planned)


def test_study_opening_graph_order_is_stable_without_analysis_or_postgres():
    rows = [
        ("2026-10-02", "rep-b", "2026-10-01", "line-b", 0, "aaa"),
        ("2026-10-01", "rep-a", "2026-10-01", "line-a", 1, "aaa"),
        ("2026-10-01", "rep-a", "2026-10-01", "line-a", 0, "zzz"),
    ]
    expected = [rows[2], rows[1], rows[0]]
    assert sorted(rows, key=worker._study_opening_cursor) == expected
    assert sorted(reversed(rows), key=worker._study_opening_cursor) == expected


def test_study_opening_selector_reads_only_one_page_per_slice(monkeypatch):
    planned = candidate()
    content = planned["study_route"]["content"]
    rows = [
        (
            "2026-10-01",
            "rep",
            "2026-10-01",
            f"line-{index:02}",
            0,
            planned["card_id"],
            1,
            *content,
        )
        for index in range(16)
    ]
    reads = []

    def bounded_read(statement, parameters=(), *, native=False):
        reads.append((statement, parameters))
        if len(reads) == 1:
            return [(True,)]
        if len(reads) == 2:
            return [rows[-1][:6]]
        assert len(reads) == 3
        assert parameters[-1] == 16 and "LIMIT %s" in statement
        return rows

    class Reader:
        def execute_native(self, statement, parameters=()):
            reads.append((statement, parameters))
            return self

        def fetchall(self):
            if len(reads) == 2:
                return [rows[-1][:6]]
            assert len(reads) == 3
            assert reads[-1][1][-1] == 16 and "LIMIT %s" in reads[-1][0]
            return rows

    @contextmanager
    def reader():
        yield Reader()

    @contextmanager
    def snapshot(*, repertoire_snapshot=False):
        assert repertoire_snapshot
        yield Reader()

    monkeypatch.setattr(worker.read_storage, "_bounded_read", bounded_read)
    monkeypatch.setattr(worker, "background_read_connection", reader)
    monkeypatch.setattr(worker.read_storage, "background_read_connection", reader)
    monkeypatch.setattr(worker.read_storage, "read_connection", snapshot)
    monkeypatch.setattr(worker, "_study_opening_route_still_valid", lambda *_: True)
    monkeypatch.setattr(worker.frozen_opening_order, "annotate", lambda _, rows: rows)
    monkeypatch.setattr(worker.frozen_opening_order, "proposal_valid", lambda *_: True)
    monkeypatch.setattr(
        worker.openings_storage, "remaining_allowance", lambda *_: (16,)
    )
    page = worker._prepare_study_opening_page("2026-10-13", {})
    assert len(reads) == 3 and page["examined"] == 16
    assert len(page["items"]) == 1  # shared route occurrences, one card
    assert page["has_more"]


@pytest.mark.parametrize(
    "moves,color",
    [(["e2e5"], "white"), (["e2e4"], "black"), ([17], "white"), ([], "white")],
)
def test_study_opening_validation_rejects_invalid_published_content(moves, color):
    planned = candidate()
    planned["study_route"]["content"][1] = json.dumps(moves)
    planned["study_route"]["content"][2] = color
    assert not worker._study_opening_content_valid(planned)


@pytest.fixture
def phase_harness(monkeypatch):
    saved = {}
    instant = datetime(2026, 10, 13, 12, tzinfo=UTC)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz else instant.replace(tzinfo=None)

    class Writer:
        def execute(self, statement, parameters=()):
            assert statement == "SELECT timezone FROM settings WHERE id=1 FOR SHARE"
            assert parameters == ()
            saved["timezone_locked"] = True
            return self

        def fetchone(self):
            return ("UTC",)

    @contextmanager
    def section(*, background):
        assert background
        yield Writer()

    def advance(database, task, *, next_phase, next_payload):
        saved.update(phase=next_phase, payload=next_payload)
        return True

    monkeypatch.setattr(worker.postgres, "configured", lambda: True)
    monkeypatch.setattr(worker.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(worker, "connection", section)
    monkeypatch.setattr(worker, "datetime", FixedClock)

    def lock_slice(database, task):
        assert saved.pop("timezone_locked", False), (
            "timezone must be fenced before the task lease"
        )
        return True

    monkeypatch.setattr(worker, "lock_current_slice", lock_slice)
    monkeypatch.setattr(worker, "advance_task_slice_in_transaction", advance)
    return saved


@pytest.mark.parametrize(
    "previous_phase", ["prioritized_openings", "prioritized_opening_item"]
)
def test_study_only_replaces_optional_ranked_checkpoint_with_bounded_page(
    previous_phase, phase_harness, monkeypatch
):
    monkeypatch.setattr(study_mode, "study_only_enabled", lambda: True)
    monkeypatch.setattr(
        worker,
        "_prepare_study_opening_page",
        lambda *_: {
            "items": [candidate()],
            "cursor": ["rep", "line", 0],
            "upper": ["rep", "line", 1],
            "has_more": True,
            "examined": 16,
            "stop_reason": None,
        },
    )
    worker.execute_postgres_queue_refresh_slice(
        {
            "payload": {
                "queue_date": "2026-10-13",
                "_queue_phase": previous_phase,
                "_refresh_cycle_epoch": 1,
                "opening_plan": [{"card_id": "obsolete-ranked-card"}],
                "opening_index": 0,
            }
        }
    )
    assert phase_harness["phase"] == "study_opening_item"
    assert "opening_plan" not in phase_harness["payload"]
    assert len(phase_harness["payload"]["study_opening_items"]) == 1


def test_study_opening_rejected_page_advances_without_starvation(
    phase_harness, monkeypatch
):
    monkeypatch.setattr(study_mode, "study_only_enabled", lambda: True)
    monkeypatch.setattr(
        worker,
        "_prepare_study_opening_page",
        lambda *_: {
            "items": [],
            "cursor": ["rep", "line", 16],
            "upper": ["rep", "line", 30],
            "has_more": True,
            "examined": 16,
            "stop_reason": None,
        },
    )
    worker.execute_postgres_queue_refresh_slice(
        {
            "payload": {
                "queue_date": "2026-10-13",
                "_queue_phase": "study_opening_page",
                "_refresh_cycle_epoch": 1,
            }
        }
    )
    assert phase_harness["phase"] == "study_opening_page"
    assert phase_harness["payload"]["study_opening_cursor"] == ["rep", "line", 16]
    assert (
        phase_harness["payload"]["study_opening_counts"]["excluded_or_no_allowance"]
        == 16
    )


def test_study_opening_publishes_one_item_then_checkpoints_even_when_mode_resumes(
    phase_harness, monkeypatch
):
    monkeypatch.setattr(study_mode, "study_only_enabled", lambda: False)
    admitted = []
    from app.services import position_queue_admission

    monkeypatch.setattr(
        position_queue_admission,
        "prepare_opening_introduction",
        lambda *a: ("legacy", "rep"),
    )
    monkeypatch.setattr(
        position_queue_admission, "fence_opening_introduction", lambda *a: None
    )

    @contextmanager
    def snapshot(*, repertoire_snapshot=False):
        assert repertoire_snapshot
        yield object()

    monkeypatch.setattr(worker, "read_connection", snapshot)
    monkeypatch.setattr(
        position_queue_admission, "redundant_opening_introduction", lambda *_: False
    )
    monkeypatch.setattr(
        worker,
        "_admit_one_prioritized_opening",
        lambda db, day, planned: admitted.append(planned["card_id"]) or False,
    )
    payload = {
        "queue_date": "2026-10-13",
        "_queue_phase": "study_opening_item",
        "_refresh_cycle_epoch": 1,
        "study_opening_items": [candidate(), candidate(["d2d4"])],
        "study_opening_index": 0,
        "study_opening_more": True,
        "study_opening_counts": {"examined": 16, "eligible": 2},
    }
    worker.execute_postgres_queue_refresh_slice({"payload": payload})
    assert len(admitted) == 1
    assert phase_harness["phase"] == "study_opening_item"
    assert phase_harness["payload"]["study_opening_index"] == 1
    assert phase_harness["payload"]["study_opening_counts"]["publication_rejected"] == 1


def test_normal_mode_resumes_retired_priority_checkpoint_with_study_page(
    phase_harness, monkeypatch
):
    monkeypatch.setattr(study_mode, "study_only_enabled", lambda: False)
    monkeypatch.setattr(
        worker,
        "_prepare_study_opening_page",
        lambda *_: {
            "items": [candidate()],
            "cursor": ["rep", "line", 0],
            "upper": ["rep", "line", 1],
            "has_more": True,
            "examined": 16,
            "stop_reason": None,
        },
    )
    worker.execute_postgres_queue_refresh_slice(
        {
            "payload": {
                "queue_date": "2026-10-13",
                "_queue_phase": "prioritized_openings",
                "_refresh_cycle_epoch": 1,
                "opening_plan": [{"card_id": "obsolete-ranked-card"}],
            }
        }
    )
    assert phase_harness["phase"] == "study_opening_item"
    assert "opening_plan" not in phase_harness["payload"]
    assert len(phase_harness["payload"]["study_opening_items"]) == 1


@pytest.fixture(autouse=True)
def legacy_study_order_has_no_accepted_snapshot(monkeypatch):
    from app.services import frozen_opening_order

    monkeypatch.setattr(
        frozen_opening_order, "activation", lambda database, **kwargs: (None, 0)
    )
    # Existing tests replace the owning reader for route/allowance proofs.
    # Metadata reads use that seam without introducing provider/model access.
    original_reader = worker.background_read_connection

    @contextmanager
    def metadata_compatible_reader():
        import os

        from tempo_storage import postgres

        if postgres.configured() and not os.getenv("TEMPO_DATABASE_READ_URL"):
            yield object()
        else:
            with original_reader() as database:
                yield database

    monkeypatch.setattr(
        worker, "background_read_connection", metadata_compatible_reader
    )
    monkeypatch.setattr(
        worker.read_storage, "background_read_connection", metadata_compatible_reader
    )


def test_opening_position_preparation_uses_unreserved_repertoire_snapshot(
    phase_harness, monkeypatch
):
    """Large active-owner sources must not time out on the short writer budget."""
    from app.services import position_queue_admission

    reads = []

    @contextmanager
    def snapshot(*, repertoire_snapshot=False):
        reads.append(repertoire_snapshot)
        yield object()

    monkeypatch.setattr(worker, "read_connection", snapshot)

    class Prepared(Exception):
        pass

    def stop_after_prepare(*_):
        raise Prepared()

    monkeypatch.setattr(
        position_queue_admission, "prepare_opening_introduction", stop_after_prepare
    )
    monkeypatch.setattr(
        position_queue_admission, "fence_opening_introduction", lambda *_: None
    )
    monkeypatch.setattr(worker, "_admit_one_prioritized_opening", lambda *_: False)
    payload = {
        "queue_date": "2026-10-13",
        "_queue_phase": "study_opening_item",
        "_refresh_cycle_epoch": 1,
        "study_opening_items": [candidate(), candidate(["d2d4"])],
        "study_opening_index": 0,
        "study_opening_more": True,
        "study_opening_counts": {"examined": 2, "eligible": 2},
    }
    with pytest.raises(Prepared):
        worker.execute_postgres_queue_refresh_slice({"payload": payload})
    assert reads == [True]


@pytest.mark.parametrize("stale", [False, True])
def test_opening_snapshot_closes_before_writer_and_stale_fence_holds(
    phase_harness, monkeypatch, stale
):
    from tempo_evidence_math.position_learning import Hold

    from app.services import position_queue_admission

    active = []

    @contextmanager
    def snapshot(*, repertoire_snapshot=False):
        assert repertoire_snapshot
        active.append(True)
        try:
            yield object()
        finally:
            active.pop()

    def fence(*_):
        assert not active, "source snapshot must close before writer fencing"
        return Hold("position_source_changed") if stale else None

    monkeypatch.setattr(worker, "read_connection", snapshot)
    monkeypatch.setattr(
        position_queue_admission,
        "prepare_opening_introduction",
        lambda *_: ("position", object()),
    )
    monkeypatch.setattr(position_queue_admission, "fence_opening_introduction", fence)
    monkeypatch.setattr(
        position_queue_admission, "redundant_opening_introduction", lambda *_: False
    )
    monkeypatch.setattr(worker, "_admit_one_prioritized_opening", lambda *_: False)
    payload = {
        "queue_date": "2026-10-13",
        "_queue_phase": "study_opening_item",
        "_refresh_cycle_epoch": 1,
        "study_opening_items": [candidate(), candidate(["d2d4"])],
        "study_opening_index": 0,
        "study_opening_more": True,
        "study_opening_counts": {"examined": 2, "eligible": 2},
    }
    if stale:
        with pytest.raises(RuntimeError, match="position_source_changed"):
            worker.execute_postgres_queue_refresh_slice({"payload": payload})
        assert not phase_harness
    else:
        assert worker.execute_postgres_queue_refresh_slice({"payload": payload})
        assert phase_harness["payload"]["study_opening_index"] == 1
