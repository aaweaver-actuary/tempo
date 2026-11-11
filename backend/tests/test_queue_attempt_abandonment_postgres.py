"""Exact abandonment against the existing isolated PostgreSQL owner fixture."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest

from native_study_fixture import owned_postgres, case, NOW
from tempo_storage.lifecycle import (
    queue_attempt_abandonment as abandonment,
)
from tempo_storage.study.issuance import stamp_issued_head
from tempo_storage.study.position_owner_epoch import owner_lock_key


def _new_attempt(case, *, buried=False):
    day = (NOW + timedelta(days=7)).date().isoformat()
    with case.transaction() as db:
        entry = db.execute_native(
            """INSERT INTO daily_queue(queue_date,card_id,position,status)
            VALUES(%s,%s,0,%s) RETURNING id""",
            (day, case.card_id, "buried" if buried else "queued"),
        ).fetchone()[0]
        stamp_issued_head(db, entry, case.card_id, 1, now=NOW)
    return {
        "queue_entry_id": entry,
        "card_id": case.card_id,
        "revision": 1,
        "operation_id": "abandon-" + case.card_id,
        "actor": "fixture-user",
        "reason": "user abandoned unfinished exercise",
    }


def _apply_payload(case, plan):
    with case.transaction() as db:
        fingerprint = abandonment.plan_in_writer(db, plan)["origin_sha256"]
    return {**plan, "expected_origin_sha256": fingerprint}


def _review(db, case, payload):
    from app.contracts.transport.study import ReviewRequest
    from app.services.learning_review_adapter import (
        LearningReviewAdapter,
        command_from_request,
    )
    from tempo_learning_review import ReviewPolicy, submit_review

    request = ReviewRequest(
        outcome="correct",
        queue_entry_id=payload["queue_entry_id"],
        expected_revision=payload["revision"],
        attempt_id="review-" + case.card_id,
    )
    adapter = LearningReviewAdapter(
        db,
        request,
        observed_at=NOW + timedelta(days=7),
        policy_provider=lambda: ReviewPolicy("UTC", 7),
    )
    command = command_from_request(case.card_id, request)
    return submit_review(adapter, command, lambda: adapter.context(command)).as_dict()


def test_native_abandonment_exact_retry_preservation_and_queue_cleanup(case):
    from app.services.study_attempt_guard import guard_current_attempt
    from app.contracts.review_conflicts import ReviewConflict

    plan = _new_attempt(case)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        before = dict(
            db.execute_native(
                "SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s",
                (plan["queue_entry_id"],),
            ).fetchone()
        )
        cards = tuple(
            db.execute_native(
                "SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s",
                (case.card_id,),
            ).fetchone()
        )
        reviews = db.execute_native(
            "SELECT count(*) FROM reviews WHERE card_id=%s", (case.card_id,)
        ).fetchone()[0]
        position_grades = db.execute_native(
            "SELECT count(*) FROM study_position_grades"
        ).fetchone()[0]
        assert (
            abandonment.abandon_in_writer(db, payload, now=NOW)["idempotent"] is False
        )
    with case.transaction() as db:
        assert abandonment.abandon_in_writer(db, payload, now=NOW)["idempotent"] is True
        with pytest.raises(ValueError, match="receipt_conflict"):
            abandonment.abandon_in_writer(
                db, {**payload, "reason": "different"}, now=NOW
            )
        with pytest.raises(ReviewConflict, match="abandoned"):
            guard_current_attempt(db, plan["queue_entry_id"], case.card_id, 1)
        db.execute_native(
            "UPDATE daily_queue SET status='skipped' WHERE id=%s",
            (plan["queue_entry_id"],),
        )
        db.execute_native(
            "DELETE FROM daily_queue WHERE id=%s", (plan["queue_entry_id"],)
        )
        assert (
            dict(
                db.execute_native(
                    "SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s",
                    (plan["queue_entry_id"],),
                ).fetchone()
            )
            == before
        )
        assert (
            tuple(
                db.execute_native(
                    "SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s",
                    (case.card_id,),
                ).fetchone()
            )
            == cards
        )
        assert (
            db.execute_native(
                "SELECT count(*) FROM reviews WHERE card_id=%s", (case.card_id,)
            ).fetchone()[0]
            == reviews
        )
        assert (
            db.execute_native("SELECT count(*) FROM study_position_grades").fetchone()[
                0
            ]
            == position_grades
        )


def test_native_changed_wrong_and_outer_rollback(case):
    plan = _new_attempt(case, buried=True)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        with pytest.raises(ValueError, match="origin_changed"):
            abandonment.abandon_in_writer(
                db, {**payload, "expected_origin_sha256": "0" * 64}, now=NOW
            )
        with pytest.raises(ValueError, match="missing_or_ambiguous"):
            abandonment.abandon_in_writer(db, {**payload, "revision": 2}, now=NOW)
    with pytest.raises(RuntimeError, match="outer rollback"):
        with case.transaction() as db:
            abandonment.abandon_in_writer(db, payload, now=NOW)
            raise RuntimeError("outer rollback")
    with case.transaction() as db:
        assert not abandonment.is_abandoned(db, plan["queue_entry_id"], case.card_id, 1)


@pytest.mark.parametrize("review_first", [True, False])
def test_native_review_and_abandonment_serialize_both_orders(
    case, monkeypatch, review_first
):
    from tempo_storage import postgres
    from app.contracts.review_conflicts import ReviewConflict
    from tempo_storage.learning_bridge import review as storage

    monkeypatch.setattr(postgres, "configured", lambda: True)
    plan = _new_attempt(case)
    payload = _apply_payload(case, plan)
    locked, release = Event(), Event()
    original = storage.lock_current_card
    if review_first:

        def hold_review(db, card_id):
            original(db, card_id)
            locked.set()
            assert release.wait(5)

        monkeypatch.setattr(storage, "lock_current_card", hold_review)

    def review():
        with case.transaction() as db:
            return _review(db, case, payload)

    def retire():
        with case.transaction() as db:
            result = abandonment.abandon_in_writer(db, payload, now=NOW)
            if not review_first:
                locked.set()
                assert release.wait(5)
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(review if review_first else retire)
        assert locked.wait(5)
        second = pool.submit(retire if review_first else review)
        release.set()
        first_result = first.result(timeout=10)
        assert first_result
        with pytest.raises((ValueError, ReviewConflict)):
            second.result(timeout=10)
    with case.transaction() as db:
        if review_first:
            assert not abandonment.is_abandoned(
                db, plan["queue_entry_id"], case.card_id, 1
            )
            before_receipt = tuple(
                db.execute_native(
                    "SELECT * FROM review_attempt_receipts WHERE attempt_id=%s",
                    ("review-" + case.card_id,),
                ).fetchone()
            )
            before_schedule = tuple(
                db.execute_native(
                    "SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s",
                    (case.card_id,),
                ).fetchone()
            )
            before_count = db.execute_native(
                "SELECT count(*) FROM reviews WHERE card_id=%s", (case.card_id,)
            ).fetchone()[0]
            before_position_grades = db.execute_native(
                "SELECT count(*) FROM study_position_grades"
            ).fetchone()[0]
            before_queue = [
                tuple(row)
                for row in db.execute_native(
                    "SELECT * FROM daily_queue WHERE card_id=%s ORDER BY id",
                    (case.card_id,),
                ).fetchall()
            ]
            replay = _review(db, case, payload)
            assert (
                replay == first_result
                and replay["persisted"]
                and replay["review_id"] == first_result["review_id"]
            )
            assert (
                replay["idempotent"] is False
            )  # Canonical receipt replay returns the saved wire result.
            assert (
                tuple(
                    db.execute_native(
                        "SELECT * FROM review_attempt_receipts WHERE attempt_id=%s",
                        ("review-" + case.card_id,),
                    ).fetchone()
                )
                == before_receipt
            )
            assert (
                tuple(
                    db.execute_native(
                        "SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s",
                        (case.card_id,),
                    ).fetchone()
                )
                == before_schedule
            )
            assert (
                db.execute_native(
                    "SELECT count(*) FROM reviews WHERE card_id=%s", (case.card_id,)
                ).fetchone()[0]
                == before_count
            )
            assert (
                db.execute_native(
                    "SELECT count(*) FROM study_position_grades"
                ).fetchone()[0]
                == before_position_grades
            )
            assert [
                tuple(row)
                for row in db.execute_native(
                    "SELECT * FROM daily_queue WHERE card_id=%s ORDER BY id",
                    (case.card_id,),
                ).fetchall()
            ] == before_queue
            assert not abandonment.is_abandoned(
                db, plan["queue_entry_id"], case.card_id, 1
            )
        else:
            assert abandonment.is_abandoned(db, plan["queue_entry_id"], case.card_id, 1)
            assert (
                db.execute_native(
                    "SELECT count(*) FROM review_attempt_receipts WHERE queue_entry_id=%s",
                    (plan["queue_entry_id"],),
                ).fetchone()[0]
                == 0
            )


def test_native_late_opening_evidence_rejected_before_writes(case):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.study.position_checkpoint import _validate_checkpoint_scope

    plan = _new_attempt(case)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        abandonment.abandon_in_writer(db, payload, now=NOW)
    manifest = SimpleNamespace(card_id=case.card_id, card_revision=1)
    request = SimpleNamespace(
        manifest=manifest,
        attempt_id="late-" + case.card_id,
        origin_queue_entry_id=plan["queue_entry_id"],
        queue_entry_id=plan["queue_entry_id"],
    )
    with pytest.raises(HTTPException) as error:
        with case.transaction() as db:
            # Any preceding work in a failed evidence transaction rolls back too.
            db.execute_native(
                "UPDATE cards SET interval_days=interval_days+1 WHERE id=%s",
                (case.card_id,),
            )
            _validate_checkpoint_scope(db, request, completing_review=False)
    assert error.value.detail["code"] == "queue_attempt_retired"
    with case.transaction() as db:
        assert (
            tuple(
                db.execute_native(
                    "SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s",
                    (case.card_id,),
                ).fetchone()
            )
            == case.initial
        )
        assert (
            db.execute_native(
                "SELECT count(*) FROM opening_evidence_attempts WHERE attempt_id=%s",
                (request.attempt_id,),
            ).fetchone()[0]
            == 0
        )






def test_native_receipt_and_origin_are_immutable(case):
    import psycopg

    plan = _new_attempt(case, buried=True)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        abandonment.abandon_in_writer(db, payload, now=NOW)
    for sql in (
        "UPDATE queue_attempt_abandonments SET reason=reason || 'changed' WHERE queue_entry_id=%s",
        "DELETE FROM queue_attempt_abandonments WHERE queue_entry_id=%s",
        "UPDATE queue_attempt_origins SET last_status='queued' WHERE queue_entry_id=%s",
        "DELETE FROM queue_attempt_origins WHERE queue_entry_id=%s",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            with case.transaction() as db:
                db.execute_native(sql, (plan["queue_entry_id"],))
    with case.transaction() as db:
        db.execute_native(
            "UPDATE queue_attempt_origins SET last_status=last_status WHERE queue_entry_id=%s",
            (plan["queue_entry_id"],),
        )


def test_native_late_failure_marker_conflict_rolls_back_prior_work(case, monkeypatch):
    from app.contracts.review_conflicts import ReviewConflict
    from app.queue_commands import mark_attempt_failed
    from tempo_storage import postgres

    monkeypatch.setattr(postgres, "configured", lambda: True)
    plan = _new_attempt(case)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        abandonment.abandon_in_writer(db, payload, now=NOW)
        original = dict(
            db.execute_native(
                "SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s",
                (plan["queue_entry_id"],),
            ).fetchone()
        )
        queue = tuple(
            db.execute_native(
                "SELECT * FROM daily_queue WHERE id=%s", (plan["queue_entry_id"],)
            ).fetchone()
        )
    with pytest.raises(ReviewConflict) as error:
        with case.transaction() as db:
            db.execute_native(
                "UPDATE cards SET interval_days=interval_days+1 WHERE id=%s",
                (case.card_id,),
            )
            mark_attempt_failed(
                db,
                {
                    "entry_id": plan["queue_entry_id"],
                    "card_id": case.card_id,
                    "expected_revision": 1,
                },
            )
    assert error.value.code == "queue_attempt_retired"
    with case.transaction() as db:
        assert (
            tuple(
                db.execute_native(
                    "SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s",
                    (case.card_id,),
                ).fetchone()
            )
            == case.initial
        )
        assert (
            tuple(
                db.execute_native(
                    "SELECT * FROM daily_queue WHERE id=%s", (plan["queue_entry_id"],)
                ).fetchone()
            )
            == queue
        )
        assert (
            dict(
                db.execute_native(
                    "SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s",
                    (plan["queue_entry_id"],),
                ).fetchone()
            )
            == original
        )


def test_native_schema56_without_abandonment_table(owned_postgres):
    """Apply 001–056 in a fresh child database on the already-owned cluster."""
    import hashlib
    import importlib.util
    from pathlib import Path
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from tempo_storage.postgres import (
        PostgresConnection,
        tempo_row_factory,
    )

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "abandonment_test_migrations",
        root / "database/tooling/apply_postgres_migrations.py",
    )
    migrations = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrations)
    options = dict(owned_postgres.connection_options)
    assert options["host"] == "127.0.0.1" and options["dbname"].startswith(
        "tempo_game_feedback_"
    )
    child = "tempo_abandonment_56_" + uuid4().hex
    with psycopg.connect(**options, autocommit=True) as admin:
        assert (
            admin.execute("SELECT current_database()").fetchone()[0]
            == options["dbname"]
        )
        assert (
            admin.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (child,)
            ).fetchone()
            is None
        )
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(child)
            )
        )
        try:
            child_options = {**options, "dbname": child}
            with psycopg.connect(**child_options, row_factory=tempo_row_factory) as raw:
                assert raw.execute("SELECT current_database()").fetchone()[0] == child
                assert (
                    raw.execute(
                        "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','S')"
                    ).fetchone()[0]
                    == 0
                )
                paths = sorted(migrations.MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
                for path in paths:
                    if int(path.name[:3]) > 56:
                        break
                    raw.execute(migrations.migration_source(path), prepare=False)
                assert [
                    row[0]
                    for row in raw.execute(
                        "SELECT version FROM tempo_schema_migrations ORDER BY version"
                    ).fetchall()
                ] == list(range(1, 57))
                database = PostgresConnection(raw)
                assert not abandonment.installed(database)
                assert not abandonment.is_abandoned(database, 13624, "unknown", 1)
                with pytest.raises(ValueError, match="schema_unavailable"):
                    abandonment.plan_in_writer(
                        database,
                        {
                            "queue_entry_id": 13624,
                            "card_id": "unknown",
                            "revision": 1,
                            "operation_id": "test",
                            "actor": "test",
                            "reason": "test",
                        },
                    )
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(child)))


def _retarget_attempt(case):
    """Retain the issued old origin while the row now projects another card."""
    from types import SimpleNamespace

    plan = _new_attempt(case)
    new_card = "replacement-" + case.card_id
    with case.transaction() as db:
        db.execute_native(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
            introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type)
            SELECT %s,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,2,
                   interval_days,fsrs_card_json,first_correct_at,stability,content_type
            FROM cards WHERE id=%s""",
            (new_card, case.card_id),
        )
        db.execute_native(
            "UPDATE daily_queue SET card_id=%s,status='blocked' WHERE id=%s",
            (new_card, plan["queue_entry_id"]),
        )
    return plan, SimpleNamespace(**(vars(case) | {"card_id": new_card}))


