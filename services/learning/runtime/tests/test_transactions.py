from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
import json, time, uuid
import chess, psycopg, pytest
from tempo_chess_core import card_id
from tempo_learning_runtime import review_adapter
from tempo_learning_runtime.database import connection
from tempo_learning_runtime.review_conflicts import ReviewConflict
from tempo_learning_runtime.maintenance import run_once
from test_runtime import activate, bundle, head, review_payload, submit


def with_completion(case, card, payload):
    moment = datetime.now(timezone.utc).isoformat()
    manifest = card["opening_decision_manifest"]
    decision = manifest["decisions"][0]
    checkpoint = {
        "attempt_id": payload["attempt_id"],
        "manifest": manifest,
        "origin_queue_entry_id": card["queue_entry_id"],
        "queue_entry_id": card["queue_entry_id"],
        "started_at": moment,
        "study_timezone": case.configuration.timezone,
        "events": [
            {
                "sequence": 1,
                "decision_index": 0,
                "decision_id": decision["decision_id"],
                "expected_uci": decision["expected_uci"],
                "kind": "first_response",
                "observed_at": moment,
                "response_uci": decision["expected_uci"],
                "disposition": "expected",
            }
        ],
        "terminal": {"state": "complete", "final_sequence": 1, "ended_at": moment},
    }
    return {**payload, "opening_evidence_completion": checkpoint}


def test_required_evidence_receipt_and_history_commit_together(case):
    activate(case)
    card = head(case)
    payload = with_completion(case, card, review_payload(card))
    response = submit(case, card, payload)
    assert response.status_code == 200, response.text
    row = case.query("SELECT state,review_id FROM learning.opening_evidence_attempts")[
        0
    ]
    assert tuple(row) == ("complete", response.json()["review_id"])
    assert (
        case.query("SELECT COUNT(*) FROM learning.opening_evidence_observations")[0][0]
        == 1
    )
    assert submit(case, card, payload).json() == response.json()
    assert (
        case.query("SELECT clean_successes FROM learning.opening_evidence_summaries")[
            0
        ][0]
        == 1
    )


@pytest.mark.parametrize(
    "handled", [False, True], ids=["handler_failure", "handled_reconciliation_conflict"]
)
def test_review_failure_rolls_back_required_evidence_history_and_logical_receipt(
    case, monkeypatch, handled
):
    activate(case)
    card = head(case)
    payload = with_completion(case, card, review_payload(card))
    original = review_adapter.LearningReviewAdapter.schedule

    def fail_after_schedule(self, *arguments, **keywords):
        original(self, *arguments, **keywords)
        raise ReviewConflict("injected_after_schedule", "Verify handler rollback")

    monkeypatch.setattr(
        review_adapter.LearningReviewAdapter, "schedule", fail_after_schedule
    )
    response = submit(case, card, payload, suffix="/reconcile" if handled else "")
    assert response.status_code == (200 if handled else 409), response.text
    if handled:
        assert response.json()["persisted"] is False
    for table in (
        "reviews",
        "review_attempt_receipts",
        "opening_evidence_attempts",
        "opening_evidence_observations",
        "advisory_notifications",
    ):
        assert case.query("SELECT COUNT(*) FROM learning." + table)[0][0] == 0, table
    assert case.query("SELECT status FROM learning.daily_queue")[0][0] == "queued"


def test_competing_review_payloads_have_one_logical_effect(case):
    activate(case)
    card = head(case)
    payload = review_payload(card)
    barrier = Barrier(2)

    def send(outcome):
        barrier.wait(timeout=5)
        return submit(case, card, {**payload, "outcome": outcome})

    with ThreadPoolExecutor(2) as executor:
        responses = [
            future.result()
            for future in [
                executor.submit(send, "correct"),
                executor.submit(send, "again"),
            ]
        ]
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
    assert (
        case.query("SELECT COUNT(*) FROM learning.review_attempt_receipts")[0][0] == 1
    )


def test_current_card_contention_is_bounded_and_original_intent_recovers(case):
    activate(case)
    card = head(case)
    payload = review_payload(card)
    operation = uuid.uuid4().hex
    with psycopg.connect(case.admin) as blocker:
        blocker.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("tempo:card-edit:" + card["id"],),
        )
        started = time.monotonic()
        response = submit(case, card, payload, operation)
        assert response.status_code == 503, response.text
        assert time.monotonic() - started < 3
        assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 0
    response = submit(case, card, payload, operation)
    assert response.status_code == 200, response.text
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1


