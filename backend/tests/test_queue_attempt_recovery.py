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
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('recovery','recovery-card')")
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


def test_study_migration_preserves_queue_attempt_provenance_and_triggers(tmp_path, monkeypatch):
    from app import study_migration

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "legacy-study.db")
    with monkeypatch.context() as legacy_schema:
        legacy_schema.setattr(study_migration, "migrate_studies", lambda: None)
        database.initialize()
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('legacy','Legacy','synthetic','2026-01-01')")
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES('legacy-card','legacy','prefix','retained-fen','[]','learning','2026-01-01')")
        queue_entry_id = connection.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES('2026-01-01','legacy-card',0)").lastrowid
        original = dict(connection.execute("SELECT * FROM queue_attempt_origins").fetchone())
    study_migration.migrate_studies()
    study_migration.migrate_studies()
    with database.connection() as connection:
        assert dict(connection.execute("SELECT * FROM queue_attempt_origins").fetchone()) == original
        connection.execute("UPDATE cards SET revision=2 WHERE id='legacy-card'")
        connection.execute("UPDATE daily_queue SET attempt_failed=1 WHERE id=?", (queue_entry_id,))
        connection.execute("DELETE FROM daily_queue WHERE id=?", (queue_entry_id,))
        retained = connection.execute("SELECT revision,attempt_failed FROM queue_attempt_origins ORDER BY revision").fetchall()
        assert [tuple(context) for context in retained] == [(1, 0), (2, 1)]


@pytest.mark.parametrize("with_identity", [False, True])
def test_stale_guided_marker_never_marks_reassigned_queue_content(admitted_attempt, with_identity):
    queue_entry_id, _ = admitted_attempt
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) SELECT 'another-card',repertoire_id,kind,start_fen,moves_json,state,due_date FROM cards WHERE id='recovery-card'")
        connection.execute("UPDATE daily_queue SET card_id='another-card' WHERE id=?", (queue_entry_id,))
    response = TestClient(main.app).post(f"/api/queue/entries/{queue_entry_id}/fail",
        json={"card_id": "recovery-card", "expected_revision": 1} if with_identity else None)
    assert response.status_code == 409
    assert response.json()["code"] == "queue_attempt_unprovable"
    with database.connection() as connection:
        assert connection.execute("SELECT attempt_failed FROM daily_queue WHERE id=?", (queue_entry_id,)).fetchone()[0] == 0


def test_identified_guided_marker_can_mark_only_current_replacement_context(admitted_attempt):
    queue_entry_id, _ = admitted_attempt
    with database.connection() as connection:
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) SELECT 'another-card',repertoire_id,kind,start_fen,moves_json,state,due_date FROM cards WHERE id='recovery-card'")
        connection.execute("UPDATE daily_queue SET card_id='another-card' WHERE id=?", (queue_entry_id,))
    response = TestClient(main.app).post(f"/api/queue/entries/{queue_entry_id}/fail",
        json={"card_id": "another-card", "expected_revision": 1})
    assert response.status_code == 200
    with database.connection() as connection:
        retained = connection.execute("SELECT card_id,attempt_failed FROM queue_attempt_origins WHERE queue_entry_id=? ORDER BY card_id", (queue_entry_id,)).fetchall()
        assert [tuple(row) for row in retained] == [("another-card", 1), ("recovery-card", 0)]


@pytest.mark.parametrize("marker_body", [None, {}, {"card_id": "recovery-card", "expected_revision": 1}])
def test_legacy_empty_guided_marker_body_preserves_unchanged_context(admitted_attempt, marker_body):
    queue_entry_id, _ = admitted_attempt
    response = TestClient(main.app).post(f"/api/queue/entries/{queue_entry_id}/fail", json=marker_body)
    assert response.status_code == 200
    assert response.json()["attempt_failed"] is True


