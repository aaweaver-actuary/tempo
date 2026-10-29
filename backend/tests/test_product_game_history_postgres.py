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
    # This native harness supplies a borrowed writer transport without running
    # API lifespan. Packaged tests separately prove reader HTTP rejects writes
    # and that these same operations run only in the established worker profile.
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", "postgresql://tempo_writer@owned-fixture/unused")
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


@contextmanager
def broad_history_case(transaction, *, conflict=False):
    """65 opening routes, but only one later exact physical decision.

    The early root stays over budget; no route or physical alternative is
    discarded to make the later complete authority capture fit.
    """
    import chess
    import hashlib
    with retained_history_case(transaction,state="unintroduced",ordinary_limit=0,study_only=True) as case:
        root_card = case.card_id+"-root"
        with transaction() as database:
            source = database.execute_native("SELECT start_fen FROM imported_games WHERE id=%s",
                (case.game_ids[0],)).fetchone()[0]
            prefix = ["e2e4","e7e5","g1f3","b8c6"]
            board = chess.Board(source)
            for move in prefix: board.push_uci(move)
            target_fen = board.fen(en_passant="legal")
            position = " ".join(target_fen.split()[:4])
            database.execute_native("UPDATE repertoire_lines SET moves_json=%s WHERE repertoire_id=%s",
                (json.dumps(prefix+["f1b5"]),case.repertoire_id))
            database.execute_native("UPDATE cards SET start_fen=%s,moves_json='[\"f1b5\"]' WHERE id=%s",
                (target_fen,case.card_id))
            database.execute_native("""UPDATE opening_graph_steps SET starting_fen=%s,moves_json='["f1b5"]',
                decision_fen_key=%s,decision_fen_keys_json=%s WHERE repertoire_id=%s""",
                (target_fen,position,json.dumps([position]),case.repertoire_id))
            for index, game_id in enumerate(case.game_ids):
                moves = prefix+["f1c4" if index==0 else "f1b5"]
                database.execute_native("UPDATE imported_games SET moves_json=%s,content_hash=%s WHERE id=%s",
                    (json.dumps(moves),hashlib.sha256(json.dumps(moves).encode()).hexdigest(),game_id))
            for index in range(64):
                route = case.repertoire_id+"-broad-"+str(index)
                database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,
                    start_fen,moves_json,created_at) VALUES(%s,%s,'Broad opening','white',%s,%s,%s)""",
                    (route,case.repertoire_id,source,json.dumps(["e2e4","e7e6"]),NOW.isoformat()))
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                revision,content_type,trained_color) VALUES(%s,%s,'prefix',%s,'["e2e4"]','new',%s,1,'opening','white')""",
                (root_card,case.repertoire_id,source,NOW.date().isoformat()))
            database.execute_native("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)",
                (case.repertoire_id,root_card))
            root_key = " ".join(source.split()[:4])
            database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,
                decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,
                first_decision_index,last_decision_index,decision_fen_keys_json)
                VALUES(%s,1,%s,0,%s,%s,%s,'["e2e4"]','white','prefix',0,0,%s)""",
                (case.repertoire_id,case.repertoire_id+"-broad-0",root_card,root_key,source,json.dumps([root_key])))
            if conflict:
                # No graph binding is needed for a relevant authored competing
                # move to remain part of the unchanged authority proof.
                database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,
                    start_fen,moves_json,created_at) VALUES(%s,%s,'Conflicting later move','white',%s,%s,%s)""",
                    (case.repertoire_id+"-conflict",case.repertoire_id,source,
                     json.dumps(prefix+["f1c4"]),NOW.isoformat()))
        try:
            yield case
        finally:
            with transaction() as database:
                database.execute_native("DELETE FROM cards WHERE id=%s",(root_card,))


def test_broad_root_overflow_resumes_later_ply_into_actual_study_once(root_history,owned_postgres):
    client,root,priority = root_history
    with broad_history_case(owned_postgres) as case:
        assert priority.refresh_policy()
        first = body(case,"broad-first",maximum_plies=4,maximum_positions=8)
        preview = client.post(PREFIX+"/preview",json=first)
        assert preview.status_code==200,preview.text
        early = preview.json()["preview"]
        assert (early["compared_games"],early["positions_compared"],early["remaining_positions"]) == (2,8,2)
        assert early["next_start_ply"]==4 and early["unresolved_positions"]==4
        assert any(item["reason"]=="legacy_capture_candidate_budget" for item in early["diagnostics"])
        assert early["matches"]==early["eligible_lapses"]==early["unintroduced_encounters"]==0
        request=body(case,"broad-later",start_ply=early["next_start_ply"],maximum_plies=4,maximum_positions=8)
        before=case.readback()
        preview=client.post(PREFIX+"/preview",json=request)
        assert preview.status_code==200,preview.text
        later=preview.json()["preview"]
        assert (later["compared_games"],later["positions_compared"],later["remaining_positions"]) == (2,2,0)
        assert (later["matches"],later["differing_responses"],later["unintroduced_encounters"]) == (1,1,2)
        assert later["next_start_ply"] is None and later["unresolved_positions"]==0
        assert case.readback()==before
        assert client.post(PREFIX+"/start",json=dict(request,manifest_digest=preview.json()["manifest_digest"])).status_code==202
        response=client.post(PREFIX+"/run-ready",json=dict(maximum_units=4))
        assert response.status_code==200,response.text
        saved=status(client,"broad-later")
        assert saved["state"]=="complete" and saved["processed_encounters"]==2
        assert [item["disposition"] for item in saved["receipts"]]==["first_introduction","frequency_only"]
        assert all(item["key"]["occurrence_ply"]==5 for item in saved["receipts"])
        after=case.readback()
        assert client.post(PREFIX+"/resume",json=dict(maximum_units=4)).json()["processed_units"]==0
        assert case.readback()==after
        with owned_postgres() as database:
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s",(case.card_id,)).fetchone()[0]==0
            assert tuple(database.execute_native("SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1").fetchone())==(0,1)
            assert database.execute_native("SELECT COUNT(*) FROM daily_queue WHERE card_id=%s",(case.card_id,)).fetchone()[0]==1


