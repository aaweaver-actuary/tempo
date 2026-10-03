"""Completed reviews survive mutable daily-queue projections without fabricated origins."""

from datetime import date, datetime, timedelta, timezone
import json
import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.models import ReviewRequest
from app.services.postgres_queue_refresh import _reconcile_one_unseen_entry
from app.review_conflicts import ReviewConflict


class NativeSqlite:
    def __init__(self, connection):
        self.connection = connection

    def execute_native(self, statement, parameters=()):
        return self.connection.execute(
            statement.replace("%s", "?").replace("FOR UPDATE OF q,c", ""), parameters,
        )


@pytest.fixture
def admitted_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "queue-recovery.db")
    database.initialize()
    today = date.today().isoformat()
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) "
            "VALUES('recovery','Recovery','synthetic',?,10)", (today,),
        )
        connection.execute(
            "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at) "
            "VALUES('recovery-card','recovery','prefix',?,'[\"e2e4\"]','learning',?,?)",
            ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", today, today),
        )
        connection.execute("INSERT INTO repertoire_cards VALUES('recovery','recovery-card')")
        queue_entry_id = connection.execute(
            "INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) "
            "VALUES(?,'recovery-card',0,'new','recovery')", (today,),
        ).lastrowid
    return queue_entry_id, ReviewRequest(
        queue_entry_id=queue_entry_id, outcome="correct", attempt_id="presented-before-refresh",
        expected_revision=1, recorded_at=datetime.now(timezone.utc).isoformat(),
    )