def test_locked_optional_successor_cannot_prevent_saving_review(case):
    definitions = [
        (
            "opening",
            {
                "type": "move_line",
                "accepted_lines": [[move]],
                "mode": "single",
                "grading_policy": "reference",
            },
        )
        for move in ("e2e4", "d2d4")
    ]
    activate(case, bundle(definitions))
    response = case.client.get("/api/queue/window?include_opening_evidence=true")
    cards = response.json()["cards"]
    assert len(cards) == 2
    current, successor = cards
    payload = review_payload(
        current,
        prepared_next={
            "queue_entry_id": successor["queue_entry_id"],
            "card_id": successor["id"],
            "revision": successor["revision"],
            "cycle": successor["cycle"],
            "local_date": response.json()["local_date"],
        },
    )
    with psycopg.connect(case.admin) as blocker:
        blocker.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("tempo:card-edit:" + successor["id"],),
        )
        started = time.monotonic()
        saved = submit(case, current, payload)
        assert saved.status_code == 200, saved.text
        assert time.monotonic() - started < 2
        assert "prepared_continuation" not in saved.json()
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1


@pytest.mark.parametrize("mutation", ["revision", "deleted", "retired"])
def test_stale_and_deleted_attempts_do_not_create_effects(case, mutation):
    activate(case)
    card = head(case)
    payload = review_payload(card)
    if mutation == "revision":
        case.query(
            "UPDATE learning.cards SET revision=revision+1 WHERE id=%s", (card["id"],)
        )
    elif mutation == "deleted":
        case.query(
            "INSERT INTO learning.deleted_cards(card_id) VALUES(%s)", (card["id"],)
        )
    else:
        response = case.client.post(
            "/internal/publications/retire",
            json={
                "retirement_id": uuid.uuid4().hex,
                "publication_id": "publication-a",
                "publication_generation": 1,
                "expected_activation_revision": 1,
            },
            headers={"Authorization": "Bearer " + case.configuration.operator_token},
        )
        assert response.status_code == 200, response.text
        assert response.json()["stale_attempts_fenced"] is True
    response = submit(case, card, payload)
    assert response.status_code == 409, response.text
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 0


def test_retained_quota_and_parent_exposure_control_introductions(case):
    payload = bundle(
        [
            (
                "opening",
                {
                    "type": "move_line",
                    "accepted_lines": [[move]],
                    "mode": "single",
                    "grading_policy": "reference",
                },
            )
            for move in ("e2e4", "d2d4")
        ]
    )
    payload["repertoire_limits"] = {"repertoire-a": 1}
    activate(case, payload)
    card = head(case)
    assert case.query("SELECT COUNT(*) FROM learning.daily_queue")[0][0] == 1
    case.query("DELETE FROM learning.daily_queue")
    with case.database.scope():
        from tempo_learning_runtime.durable_tasks import enqueue_task_in_transaction

        with connection() as database:
            enqueue_task_in_transaction(
                database,
                "daily_queue",
                "current",
                {"queue_date": payload["facts"][0]["due_date"]},
            )
        for _ in range(20):
            if not run_once():
                break
    # Removing the replaceable projection cannot refund today's admission allowance.
    assert {
        row[0] for row in case.query("SELECT card_id FROM learning.daily_queue")
    } == {card["id"]}
    assert (
        case.query(
            "SELECT COUNT(DISTINCT card_id) FROM learning.queue_attempt_origins WHERE admission_kind='new'"
        )[0][0]
        == 1
    )


def test_background_work_yields_and_finishes_under_finite_contention(case):
    activate(case)
    card = head(case)
    from tempo_learning_runtime.durable_tasks import enqueue_task_in_transaction

    day = case.query("SELECT queue_date FROM learning.daily_queue")[0][0]
    with case.database.scope(), connection() as database:
        enqueue_task_in_transaction(
            database, "daily_queue", "current", {"queue_date": day}
        )
    with psycopg.connect(case.admin) as blocker:
        blocker.execute(
            "SELECT id FROM learning.cards WHERE id=%s FOR UPDATE", (card["id"],)
        )
        with case.database.scope():
            from tempo_learning_runtime.maintenance import safe_run_once

            started = time.monotonic()
            assert safe_run_once() is False
            assert time.monotonic() - started < 1
    with case.database.scope():
        for _ in range(20):
            if not run_once():
                break
        else:
            raise AssertionError("Finite workload did not complete")
    assert case.query("SELECT state FROM learning.background_tasks")[0][0] == "complete"


def test_workspace_calendar_uses_configured_local_day_across_utc_boundary():
    from tempo_learning_runtime.clock import workspace_timezone, date
    from unittest.mock import patch

    class BoundaryDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)

    token = workspace_timezone.set("America/New_York")
    try:
        with patch("tempo_learning_runtime.clock.datetime", BoundaryDatetime):
            assert date.today().isoformat() == "2026-10-15"
    finally:
        workspace_timezone.reset(token)