def _retarget_snapshot(db, plan, new_card):
    entry = plan["queue_entry_id"]
    return {
        "cards": [
            tuple(row)
            for row in db.execute_native(
                "SELECT * FROM cards WHERE id IN (%s,%s) ORDER BY id",
                (plan["card_id"], new_card),
            ).fetchall()
        ],
        "queue": [
            tuple(row)
            for row in db.execute_native(
                "SELECT * FROM daily_queue WHERE id=%s", (entry,)
            ).fetchall()
        ],
        "origins": [
            tuple(row)
            for row in db.execute_native(
                "SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s ORDER BY card_id,revision",
                (entry,),
            ).fetchall()
        ],
        "reviews": [
            tuple(row)
            for row in db.execute_native(
                "SELECT * FROM reviews WHERE card_id IN (%s,%s) ORDER BY id",
                (plan["card_id"], new_card),
            ).fetchall()
        ],
        "receipts": [
            tuple(row)
            for row in db.execute_native(
                "SELECT * FROM review_attempt_receipts WHERE queue_entry_id=%s ORDER BY attempt_id",
                (entry,),
            ).fetchall()
        ],
    }


def test_native_retargeted_origin_abandonment_preserves_current_review(
    case, monkeypatch
):
    from tempo_storage import postgres

    monkeypatch.setattr(postgres, "configured", lambda: True)
    plan, new_case = _retarget_attempt(case)
    # The foreign projection may already have an accepted review; it is not
    # evidence that the explicitly named old origin was completed.
    new_plan = {**plan, "card_id": new_case.card_id, "revision": 2}
    with case.transaction() as db:
        db.execute_native(
            "UPDATE daily_queue SET status='queued' WHERE id=%s",
            (plan["queue_entry_id"],),
        )
        result = _review(db, new_case, new_plan)
        assert result["persisted"]
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        before = _retarget_snapshot(db, plan, new_case.card_id)
        assert len(before["origins"]) == 2 and len(before["receipts"]) == 1
        assert abandonment.abandon_in_writer(db, payload, now=NOW)["abandoned"]
        assert _retarget_snapshot(db, plan, new_case.card_id) == before
        assert abandonment.is_abandoned(db, plan["queue_entry_id"], case.card_id, 1)
        assert not abandonment.is_abandoned(
            db, plan["queue_entry_id"], new_case.card_id, 2
        )
        assert abandonment.abandon_in_writer(db, payload, now=NOW)["idempotent"]
        with pytest.raises(ValueError, match="receipt_conflict"):
            abandonment.abandon_in_writer(db, {**payload, "reason": "other"}, now=NOW)