def test_limit_reconciliation_deleted_attempt_remains_saveable_and_idempotent(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("UPDATE repertoires SET new_cards_per_day=0 WHERE id='recovery'")
        assert not _reconcile_one_unseen_entry(
            NativeSqlite(connection), date.today().isoformat(),
            {"id": queue_entry_id, "card_id": "recovery-card", "repertoire_id": "recovery"}, 0,
        )
        assert connection.execute("SELECT id FROM daily_queue WHERE id=?", (queue_entry_id,)).fetchone() is None
    result = main._apply_review("recovery-card", request)
    assert result["persisted"] is True
    assert main._apply_review("recovery-card", request) == result
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM reviews WHERE card_id='recovery-card'").fetchone()[0] == 1
        assert connection.execute("SELECT introduced_at FROM cards WHERE id='recovery-card'").fetchone()[0] == date.today().isoformat()


def test_saved_attempt_receipt_precedes_archived_card_and_deleted_projection(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    saved = main._apply_review("recovery-card", request)
    with database.connection() as connection:
        connection.execute("DELETE FROM daily_queue WHERE id=?", (queue_entry_id,))
        connection.execute("UPDATE cards SET archived=1,revision=2 WHERE id='recovery-card'")
    assert main._apply_review("recovery-card", request) == saved


def test_recovered_projection_retains_completion_for_competing_review(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("DELETE FROM daily_queue WHERE id=?", (queue_entry_id,))
    saved = main._apply_review("recovery-card", request)
    with database.connection() as connection:
        origin = connection.execute("SELECT last_status,review_result_json FROM queue_attempt_origins WHERE queue_entry_id=?", (queue_entry_id,)).fetchone()
        assert origin["last_status"] == "complete"
        assert json.loads(origin["review_result_json"])["review_id"] == saved["review_id"]
    competing = request.model_copy(update={"attempt_id": "another-device-result", "recorded_at": (datetime.fromisoformat(request.recorded_at) - timedelta(seconds=1)).isoformat()})
    result = main._apply_review("recovery-card", competing)
    assert result["competing_review"]["outcome"] == "correct"
    assert main._apply_review("recovery-card", competing) == result
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2


def test_recovered_result_never_writes_another_cards_replacement_projection(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) SELECT 'another-card',repertoire_id,kind,start_fen,moves_json,state,due_date FROM cards WHERE id='recovery-card'")
        connection.execute("UPDATE daily_queue SET card_id='another-card' WHERE id=?", (queue_entry_id,))
    assert main._apply_review("recovery-card", request)["persisted"]
    with database.connection() as connection:
        replacement = connection.execute("SELECT status,review_result_json FROM daily_queue WHERE id=?", (queue_entry_id,)).fetchone()
        assert tuple(replacement) == ("queued", None)


@pytest.mark.parametrize("changed_fields", [
    {"outcome": "again"}, {"guided": True}, {"queue_entry_id": 999},
    {"expected_revision": 2}, {"expected_review_id": 1},
])
def test_logical_attempt_id_reuse_rejects_changed_payload(admitted_attempt, changed_fields):
    _, request = admitted_attempt
    main._apply_review("recovery-card", request)
    with pytest.raises(ReviewConflict, match="different result") as failure:
        main._apply_review("recovery-card", request.model_copy(update=changed_fields))
    assert failure.value.code == "review_attempt_reused"


@pytest.mark.parametrize("queue_identity,revision", [(999999, 1), (None, 1), ("original", 9)])
def test_missing_attempt_requires_retained_matching_provenance(admitted_attempt, queue_identity, revision):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("DELETE FROM daily_queue WHERE id=?", (queue_entry_id,))
    result = main._reconcile_review("recovery-card", request.model_copy(update={
        "queue_entry_id": queue_entry_id if queue_identity == "original" else queue_identity,
        "expected_revision": revision,
    }))
    assert result["persisted"] is False
    assert result["conflict"]["code"] == "queue_attempt_unprovable"
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


@pytest.mark.parametrize("mutation,code", [
    ("revision=2,moves_json='[\"d2d4\"]'", "card_revision_changed"),
    ("moves_json='[\"d2d4\"]'", "card_revision_changed"),
    ("archived=1", "card_archived"),
    ("superseded_by='replacement'", "card_replaced"),
    ("pending_validation=1", "card_integrity_blocked"),
    ("state='locked'", "card_integrity_blocked"),
])
def test_stale_attempt_never_credits_changed_archived_or_blocked_content(admitted_attempt, mutation, code):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("DELETE FROM daily_queue WHERE id=?", (queue_entry_id,))
        connection.execute(f"UPDATE cards SET {mutation} WHERE id='recovery-card'")
    result = main._reconcile_review("recovery-card", request)
    assert result == {"persisted": False, "conflict": {"code": code, "message": result["conflict"]["message"], "retryable": False}}
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_live_projection_cannot_authorize_changed_content_without_revision_bump(admitted_attempt):
    _, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("UPDATE cards SET moves_json='[\"d2d4\"]' WHERE id='recovery-card'")
    assert main._reconcile_review("recovery-card", request)["conflict"]["code"] == "card_revision_changed"


def test_retired_unchanged_projection_reconciles_after_integrity_repair(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("UPDATE daily_queue SET status='blocked' WHERE id=?", (queue_entry_id,))
    assert main._reconcile_review("recovery-card", request)["persisted"] is True
    assert main._apply_review("recovery-card", request)["persisted"] is True


def test_retained_guided_failure_requeues_once_after_projection_deletion(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("UPDATE daily_queue SET attempt_failed=1 WHERE id=?", (queue_entry_id,))
        connection.execute("UPDATE daily_queue SET attempt_failed=0 WHERE id=?", (queue_entry_id,))
        connection.execute("DELETE FROM daily_queue WHERE id=?", (queue_entry_id,))
    result = main._apply_review("recovery-card", request)
    assert result["requeue_entry_id"] is not None
    assert main._apply_review("recovery-card", request) == result
    with database.connection() as connection:
        review = connection.execute("SELECT rating,guided FROM reviews WHERE card_id='recovery-card'").fetchone()
        assert tuple(review) == ("again", 1)
        repeat = connection.execute("SELECT cycle,admission_kind FROM daily_queue WHERE id=?", (result["requeue_entry_id"],)).fetchone()
        assert tuple(repeat) == (1, "review")
        assert connection.execute("SELECT admission_kind FROM queue_attempt_origins WHERE queue_entry_id=?", (result["requeue_entry_id"],)).fetchone()[0] == "review"
        assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1


def test_recovered_review_admission_does_not_invent_a_new_introduction(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("DELETE FROM daily_queue WHERE id=?", (queue_entry_id,))
        connection.execute("UPDATE cards SET introduced_at=NULL WHERE id='recovery-card'")
        reviewed_entry = connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind) VALUES(?,'recovery-card',0,'review')", (date.today().isoformat(),)).lastrowid
        connection.execute("DELETE FROM daily_queue WHERE id=?", (reviewed_entry,))
    assert main._apply_review("recovery-card", request.model_copy(update={"queue_entry_id": reviewed_entry}))["persisted"]
    with database.connection() as connection:
        assert connection.execute("SELECT introduced_at FROM cards WHERE id='recovery-card'").fetchone()[0] is None


def test_buried_projection_is_an_explicit_conflict(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?", (queue_entry_id,))
    assert main._reconcile_review("recovery-card", request)["conflict"]["code"] == "queue_attempt_retired"


def test_conflict_http_contract_keeps_code_retryability_and_readable_detail(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    with database.connection() as connection:
        connection.execute("DELETE FROM daily_queue WHERE id=?", (queue_entry_id,))
        connection.execute("DELETE FROM queue_attempt_origins")
    client = TestClient(main.app)
    response = client.post("/api/cards/recovery-card/review", json=request.model_dump())
    assert response.status_code == 409
    assert response.json()["code"] == "queue_attempt_unprovable"
    assert response.json()["retryable"] is False
    assert isinstance(response.json()["detail"], str)
    reconciled = client.post("/api/cards/recovery-card/review/reconcile", json=request.model_dump())
    assert reconciled.status_code == 200
    assert reconciled.json()["conflict"]["code"] == response.json()["code"]


def test_sqlite_origin_backfill_runs_once_and_survives_reinitialization(admitted_attempt):
    queue_entry_id, _ = admitted_attempt
    with database.connection() as connection:
        for trigger in ("insert", "update", "delete", "revision"):
            connection.execute(f"DROP TRIGGER queue_attempt_origin_{trigger}")
        connection.execute("DROP TABLE queue_attempt_origins")
        connection.execute("DELETE FROM internal_migrations WHERE name='queue-attempt-origins-v1'")
    database.initialize()
    with database.connection() as connection:
        origin = dict(connection.execute("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=?", (queue_entry_id,)).fetchone())
        assert origin["legacy"] == 1 and origin["admission_kind"] == "new"
        connection.execute("DELETE FROM daily_queue WHERE id=?", (queue_entry_id,))
    database.initialize()
    with database.connection() as connection:
        assert dict(connection.execute("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=?", (queue_entry_id,)).fetchone()) == origin
