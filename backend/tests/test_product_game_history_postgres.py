"""Schema051 root/window/control proof using only task-owned synthetic history.

The root middleware/router, public claim/control and real receiving/Study owners
run unchanged. Only database transport and clocks point to the disposable owner
fixture. This is source/native qualification, not an installed-image claim.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient
import pytest

from app.product_composition import game_history_current as current
from app.product_composition import game_history_runtime as runtime
from product_encounter_fixtures import retained_history_case
from test_product_feedback_integration import _issue, _ordinary_review
from test_product_game_lapse import NOW
from test_product_game_lapse_postgres import owned_postgres

PREFIX = "/api/product/game-history"


@pytest.fixture
def root_history(owned_postgres, monkeypatch):
    from tempo_legacy_database.adapters import postgres_store
    from app.services import redis_admission_gate, study_priority, learning_review_adapter
    from app import queue_commands

    @contextmanager
    def transport(*, read_only=False, **_options):
        with owned_postgres() as database:
            if read_only:
                database.execute_native("SET TRANSACTION READ ONLY")
            yield database

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or timezone.utc)

    class StudyDate(date):
        @classmethod
        def today(cls):
            return NOW.date()

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_store, "connection", transport)
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: False)
    monkeypatch.setattr(runtime, "datetime", FrozenDateTime)
    monkeypatch.setattr(queue_commands, "date", StudyDate)
    monkeypatch.setattr(learning_review_adapter, "date", StudyDate)
    monkeypatch.delenv("TEMPO_FOREGROUND_ACTIVITY_URL", raising=False)
    monkeypatch.setenv("TEMPO_RETAINED_HISTORY_ENABLED", "1")
    monkeypatch.setenv("TEMPO_RETAINED_HISTORY_QUEUE_MODE", "postgres-celery")
    # Importing the actual root constructs only a lazy history router. The
    # TestClient does not run lifespan/start Redis, Celery or a legacy worker.
    from app import main
    lazy = main.current_game_history_router.__globals__["CurrentHistoryOperations"]
    operations = next(route.endpoint.__closure__ for route in main.app.routes
        if getattr(route, "path", "") == PREFIX + "/preview")
    # Resolve the router's supplied operations without replacing its endpoints.
    closure = dict(zip(next(route.endpoint.__code__.co_freevars for route in main.app.routes
        if getattr(route, "path", "") == PREFIX + "/preview"),
        (cell.cell_contents for cell in operations)))
    root_operations = closure["operations"]
    assert isinstance(root_operations, lazy)
    root_operations._operations = None
    client = TestClient(main.app)
    try:
        yield client, root_operations, study_priority
    finally:
        root_operations._operations = None
        with owned_postgres() as database:
            database.execute_native("DELETE FROM background_tasks WHERE kind='game_feedback_history'")


def body(case, run_id="bounded-history", **changes):
    selection = dict(workspace_id="legacy", provider="lichess", account="SYNTHETIC",
        window=dict(start_at=(NOW-timedelta(days=2)).isoformat(), end_at=NOW.isoformat(), precision="second"),
        repertoire_ids=[case.repertoire_id], speeds=["rapid"], rated=False,
        maximum_games=4, maximum_positions=10)
    selection.update(changes)
    return dict(run_id=run_id, selection=selection)


def status(client, run_id="bounded-history"):
    response = client.get(PREFIX + "/runs/" + run_id)
    assert response.status_code == 200, response.text
    return response.json()


def test_actual_root_window_pause_resume_and_normal_study(root_history, owned_postgres):
    client, root, priority = root_history
    from app.services.background_activity import set_control_in_transaction
    from app.services.durable_tasks import enqueue_task_in_transaction, claim_task
    from app.services.activity_gate import activity_gate
    from tempo_legacy_database.adapters.durable_tasks import storage
    with retained_history_case(owned_postgres, state="unintroduced", ordinary_limit=0, study_only=True) as case:
        assert priority.refresh_policy()
        with case.transaction() as database:
            protected = _issue(database, case.protected_id)
            # Same account/window but explicitly excluded; unrelated account and
            # outside-window sources are not selected by the actual SQL reader.
            for suffix, account, played_at, excluded in (
                ("-excluded", "synthetic", NOW-timedelta(days=1), 1),
                ("-other", "someone-else", NOW-timedelta(days=1), 0),
                ("-old", "synthetic", NOW-timedelta(days=30), 0),
                ("-unproven", "synthetic", NOW-timedelta(days=1), 0),
            ):
                database.execute_native("""INSERT INTO imported_games(id,provider,provider_game_id,username,
                    played_at,speed,rated,color,result,start_fen,moves_json,content_hash,adaptive_excluded)
                    SELECT id||%s,provider,provider_game_id||%s,%s,%s,speed,rated,color,result,
                        start_fen,moves_json,content_hash,%s FROM imported_games WHERE id=%s""",
                    (suffix,suffix,account,played_at.isoformat(),excluded,case.game_ids[0]))
            database.execute_native("UPDATE imported_games SET provider_game_id=NULL WHERE id=%s",
                (case.game_ids[0]+"-unproven",))
        before = case.readback()
        request = body(case)
        preview = client.post(PREFIX + "/preview", json=request)
        assert preview.status_code == 200, preview.text
        report = preview.json()["preview"]
        assert (report["selected_games"], report["compared_games"], report["positions_compared"]) == (4,2,2)
        assert (report["matches"], report["differing_responses"], report["unintroduced_encounters"]) == (1,1,2)
        assert report["excluded_games"] == 1 and report["unknown_authority_or_mapping_games"] == 1
        assert report["diagnostics"][0]["reason"] == "retained_provider_identity_unavailable"
        assert report["eligible_lapses"] == 0 and report["matches_are_lower_bound"]
        assert report["execution_ready"] and not report["more_available"]
        assert case.readback() == before
        start = dict(request, manifest_digest=preview.json()["manifest_digest"])
        response = client.post(PREFIX + "/start", json=start)
        assert response.status_code == 202, response.text
        with case.transaction() as database:
            row = dict(storage.task_by_kind(database, ("game_feedback_history",
                root._operations._plans[(request["run_id"],start["manifest_digest"])].task_identity)))
            assert set_control_in_transaction(database,"durable",row["id"],"pause")
            unrelated = enqueue_task_in_transaction(database,"opening_graph_rebuild","history-unrelated",{})
        paused = client.post(PREFIX + "/run-ready", json=dict(maximum_units=3))
        assert paused.status_code == 200 and paused.json()["processed_units"] == 0
        assert status(client)["processed_encounters"] == 0 and case.readback() == before
        with case.transaction() as database:
            assert set_control_in_transaction(database,"durable",row["id"],"resume")
        first = client.post(PREFIX + "/run-ready", json=dict(maximum_units=1))
        assert first.status_code == 200 and first.json()["processed_units"] == 1, first.text
        assert status(client)["processed_encounters"] == 1
        # A new API composition has no private preview cache. The approved
        # original durable manifest still resumes, retaining key and detection.
        root._operations = None
        resumed = client.post(PREFIX + "/resume", json=dict(maximum_units=4))
        assert resumed.status_code == 200, resumed.text
        saved = status(client)
        assert saved["state"] == "complete" and saved["processed_encounters"] == 2
        assert [item["disposition"] for item in saved["receipts"]] == ["first_introduction","frequency_only"]
        assert [item["admission_status"] for item in saved["receipts"]] == ["available","not_requested"]
        assert all(item["detected_at"] == NOW.isoformat() for item in saved["receipts"])
        with activity_gate.background_job("game_feedback_history","unrelated-check",yielding=True):
            assert claim_task("opening_graph_rebuild") is None
        with case.transaction() as database:
            assert storage.task_by_id(database,(unrelated["id"],))["state"] == "queued"
            assert tuple(database.execute_native("SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1").fetchone()) == (0,1)
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s",(case.card_id,)).fetchone()[0] == 0
            _ordinary_review(database,case.protected_id,protected,"history-protected:"+case.card_id)
            target = _issue(database,case.card_id)
            assert _ordinary_review(database,case.card_id,target,"history-first:"+case.card_id)["persisted"]
        learned = case.readback()
        replay = client.post(PREFIX + "/resume", json=dict(maximum_units=3))
        assert replay.status_code == 200 and replay.json()["processed_units"] == 0
        assert case.readback() == learned
        with case.transaction() as database:
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",(case.card_id,)).fetchone()[0] == 0
            database.execute_native("DELETE FROM background_tasks WHERE id=%s",(unrelated["id"],))
            database.execute_native("DELETE FROM imported_games WHERE id=ANY(%s)",([case.game_ids[0]+suffix for suffix in ("-excluded","-other","-old","-unproven")],))


def test_owner_effect_and_history_checkpoint_rollback_then_original_resume(root_history,owned_postgres,monkeypatch):
    client, root, priority = root_history
    from app.services import durable_tasks
    with retained_history_case(owned_postgres,state="unintroduced",ordinary_limit=0,study_only=True) as case:
        assert priority.refresh_policy()
        request=body(case,"rollback-history")
        preview=client.post(PREFIX+"/preview",json=request)
        assert preview.status_code==200,preview.text
        assert client.post(PREFIX+"/start",json=dict(request,manifest_digest=preview.json()["manifest_digest"])).status_code==202
        before=case.readback()
        advance=durable_tasks.advance_task_slice_in_transaction
        def fail_after_real_owner(*args,**kwargs):
            raise RuntimeError("injected_history_checkpoint_failure")
        monkeypatch.setattr(durable_tasks,"advance_task_slice_in_transaction",fail_after_real_owner)
        with pytest.raises(RuntimeError,match="injected_history_checkpoint_failure"):
            client.post(PREFIX+"/run-ready",json=dict(maximum_units=1))
        assert case.readback()==before and status(client,"rollback-history")["processed_encounters"]==0
        with case.transaction() as database:
            assert database.execute_native("SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s",(case.card_id,)).fetchone()[0]==0
            database.execute_native("UPDATE background_tasks SET lease_expires_at=%s WHERE kind='game_feedback_history'",
                ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
        monkeypatch.setattr(durable_tasks,"advance_task_slice_in_transaction",advance)
        root._operations=None
        resumed=client.post(PREFIX+"/resume",json=dict(maximum_units=4))
        assert resumed.status_code==200,resumed.text
        saved=status(client,"rollback-history")
        assert saved["state"]=="complete" and saved["processed_encounters"]==2
        assert all(item["detected_at"]==NOW.isoformat() for item in saved["receipts"])


@pytest.mark.parametrize("blocked",["unknown","foreground"])
def test_actual_root_denies_unknown_or_foreground_without_claim(root_history,owned_postgres,monkeypatch,blocked):
    client, root, priority=root_history
    from app.services.activity_gate import activity_gate
    with retained_history_case(owned_postgres,state="unintroduced",ordinary_limit=0,study_only=True) as case:
        assert priority.refresh_policy()
        before=case.readback()
        if blocked=="unknown":
            monkeypatch.setattr(priority,"control_snapshot",lambda:dict(available=False,active=True,study_only_mode=None))
            response=client.post(PREFIX+"/preview",json=body(case))
        else:
            with activity_gate.foreground():
                response=client.post(PREFIX+"/preview",json=body(case))
        assert response.status_code==503,response.text
        assert case.readback()==before
