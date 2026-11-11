"""Real SQL maintenance guard: preserve active work, allow retained completed history."""

from datetime import timedelta
import json
import pytest

from native_study_fixture import owned_postgres, case, NOW
from test_queue_attempt_abandonment_postgres import (
    _new_attempt,
    _apply_payload,
    _retarget_attempt,
)
from tempo_storage.opening_graph import worker
from tempo_storage.lifecycle import (
    queue_attempt_abandonment as abandonment,
)
from tempo_storage.study.issuance import stamp_issued_head


def test_native_completed_history_does_not_block_graph_cleanup_or_get_rewritten(case):
    past = (NOW - timedelta(days=1)).date().isoformat()
    today = NOW.date().isoformat()
    with case.transaction() as db:
        entry = db.execute_native(
            "INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES(%s,%s,0,'queued') RETURNING id",
            (past, case.card_id),
        ).fetchone()[0]
        stamp_issued_head(db, entry, case.card_id, 1, now=NOW)
        result = json.dumps({"outcome": "correct", "persisted": True})
        db.execute_native(
            "UPDATE daily_queue SET status='complete',review_result_json=%s WHERE id=%s",
            (result, entry),
        )
        db.execute_native(
            "UPDATE queue_attempt_origins SET last_status='complete',review_result_json=%s WHERE queue_entry_id=%s",
            (result, entry),
        )
        review = db.execute_native(
            "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(%s,'correct',%s,1,2) RETURNING id",
            (case.card_id, NOW.isoformat()),
        ).fetchone()[0]
        statements = [
            ("SELECT * FROM daily_queue WHERE id=%s", entry),
            ("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s", entry),
            ("SELECT * FROM reviews WHERE id=%s", review),
        ]
        before = [
            tuple(db.execute_native(sql, (key,)).fetchone()) for sql, key in statements
        ]
        assert worker.protected_queue_entry(db, ([case.card_id], today)) is None
        worker.retire_orphaned_queue(
            db, [case.card_id], "white"
        )
        assert [
            tuple(db.execute_native(sql, (key,)).fetchone()) for sql, key in statements
        ] == before


def test_native_unfinished_and_missing_completion_stay_protected_but_exact_abandonment_releases(
    case,
):
    payload = _new_attempt(case)
    other_day = (NOW + timedelta(days=8)).date().isoformat()
    with case.transaction() as db:
        assert worker.protected_queue_entry(db, ([case.card_id], other_day)) is not None
        db.execute_native(
            "UPDATE queue_attempt_origins SET last_status='complete',review_result_json=NULL WHERE queue_entry_id=%s",
            (payload["queue_entry_id"],),
        )
        assert worker.protected_queue_entry(db, ([case.card_id], other_day)) is not None
        db.execute_native(
            "UPDATE queue_attempt_origins SET last_status='queued' WHERE queue_entry_id=%s",
            (payload["queue_entry_id"],),
        )
    approved = _apply_payload(case, payload)
    with case.transaction() as db:
        abandonment.abandon_in_writer(db, approved, now=NOW)
        assert worker.protected_queue_entry(db, ([case.card_id], other_day)) is None
        db.execute_native("UPDATE cards SET revision=2 WHERE id=%s", (case.card_id,))
        db.execute_native(
            "UPDATE daily_queue SET status='queued' WHERE id=%s",
            (payload["queue_entry_id"],),
        )
        stamp_issued_head(db, payload["queue_entry_id"], case.card_id, 2, now=NOW)
        assert worker.protected_queue_entry(db, ([case.card_id], other_day)) is not None


def test_native_unissued_current_head_remains_protected(case):
    day = (NOW + timedelta(days=9)).date().isoformat()
    with case.transaction() as db:
        db.execute_native(
            "INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES(%s,%s,0,'queued')",
            (day, case.card_id),
        )
        assert worker.protected_queue_entry(db, ([case.card_id], day)) is not None
        assert (
            worker.protected_queue_entry(
                db, ([case.card_id], (NOW + timedelta(days=10)).date().isoformat())
            )
            is None
        )


@pytest.mark.parametrize("projection_absent", [False, True])
def test_native_retained_unfinished_origin_protected_without_matching_projection(
    case, projection_absent
):
    payload, foreign = _retarget_attempt(case)
    day = (NOW + timedelta(days=8)).date().isoformat()
    with case.transaction() as db:
        if projection_absent:
            db.execute_native(
                "DELETE FROM daily_queue WHERE id=%s", (payload["queue_entry_id"],)
            )
        assert worker.protected_queue_entry(db, ([case.card_id], day)) is not None
    approved = _apply_payload(case, payload)
    with case.transaction() as db:
        abandonment.abandon_in_writer(db, approved, now=NOW)
        assert worker.protected_queue_entry(db, ([case.card_id], day)) is None