@pytest.mark.parametrize("status", ["queued", "blocked", "complete"])
@pytest.mark.parametrize("reinitialize", [False, True])
def test_card_revision_retains_original_guided_failure_without_guiding_new_content(admitted_attempt, status, reinitialize):
    queue_entry_id, _ = admitted_attempt
    with database.connection() as connection:
        connection.execute("UPDATE daily_queue SET attempt_failed=1,status=? WHERE id=?", (status, queue_entry_id))
        original = dict(connection.execute("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=? AND revision=1", (queue_entry_id,)).fetchone())
        projection = dict(connection.execute("SELECT * FROM daily_queue WHERE id=?", (queue_entry_id,)).fetchone())
        if reinitialize:
            # Simulate an already-installed pre-repair SQLite trigger.
            connection.execute("DROP TRIGGER queue_attempt_origin_revision")
            connection.execute("""CREATE TRIGGER queue_attempt_origin_revision
                AFTER UPDATE OF revision ON cards WHEN NEW.revision!=OLD.revision BEGIN
                INSERT OR IGNORE INTO queue_attempt_origins
                SELECT q.id,NEW.id,NEW.revision,q.queue_date,q.cycle,q.admission_kind,
                       q.admission_repertoire_id,q.attempt_failed,q.status,q.review_result_json,0,
                       NEW.start_fen,NEW.moves_json,NEW.trained_color,NEW.content_type
                FROM daily_queue q WHERE q.card_id=NEW.id AND q.status='queued'; END""")
    if reinitialize:
        database.initialize()
        database.initialize()
    with database.connection() as connection:
        connection.execute("UPDATE cards SET revision=2,moves_json='[\"d2d4\"]' WHERE id='recovery-card'")
        revised = dict(connection.execute("SELECT * FROM daily_queue WHERE id=?", (queue_entry_id,)).fetchone())
        assert revised == {**projection, "attempt_failed": 1 if status == "complete" else 0}
        assert dict(connection.execute("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=? AND revision=1", (queue_entry_id,)).fetchone()) == original
        if status == "complete":
            assert connection.execute("SELECT COUNT(*) FROM queue_attempt_origins WHERE revision=2").fetchone()[0] == 0
        else:
            if status == "blocked":
                connection.execute("UPDATE daily_queue SET status='queued' WHERE id=?", (queue_entry_id,))
            new_origin = connection.execute("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=? AND revision=2", (queue_entry_id,)).fetchone()
            assert new_origin["attempt_failed"] == 0
            assert new_origin["admission_kind"] == original["admission_kind"]
            connection.execute("UPDATE cards SET revision=3,moves_json='[\"c2c4\"]' WHERE id='recovery-card'")
            assert [tuple(row) for row in connection.execute("SELECT revision,attempt_failed FROM queue_attempt_origins WHERE queue_entry_id=? ORDER BY revision", (queue_entry_id,))] == [(1, 1), (2, 0), (3, 0)]


def test_sqlite_revision_trigger_replacement_rolls_back_on_install_failure(admitted_attempt):
    from app.queue_attempt_origins import initialize_sqlite_origins

    class FailedTriggerInstall:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            if statement.startswith("CREATE TRIGGER queue_attempt_origin_revision"):
                raise RuntimeError("Simulated trigger install failure")
            return self.connection.execute(statement, parameters)

    with database.connection() as connection:
        original = connection.execute("SELECT sql FROM sqlite_master WHERE name='queue_attempt_origin_revision'").fetchone()[0]
        with pytest.raises(RuntimeError, match="trigger install failure"):
            initialize_sqlite_origins(FailedTriggerInstall(connection))
        assert connection.execute("SELECT sql FROM sqlite_master WHERE name='queue_attempt_origin_revision'").fetchone()[0] == original


def test_pre_evidence_aggregate_receipt_replays_without_inventing_a_new_result(admitted_attempt):
    queue_entry_id, request = admitted_attempt
    result = main._apply_review('recovery-card', request)
    with database.connection() as connection:
        row = connection.execute('SELECT request_json FROM review_attempt_receipts WHERE attempt_id=?', (request.attempt_id,)).fetchone()
        historical_request = json.loads(row[0])
        historical_request.pop('opening_evidence_completion', None)
        connection.execute('UPDATE review_attempt_receipts SET request_json=? WHERE attempt_id=?', (json.dumps(historical_request), request.attempt_id))
    assert main._apply_review('recovery-card', request) == result
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?', ('recovery-card',)).fetchone()[0] == 1


def test_queue_origin_migration_follows_current_main_without_renumbering_published_versions():
    from pathlib import Path
    from app.schema_version import POSTGRES_SCHEMA_VERSION
    root = Path(__file__).resolve().parents[1]
    migrations = sorted((root/'migrations').glob('[0-9][0-9][0-9]_*.sql'))
    assert [int(path.name[:3]) for path in migrations] == list(range(1, POSTGRES_SCHEMA_VERSION+1))
    assert (root/'migrations/030_opening_decision_evidence.sql').is_file()
    assert (root/'migrations/031_defensive_analysis_pause.sql').is_file()
    queue_migration = root/'migrations/032_queue_attempt_origins.sql'
    assert 'INSERT INTO tempo_schema_migrations(version) VALUES (32);' in queue_migration.read_text()
    assert POSTGRES_SCHEMA_VERSION == 37
    assert (root/'migrations/037_prefix_transition_application.sql').is_file()