def test_native_retargeted_origin_holds_unknown_projection_and_changed_identity(
    case, monkeypatch
):
    plan, new_case = _retarget_attempt(case)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        with pytest.raises(ValueError, match="origin_changed"):
            abandonment.abandon_in_writer(
                db, {**payload, "expected_origin_sha256": "0" * 64}, now=NOW
            )
        with pytest.raises(ValueError, match="missing_or_ambiguous"):
            abandonment.abandon_in_writer(db, {**payload, "revision": 99}, now=NOW)
        db.execute_native(
            "DELETE FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id=%s",
            (plan["queue_entry_id"], new_case.card_id),
        )
        with pytest.raises(ValueError, match="queue_changed"):
            abandonment.abandon_in_writer(db, payload, now=NOW)
    assert not _is_retired(case, plan)


def test_native_retargeted_queue_identity_change_after_read_holds(case, monkeypatch):
    from tempo_storage import queue_position_lock

    plan, new_case = _retarget_attempt(case)
    payload = _apply_payload(case, plan)
    original = queue_position_lock.lock_queue_date_for_position

    def change_queue_before_lock(db, day):
        db.execute_native(
            "UPDATE daily_queue SET queue_date='2030-01-01' WHERE id=%s",
            (plan["queue_entry_id"],),
        )
        original(db, day)

    monkeypatch.setattr(
        queue_position_lock, "lock_queue_date_for_position", change_queue_before_lock
    )
    with case.transaction() as db:
        with pytest.raises(ValueError, match="queue_changed"):
            abandonment.abandon_in_writer(db, payload, now=NOW)
        assert not abandonment.is_abandoned(
            db, plan["queue_entry_id"], plan["card_id"], plan["revision"]
        )