def test_later_ply_capture_keeps_authored_conflict_and_produces_no_effect(root_history,owned_postgres):
    client,root,priority=root_history
    with broad_history_case(owned_postgres,conflict=True) as case:
        assert priority.refresh_policy()
        before=case.readback()
        response=client.post(PREFIX+"/preview",json=body(case,"conflict-later",start_ply=4,maximum_plies=4))
        assert response.status_code==200,response.text
        summary=response.json()["preview"]
        assert summary["compared_games"]==2 and summary["positions_compared"]==2
        assert summary["unresolved_positions"]==summary["unknown_authority_or_mapping_games"]==2
        assert summary["unintroduced_encounters"]==summary["matches"]==summary["eligible_lapses"]==0
        plan=next(plan for (run,_),plan in root._operations._plans.items() if run=="conflict-later")
        assert not plan.encounters and case.readback()==before


def test_equivalent_route_history_persists_one_effect_and_receipt_under_recomparison(root_history,owned_postgres):
    """62 complete route bindings to one exact card remain one logical effect."""
    client,root,priority=root_history
    with retained_history_case(owned_postgres,state="unintroduced",ordinary_limit=0,study_only=True) as case:
        with owned_postgres() as database:
            for index in range(61):
                alias=case.repertoire_id+f"-alias-{index:02d}"
                database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,
                    start_fen,moves_json,created_at) SELECT %s,repertoire_id,name,trained_color,
                    start_fen,moves_json,created_at FROM repertoire_lines WHERE id=%s""",
                    (alias,case.repertoire_id+"-route"))
                database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,
                    decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,
                    first_decision_index,last_decision_index,decision_fen_keys_json)
                    SELECT repertoire_id,generation,%s,decision_index,card_id,decision_fen_key,starting_fen,
                    moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json
                    FROM opening_graph_steps WHERE repertoire_id=%s AND line_id=%s""",
                    (alias,case.repertoire_id,case.repertoire_id+"-route"))
        assert priority.refresh_policy()
        request=body(case,"equivalent-route-history",maximum_games=1,start_ply=0,maximum_plies=1)
        response=client.post(PREFIX+"/preview",json=request)
        assert response.status_code==200,response.text
        preview=response.json()
        assert preview["preview"]["unintroduced_encounters"]==1
        plan=next(value for (run,_),value in root._operations._plans.items() if run==request["run_id"])
        command,=plan.encounters
        capture=case.capture(command.key.game_id)
        from app.product_composition.encounters import prepare_captured_encounters
        prepared=prepare_captured_encounters(capture,detected_at=plan.detected_at)
        assert len(capture["tables"]["repertoire_lines"])==62
        assert len(capture["graphs"])==len(prepared.snapshot.decision_presentations)==62
        assert prepared.encounters==(command,)
        assert client.post(PREFIX+"/start",json=dict(request,manifest_digest=preview["manifest_digest"])).status_code==202
        run=client.post(PREFIX+"/run-ready",json=dict(maximum_units=2))
        assert run.status_code==200,run.text
        first=status(client,request["run_id"])
        assert first["state"]=="complete" and first["processed_encounters"]==1
        receipt,=first["receipts"]
        assert receipt["disposition"]=="first_introduction" and receipt["admission_status"]=="available"
        original=case.readback()
        # Recomparison has a new run, but still the same genuine historical key.
        repeated=dict(request,run_id="equivalent-route-recomparison")
        root._operations=None
        preview=client.post(PREFIX+"/preview",json=repeated)
        assert preview.status_code==200,preview.text
        assert client.post(PREFIX+"/start",json=dict(repeated,
            manifest_digest=preview.json()["manifest_digest"])).status_code==202
        assert client.post(PREFIX+"/resume",json=dict(maximum_units=2)).status_code==200
        second=status(client,repeated["run_id"])
        assert second["processed_encounters"]==1 and second["state"]=="complete"
        assert second["receipts"][0]["owner_receipt_id"]==receipt["owner_receipt_id"]
        assert case.readback()==original
        with owned_postgres() as database:
            assert database.execute_native("SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s",
                (case.card_id,)).fetchone()[0]==1
            assert database.execute_native("SELECT COUNT(*) FROM daily_queue WHERE card_id=%s",
                (case.card_id,)).fetchone()[0]==1
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s",
                (case.card_id,)).fetchone()[0]==0
            assert tuple(database.execute_native("SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1").fetchone())==(0,1)
