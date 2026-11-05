"""Coverage request stays atomic on the caller's borrowed writer."""

import json

from app.services import coverage_seed_request as owner


def test_coverage_seed_request_preserves_writer_order_and_original_task_identity(
    monkeypatch,
):
    database = object()
    events = []

    def step(name, result=None):
        def invoke(db, *args, **kwargs):
            assert db is database
            events.append((name, args, kwargs))
            return result

        return invoke

    monkeypatch.setattr(owner.storage, "lock_coverage", step("lock"))
    monkeypatch.setattr(owner.storage, "repertoire_present", step("present", (1,)))
    monkeypatch.setattr(owner.storage, "active_run", step("active", None))
    monkeypatch.setattr(
        owner.storage,
        "settings_row",
        step(
            "settings",
            {
                "coverage_maia_elo": 1500,
                "coverage_reply_denominator": 2,
                "coverage_cumulative_target": 80,
                "coverage_horizon_fullmoves": 4,
                "coverage_path_floor": 0.01,
            },
        ),
    )
    monkeypatch.setattr(
        owner,
        "recent_player_cohort",
        step(
            "cohort",
            {
                "maia_elo": 1500,
                "explorer_rating": 1600,
                "recent_median_rating": 1500,
                "speed_weights": {"rapid": 1},
                "games": 3,
            },
        ),
    )
    monkeypatch.setattr(owner, "scope_identity", step("scope", {"scope_revision": 7}))
    monkeypatch.setattr(
        owner, "_source_fingerprint", step("fingerprint", "original-fingerprint")
    )
    monkeypatch.setattr(owner.storage, "insert_run", step("insert"))
    monkeypatch.setattr(owner, "enqueue_task_in_transaction", step("enqueue"))
    monkeypatch.setattr(owner, "_now", lambda: "fixed-time")
    monkeypatch.setattr(owner.uuid, "uuid4", lambda: "run-id")
    result = owner.request_coverage_seed_in_transaction(
        database, "rep", automatic=True, supersede_active=True
    )
    assert result == {"run_id": "run-id", "status": "queued"}
    assert [event[0] for event in events] == [
        "lock",
        "present",
        "active",
        "settings",
        "cohort",
        "scope",
        "fingerprint",
        "insert",
        "enqueue",
    ]
    assert events[5][1:] == (("rep",), {"lock": True})
    inserted = events[7][1][0]
    assert inserted[:2] == ("run-id", "rep") and inserted[-2:] == (
        "fixed-time",
        "fixed-time",
    )
    assert json.loads(inserted[2]) == {
        "automatic_priority": True,
        "scope_revision": 7,
        "reply_denominator": 2,
        "cumulative_target": 0.8,
        "horizon_fullmoves": 4,
        "path_floor": 0.01,
        "maia_elo": 1500,
        "explorer_rating": 1600,
        "recent_median_rating": 1500,
        "speed_weights": {"rapid": 1},
        "cohort_games": 3,
    }
    assert events[8][1] == (
        "coverage_seed",
        "rep",
        {
            "run_id": "run-id",
            "repertoire_id": "rep",
            "horizon_fullmoves": 4,
            "source_fingerprint": "original-fingerprint",
            "after_line_id": "",
            "position_index": 0,
        },
    )
    assert events[8][2] == {"priority": 75}


def test_coverage_seed_compatibility_export_is_same_owner_function():
    from app.services import postgres_coverage_seed as producer

    assert (
        producer.request_coverage_seed_in_transaction
        is owner.request_coverage_seed_in_transaction
    )