def _is_retired(case, plan):
    with case.transaction() as db:
        return abandonment.is_abandoned(
            db, plan["queue_entry_id"], plan["card_id"], plan["revision"]
        )


def test_native_retargeted_old_accepted_review_still_holds(case, monkeypatch):
    from tempo_storage import postgres

    monkeypatch.setattr(postgres, "configured", lambda: True)
    plan = _new_attempt(case)
    with case.transaction() as db:
        assert _review(db, case, plan)["persisted"]
        # A separate retained current identity does not erase the old completion.
        new_card = "replacement-" + case.card_id
        db.execute_native(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
            introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type)
            SELECT %s,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,2,
                   interval_days,fsrs_card_json,first_correct_at,stability,content_type FROM cards WHERE id=%s""",
            (new_card, case.card_id),
        )
        db.execute_native(
            "UPDATE daily_queue SET card_id=%s,status='blocked',review_result_json=NULL WHERE id=%s",
            (new_card, plan["queue_entry_id"]),
        )
        with pytest.raises(
            ValueError, match="not_unfinished_issued_opening|review_already_accepted"
        ):
            abandonment.plan_in_writer(db, plan)
        assert not abandonment.is_abandoned(db, plan["queue_entry_id"], case.card_id, 1)


def test_native_retargeted_receipt_retry_survives_current_card_change(case):
    plan, new_case = _retarget_attempt(case)
    payload = _apply_payload(case, plan)
    with case.transaction() as db:
        accepted = abandonment.abandon_in_writer(db, payload, now=NOW)
        receipt = tuple(
            db.execute_native(
                "SELECT * FROM queue_attempt_abandonments WHERE operation_id=%s",
                (plan["operation_id"],),
            ).fetchone()
        )
    with case.transaction() as db:
        # No revision-3 origin is captured for this blocked projection.
        db.execute_native(
            "UPDATE cards SET revision=3 WHERE id=%s", (new_case.card_id,)
        )
        assert not db.execute_native(
            "SELECT 1 FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id=%s AND revision=3",
            (plan["queue_entry_id"], new_case.card_id),
        ).fetchone()
    with case.transaction() as db:
        replay = abandonment.abandon_in_writer(db, payload, now=NOW)
        assert replay == {**accepted, "idempotent": True}
        assert (
            tuple(
                db.execute_native(
                    "SELECT * FROM queue_attempt_abandonments WHERE operation_id=%s",
                    (plan["operation_id"],),
                ).fetchone()
            )
            == receipt
        )
        for changed in (
            {**payload, "reason": "changed"},
            {**payload, "operation_id": "other-operation"},
            {**payload, "card_id": new_case.card_id, "revision": 2},
        ):
            with pytest.raises(ValueError, match="receipt_conflict"):
                abandonment.abandon_in_writer(db, changed, now=NOW)


def test_native_foreign_card_lock_contention_holds_then_succeeds(case):
    plan, new_case = _retarget_attempt(case)
    payload = _apply_payload(case, plan)
    locked, release = Event(), Event()

    def hold_foreign():
        with case.transaction() as db:
            db.execute_native(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("tempo:card-edit:" + new_case.card_id,),
            )
            locked.set()
            assert release.wait(10)

    def abandon():
        with case.transaction() as db:
            return abandonment.abandon_in_writer(db, payload, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_foreign)
        try:
            assert locked.wait(5)
            attempt = pool.submit(abandon)
            with pytest.raises(ValueError, match="queue_changed"):
                attempt.result(timeout=5)
            assert not _is_retired(case, plan)
        finally:
            release.set()
            holder.result(timeout=10)
    with case.transaction() as db:
        assert abandonment.abandon_in_writer(db, payload, now=NOW)["abandoned"]
