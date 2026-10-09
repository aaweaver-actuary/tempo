"""Regular-suite progress/incident regressions with controlled execution evidence."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from app import database
from app.services import activity_health
from app.services.durable_tasks import (
    enqueue_task,
    claim_task,
    advance_task_slice_in_transaction,
)
from app.services.database_executor import database_writer

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


@pytest.fixture
def health_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "health.db")
    database.initialize()
    database_writer.start()
    try:
        yield
    finally:
        database_writer.stop()


def test_activity_health_claim_heartbeat_retry_and_generation_replacement_are_not_progress(
    health_db,
):
    task = enqueue_task("daily_queue", "health", {})
    claimed = claim_task("daily_queue")
    with database.connection() as db:
        assert (
            db.execute(
                "SELECT last_progress_at FROM activity_work_progress WHERE work_id=?",
                (task["id"],),
            ).fetchone()[0]
            is None
        )
        db.execute(
            "UPDATE background_tasks SET updated_at=? WHERE id=?",
            (NOW.isoformat(), task["id"]),
        )
        assert advance_task_slice_in_transaction(
            db, claimed, next_phase="unlock", next_payload={"cursor": "one"}
        )
        first = dict(
            db.execute(
                "SELECT * FROM activity_work_progress WHERE work_id=?", (task["id"],)
            ).fetchone()
        )
        assert first["last_progress_at"] and first["progress_version"] == 1
    replacement = enqueue_task("daily_queue", "health", {"replacement": True})
    with database.read_connection() as db:
        later = dict(
            db.execute(
                "SELECT * FROM activity_work_progress WHERE work_id=?", (task["id"],)
            ).fetchone()
        )
        assert later["generation_key"] == str(replacement["generation"])
        assert (
            later["last_progress_at"] == first["last_progress_at"]
            and later["progress_version"] == 1
        )
        assert later["progress_generation"] != later["generation_key"]


@pytest.mark.parametrize(
    "running,idle,timeouts,expected",
    [
        (299.999, 0, 0, None),
        (300, 0, 0, "running_stalled"),
        (0, 899.999, 0, None),
        (0, 900, 0, "queue_stalled"),
        (0, 0, 4, None),
        (0, 0, 5, "repeated_timeout"),
    ],
)
def test_activity_stall_balanced_threshold_boundaries(
    running, idle, timeouts, expected
):
    assert (
        activity_health.stall_reason(
            state="leased" if running else "queued",
            admitted_seconds=running,
            idle_seconds=idle,
            identical_timeouts=timeouts,
            eligible=True,
            evidence_available=True,
        )
        == expected
    )


@pytest.mark.parametrize(
    "exclusion",
    [
        "foreground",
        "manual_pause",
        "settings_disabled",
        "retry_delay",
        "blocked",
        "unknown",
    ],
)
def test_activity_stall_excludes_expected_waiting_and_unavailable_evidence(exclusion):
    assert (
        activity_health.stall_reason(
            state="leased",
            admitted_seconds=600,
            idle_seconds=1800,
            identical_timeouts=8,
            eligible=exclusion != "blocked",
            evidence_available=exclusion != "unknown",
            waiting_reason=exclusion,
        )
        == None
    )


def test_activity_incident_deduplicates_persists_and_requires_affected_forward_progress(
    health_db,
):
    task = enqueue_task("daily_queue", "incident", {})
    with database.connection() as db:
        work = dict(
            db.execute(
                "SELECT * FROM activity_work_progress WHERE work_id=?", (task["id"],)
            ).fetchone()
        )
        first = activity_health.reconcile_incident(db, work, "repeated_timeout", NOW)
        assert (
            activity_health.reconcile_incident(
                db, work, "repeated_timeout", NOW + timedelta(minutes=1)
            )
            == first
        )
    replacement = enqueue_task("daily_queue", "incident", {"new": True})
    with database.connection() as db:
        work = dict(
            db.execute(
                "SELECT * FROM activity_work_progress WHERE work_id=?",
                (replacement["id"],),
            ).fetchone()
        )
        activity_health.reconcile_incident(db, work, None, NOW + timedelta(minutes=2))
        assert (
            db.execute(
                "SELECT resolved_at FROM activity_notification_incidents WHERE id=?",
                (first,),
            ).fetchone()[0]
            is None
        )
    claimed = claim_task("daily_queue")
    with database.connection() as db:
        assert advance_task_slice_in_transaction(
            db, claimed, next_phase="unlock", next_payload={"cursor": "accepted"}
        )
        work = dict(
            db.execute(
                "SELECT * FROM activity_work_progress WHERE work_id=?", (task["id"],)
            ).fetchone()
        )
        activity_health.reconcile_incident(db, work, None, NOW + timedelta(minutes=3))
        activity_health.reconcile_incident(db, work, None, NOW + timedelta(minutes=4))
        assert (
            db.execute("SELECT COUNT(*) FROM activity_notification_changes").fetchone()[
                0
            ]
            == 2
        )
        assert db.execute(
            "SELECT resolved_at FROM activity_notification_incidents WHERE id=?",
            (first,),
        ).fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM background_tasks").fetchone()[0] == 1
    assert len(activity_health.notification_changes()["items"]) == 2


def test_activity_identical_timeout_series_is_fenced_and_resets_only_after_progress(
    health_db,
):
    task = enqueue_task("daily_queue", "timeout", {})
    claimed = claim_task("daily_queue")
    for index in range(5):
        activity_health.record_execution_error(
            claimed, "transaction_timeout", execution_id=str(index)
        )
        activity_health.record_execution_error(
            claimed, "transaction_timeout", execution_id=str(index)
        )
    with database.read_connection() as db:
        assert (
            db.execute(
                "SELECT identical_timeouts FROM activity_work_progress WHERE work_id=?",
                (task["id"],),
            ).fetchone()[0]
            == 5
        )
    activity_health.record_execution_error(
        claimed, "ordinary_error", execution_id="different"
    )
    with database.read_connection() as db:
        assert (
            db.execute(
                "SELECT identical_timeouts FROM activity_work_progress WHERE work_id=?",
                (task["id"],),
            ).fetchone()[0]
            == 0
        )


def select_only_pipeline(kind, now=NOW):
    with database.connection() as db:
        db.execute(
            "UPDATE activity_pipeline_health SET bootstrap_ready=1,checked_at=?",
            ((now + timedelta(hours=1)).isoformat(),),
        )
        db.execute(
            "UPDATE activity_pipeline_health SET checked_at=NULL WHERE kind=?", (kind,)
        )


def test_activity_execution_accounting_is_admitted_restart_safe_and_lease_fenced(
    health_db,
):
    task = enqueue_task("daily_queue", "execution", {})
    claimed = claim_task("daily_queue", lease_seconds=900)
    claimed["lease_expires_at"] = (NOW + timedelta(seconds=900)).isoformat()
    with database.connection() as db:
        db.execute(
            "UPDATE background_tasks SET lease_expires_at=? WHERE id=?",
            (claimed["lease_expires_at"], claimed["id"]),
        )
    execution = activity_health.begin_execution(claimed, now=NOW)
    assert execution == claimed["lease_token"]
    assert (
        activity_health.begin_execution(claimed, now=NOW + timedelta(seconds=30))
        is None
    )
    stale = {**claimed, "lease_token": "stale"}
    assert (
        activity_health.begin_execution(stale, now=NOW + timedelta(seconds=600)) is None
    )
    activity_health.finish_execution(
        claimed, execution, admission_wait_seconds=20, now=NOW + timedelta(seconds=40)
    )
    activity_health.finish_execution(
        claimed, execution, now=NOW + timedelta(seconds=50)
    )
    with database.read_connection() as db:
        assert (
            db.execute(
                "SELECT admitted_seconds FROM activity_work_progress WHERE work_id=?",
                (task["id"],),
            ).fetchone()[0]
            == 20
        )
    # An expired claim has a bounded recorded execution interval, not an
    # unbounded wall clock or credit for stale/replayed completion.
    with database.connection() as db:
        db.execute(
            "UPDATE activity_work_progress SET execution_id=?,execution_admitted_at=?,execution_expires_at=? WHERE work_id=?",
            (
                "old",
                (NOW - timedelta(seconds=90)).isoformat(),
                (NOW - timedelta(seconds=30)).isoformat(),
                task["id"],
            ),
        )
    next_execution = activity_health.begin_execution(claimed, now=NOW)
    assert next_execution
    with database.read_connection() as db:
        assert (
            db.execute(
                "SELECT admitted_seconds FROM activity_work_progress WHERE work_id=?",
                (task["id"],),
            ).fetchone()[0]
            == 80
        )


def test_activity_monitor_idle_threshold_counts_observed_capacity_and_excludes_foreground_gaps(
    health_db,
):
    task = enqueue_task("daily_queue", "idle-threshold", {})
    select_only_pipeline("daily_queue")
    with database.connection() as db:
        db.execute(
            "UPDATE activity_work_progress SET eligible_idle_seconds=899,idle_observed_at=? WHERE work_id=?",
            ((NOW - timedelta(seconds=1)).isoformat(), task["id"]),
        )
    idle = {int((NOW - timedelta(seconds=1)).timestamp()): 1}
    assert activity_health.monitor_one_pipeline(
        now=NOW,
        evidence={
            "available": True,
            "foreground": True,
            "idle_sampler": lambda _a, _b: idle,
        },
    )
    assert activity_health.notification_changes()["items"] == []
    with database.connection() as db:
        db.execute(
            "UPDATE activity_work_progress SET idle_observed_at=? WHERE work_id=?",
            ((NOW + timedelta(seconds=59)).isoformat(), task["id"]),
        )
    idle = {int((NOW + timedelta(seconds=59)).timestamp()): 1}
    assert activity_health.monitor_one_pipeline(
        now=NOW + timedelta(seconds=60),
        evidence={
            "available": True,
            "foreground": False,
            "idle_sampler": lambda _a, _b: idle,
        },
    )
    assert (
        activity_health.notification_changes()["items"][0]["reason"] == "queue_stalled"
    )


def test_activity_monitor_running_uses_execution_after_last_progress_and_stays_unknown_without_evidence(
    health_db,
):
    task = enqueue_task("daily_queue", "running-threshold", {})
    claim_task("daily_queue")
    select_only_pipeline("daily_queue")
    with database.connection() as db:
        db.execute(
            "UPDATE activity_work_progress SET execution_id=?,execution_admitted_at=?,execution_expires_at=?,last_progress_at=? WHERE work_id=?",
            (
                "admitted",
                (NOW - timedelta(seconds=600)).isoformat(),
                (NOW + timedelta(seconds=600)).isoformat(),
                (NOW - timedelta(seconds=299)).isoformat(),
                task["id"],
            ),
        )
    assert activity_health.monitor_one_pipeline(
        now=NOW, evidence={"available": True, "foreground": False}
    )
    assert activity_health.notification_changes()["items"] == []
    assert activity_health.monitor_one_pipeline(
        now=NOW + timedelta(seconds=60),
        evidence={"available": False, "foreground": False},
    )
    with database.read_connection() as db:
        assert (
            db.execute(
                "SELECT health FROM activity_pipeline_health WHERE kind='daily_queue'"
            ).fetchone()[0]
            == "unknown"
        )
    assert activity_health.notification_changes()["items"] == []
    assert activity_health.monitor_one_pipeline(
        now=NOW + timedelta(seconds=120),
        evidence={"available": True, "foreground": False},
    )
    assert (
        activity_health.notification_changes()["items"][0]["reason"]
        == "running_stalled"
    )


def test_activity_monitor_verified_timeout_episode_remains_actionable_when_worker_evidence_is_unavailable(
    health_db,
):
    enqueue_task("daily_queue", "verified-errors", {})
    claimed = claim_task("daily_queue")
    for index in range(5):
        activity_health.record_execution_error(
            claimed, "transaction_timeout", execution_id=str(index)
        )
    select_only_pipeline("daily_queue")
    assert activity_health.monitor_one_pipeline(
        now=NOW, evidence={"available": False, "foreground": False}
    )
    assert (
        activity_health.notification_changes()["items"][0]["reason"]
        == "repeated_timeout"
    )
    assert activity_health.notification_changes()["monitoring_available"] is False


def test_activity_monitor_bounded_eligibility_cache_does_not_let_obsolete_work_hide_current_queued_work(
    health_db,
):
    now = NOW.isoformat()
    with database.connection() as db:
        db.execute(
            "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('current-game','lichess','test',?,'rapid',1,'white','win','start','[]')",
            (now,),
        )
        db.execute(
            "INSERT INTO game_analysis_jobs(game_id,status,analysis_version,updated_at) VALUES('current-game','queued',1,?)",
            (now,),
        )
    obsolete = enqueue_task(
        "game_analysis_followup",
        "obsolete",
        {"game_id": "missing", "analysis_version": 1},
    )
    current = enqueue_task(
        "game_analysis_followup",
        "current",
        {"game_id": "current-game", "analysis_version": 1},
    )
    select_only_pipeline("game_analysis_followup")
    with database.connection() as db:
        db.execute(
            "UPDATE activity_work_progress SET pending_since=? WHERE work_id=?",
            ((NOW - timedelta(days=1)).isoformat(), obsolete["id"]),
        )
        db.execute(
            "UPDATE activity_work_progress SET eligible_idle_seconds=900 WHERE work_id=?",
            (current["id"],),
        )
    assert activity_health.monitor_one_pipeline(
        now=NOW, evidence={"available": True, "foreground": False}
    )
    incident = activity_health.notification_changes()["items"][0]
    assert (
        incident["work_id"] == current["id"] and incident["reason"] == "queue_stalled"
    )
    with database.read_connection() as db:
        assert (
            db.execute(
                "SELECT eligible FROM activity_work_progress WHERE work_id=?",
                (obsolete["id"],),
            ).fetchone()[0]
            == 0
        )


def test_activity_notification_read_pagination_and_unavailable_monitoring_do_not_report_healthy(
    health_db,
):
    task = enqueue_task("daily_queue", "pagination", {})
    with database.connection() as db:
        work = dict(
            db.execute(
                "SELECT * FROM activity_work_progress WHERE work_id=?", (task["id"],)
            ).fetchone()
        )
        activity_health.reconcile_incident(db, work, "queue_stalled", NOW)
    page = activity_health.notification_changes(limit=1)
    assert (
        page["available"]
        and not page["monitoring_available"]
        and page["monitoring_as_of"] is None
    )
    assert page["next_cursor"] == 1 and not page["has_more"]
    assert activity_health.notification_changes(after=1)["items"] == []
    from fastapi.testclient import TestClient
    from app.main import app

    assert (
        TestClient(app).get("/api/system/activity/notifications?after=-1").status_code
        == 422
    )
    assert (
        TestClient(app).get("/api/system/activity?work_source=durable").status_code
        == 422
    )


def test_activity_notification_details_link_filters_exact_affected_work_and_retains_unknown_health(
    health_db,
):
    from fastapi.testclient import TestClient
    from app.main import app

    task = enqueue_task("daily_queue", "details", {})
    enqueue_task("daily_queue", "other-details", {})
    claim_task("daily_queue")
    select_only_pipeline("daily_queue")
    with database.connection() as db:
        db.execute(
            "UPDATE activity_work_progress SET admitted_seconds=300 WHERE work_id=?",
            (task["id"],),
        )
    assert activity_health.monitor_one_pipeline(
        now=NOW, evidence={"available": True, "foreground": False}
    )
    response = TestClient(app).get(
        "/api/system/activity", params={"work_source": "durable", "work_id": task["id"]}
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [task["id"]]
    assert response.json()["items"][0]["classification"] == "needs_attention"


def test_activity_monitor_control_worker_is_not_analysis_capacity(
    health_db, monkeypatch
):
    from app.services import background_runtime, redis_admission_gate

    enqueue_task("daily_queue", "control-capacity", {})
    select_only_pipeline("daily_queue")
    control = background_runtime.RuntimeMeasurement(
        "other", worker_role="control"
    ).sample()
    monkeypatch.setattr(
        background_runtime,
        "snapshot",
        lambda: background_runtime.RuntimeSnapshot(available=True, workers=[control]),
    )
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: False)
    assert activity_health.monitor_one_pipeline(now=NOW)
    with database.read_connection() as db:
        pipeline = db.execute(
            "SELECT available,health FROM activity_pipeline_health WHERE kind='daily_queue'"
        ).fetchone()
        assert tuple(pipeline) == (0, "unknown")
    assert activity_health.notification_changes()["items"] == []


def test_activity_monitor_idle_evidence_outage_remains_unknown_without_discarding_errors(
    health_db,
):
    task = enqueue_task("daily_queue", "idle-outage", {})
    select_only_pipeline("daily_queue")

    def unavailable(_start, _end):
        raise ConnectionError("idle store unavailable")

    assert activity_health.monitor_one_pipeline(
        now=NOW,
        evidence={"available": True, "foreground": False, "idle_sampler": unavailable},
    )
    with database.read_connection() as db:
        assert (
            db.execute(
                "SELECT health FROM activity_pipeline_health WHERE kind='daily_queue'"
            ).fetchone()[0]
            == "unknown"
        )
        assert (
            db.execute(
                "SELECT eligible_idle_seconds FROM activity_work_progress WHERE work_id=?",
                (task["id"],),
            ).fetchone()[0]
            == 0
        )
    assert activity_health.notification_changes()["items"] == []


def test_activity_engine_reclaimed_lease_retains_only_bounded_admitted_execution(
    health_db,
):
    with database.connection() as db:
        db.execute(
            "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('reclaimed-game','lichess','test',?,'rapid',1,'white','win','start','[]')",
            (NOW.isoformat(),),
        )
        db.execute(
            "INSERT INTO game_analysis_jobs(game_id,status,analysis_version,updated_at) VALUES('reclaimed-game','queued',1,?)",
            (NOW.isoformat(),),
        )
        db.execute(
            "UPDATE game_analysis_jobs SET status='leased',lease_id='first',lease_expires_at=?,updated_at=? WHERE game_id='reclaimed-game'",
            ((NOW + timedelta(seconds=60)).isoformat(), NOW.isoformat()),
        )
        db.execute(
            "UPDATE game_analysis_jobs SET lease_id='replacement',lease_expires_at=?,updated_at=? WHERE game_id='reclaimed-game'",
            (
                (NOW + timedelta(seconds=150)).isoformat(),
                (NOW + timedelta(seconds=90)).isoformat(),
            ),
        )
        db.execute(
            "UPDATE game_analysis_jobs SET updated_at=? WHERE game_id='reclaimed-game'",
            ((NOW + timedelta(seconds=100)).isoformat(),),
        )
        observed = db.execute(
            "SELECT admitted_seconds,execution_id FROM activity_work_progress WHERE work_id='reclaimed-game'"
        ).fetchone()
        assert (
            observed[0] == pytest.approx(60, abs=0.001) and observed[1] == "replacement"
        )


def test_activity_monitor_notifies_terminal_identical_timeouts_without_retrying_historical_failure(
    health_db,
):
    task = enqueue_task("daily_queue", "terminal-timeouts", {})
    claimed = claim_task("daily_queue")
    for index in range(5):
        activity_health.record_execution_error(
            claimed, "transaction_timeout", execution_id=str(index)
        )
    with database.connection() as db:
        db.execute(
            "UPDATE background_tasks SET state='failed',next_attempt_at=?,last_error='transaction timeout',updated_at=? WHERE id=?",
            ((NOW + timedelta(seconds=60)).isoformat(), NOW.isoformat(), task["id"]),
        )
    select_only_pipeline("daily_queue")
    assert activity_health.monitor_one_pipeline(
        now=NOW, evidence={"available": False, "foreground": False}
    )
    assert (
        activity_health.notification_changes()["items"][0]["reason"]
        == "repeated_timeout"
    )
    with database.read_connection() as db:
        assert (
            db.execute(
                "SELECT state FROM background_tasks WHERE id=?", (task["id"],)
            ).fetchone()[0]
            == "failed"
        )


@pytest.mark.parametrize("source", ["coverage", "sync", "derivation"])
def test_activity_native_parent_envelopes_do_not_compete_with_executable_child_stages(
    health_db, monkeypatch, source
):
    with database.read_connection() as db:
        monkeypatch.setattr(activity_health.postgres_store, "configured", lambda: True)
        work = {
            "manual_paused": 0,
            "kind": "game_sync_window",
            "state": "queued",
            "next_attempt_at": None,
            "source": source,
        }
        assert activity_health._eligible_work(db, work, NOW, True) == (False, "blocked")


def test_activity_engine_queue_uses_engine_idle_capacity_not_an_idle_analysis_worker(
    health_db,
):
    with database.connection() as db:
        db.execute(
            "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('engine-idle-game','lichess','test',?,'rapid',1,'white','win','start','[]')",
            (NOW.isoformat(),),
        )
        db.execute(
            "INSERT INTO game_analysis_jobs(game_id,status,analysis_version,updated_at) VALUES('engine-idle-game','queued',1,?)",
            (NOW.isoformat(),),
        )
        db.execute(
            "UPDATE activity_work_progress SET eligible_idle_seconds=899,idle_observed_at=? WHERE work_id='engine-idle-game'",
            ((NOW - timedelta(seconds=1)).isoformat(),),
        )
    select_only_pipeline("engine_game")
    idle = {int((NOW - timedelta(seconds=1)).timestamp()): 1}
    assert activity_health.monitor_one_pipeline(
        now=NOW,
        evidence={
            "available": True,
            "engine_available": False,
            "foreground": False,
            "idle_sampler": lambda _a, _b: idle,
        },
    )
    assert activity_health.notification_changes()["items"] == []
    with database.connection() as db:
        assert (
            db.execute(
                "SELECT health FROM activity_pipeline_health WHERE kind='engine_game'"
            ).fetchone()[0]
            == "unknown"
        )
        db.execute(
            "UPDATE activity_work_progress SET idle_observed_at=? WHERE work_id='engine-idle-game'",
            ((NOW + timedelta(seconds=59)).isoformat(),),
        )
    idle = {int((NOW + timedelta(seconds=59)).timestamp()): 1}
    assert activity_health.monitor_one_pipeline(
        now=NOW + timedelta(seconds=60),
        evidence={
            "available": False,
            "engine_available": True,
            "foreground": False,
            "engine_idle_sampler": lambda _a, _b: idle,
        },
    )
    assert (
        activity_health.notification_changes()["items"][0]["reason"] == "queue_stalled"
    )


def test_activity_bootstrap_yields_between_pipelines_and_checkpoints_bounded_pages(
    health_db,
):
    with database.connection() as db:
        for index in range(65):
            db.execute(
                "INSERT INTO background_tasks(id,kind,deduplication_key,next_attempt_at,created_at,updated_at) VALUES(?,'daily_queue',?,?,?,?)",
                (
                    f"bootstrap-{index:03d}",
                    f"bootstrap-{index:03d}",
                    NOW.isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
        db.execute(
            "UPDATE activity_pipeline_health SET bootstrap_ready=1,checked_at=?",
            ((NOW + timedelta(hours=1)).isoformat(),),
        )
        db.execute(
            "UPDATE activity_pipeline_health SET bootstrap_ready=0,checked_at=NULL WHERE kind IN ('daily_queue','priority_retention')"
        )
    for turn in range(4):
        assert activity_health.monitor_one_pipeline(
            now=NOW + timedelta(seconds=turn),
            evidence={"available": False, "foreground": False},
        )
        with database.read_connection() as db:
            daily = db.execute(
                "SELECT bootstrap_cursor,bootstrap_ready FROM activity_pipeline_health WHERE kind='daily_queue'"
            ).fetchone()
            if turn == 0:
                assert tuple(daily) == ("bootstrap-031", 0)
            if turn == 1:
                assert tuple(daily) == ("bootstrap-031", 0)
                assert (
                    db.execute(
                        "SELECT bootstrap_ready FROM activity_pipeline_health WHERE kind='priority_retention'"
                    ).fetchone()[0]
                    == 1
                )
            if turn == 2:
                assert tuple(daily) == ("bootstrap-063", 0)
            if turn == 3:
                assert tuple(daily) == ("bootstrap-064", 1)
    with database.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM background_tasks").fetchone()[0] == 65
        assert (
            db.execute(
                "SELECT MAX(progress_version) FROM activity_work_progress"
            ).fetchone()[0]
            == 0
        )
