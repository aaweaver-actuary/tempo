"""Retained-history composition through real source, Study and receipt owners."""
import pytest
from datetime import date

from app.product_composition.retained_game import retained_canonical_game
from app.repertoire import project_capture
from product_encounter_fixtures import retained_history_case
from test_product_feedback_integration import _issue, _ordinary_review
from test_product_game_lapse_postgres import owned_postgres


@pytest.fixture(autouse=True)
def normal_study_clock(monkeypatch):
    from app import queue_commands
    from app.services import learning_review_adapter
    from test_product_game_lapse import NOW
    class StudyDate(date):
        @classmethod
        def today(cls):
            return NOW.date()
    monkeypatch.setattr(queue_commands, "date", StudyDate)
    monkeypatch.setattr(learning_review_adapter, "date", StudyDate)


@pytest.mark.parametrize("state", ["unintroduced", "introduced", "later_learning"])
@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_retained_history_fixture_has_exact_current_mapping_and_protected_attempt(owned_postgres, monkeypatch, state, played):
    from tempo_legacy_database.adapters import postgres_store
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with retained_history_case(owned_postgres, state=state, played=played) as case:
        with case.transaction() as database:
            _issue(database, case.protected_id)
        before = case.readback()
        captured = case.capture(case.game_ids[0])
        snapshot = project_capture(captured, comparison_game=retained_canonical_game(captured))
        mapping, = snapshot.decision_presentations
        from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentOwnerDecisions
        command = case.command(captured)
        with case.transaction() as database:
            current, = CurrentOwnerDecisions(database, retained_owner_capture=captured,
                snapshot=snapshot).current_encounter(command)
        assert mapping.presentation_id == case.card_id and mapping.available
        assert mapping.selected_response_uci == "e2e4"
        assert current.introduction == ("unintroduced" if state == "unintroduced" else "introduced")
        assert mapping.introduced_before_game is (state == "introduced")
        from app.product_composition.encounters import importance_from_encounters
        importance, = importance_from_encounters(workspace_id=command.key.workspace_id, account="synthetic",
            current_approved_decisions=(current,), encounters=(command, command))
        assert importance.facts.personal_encounters == 1
        assert len(importance.differing_response_sources) == (0 if played == "e2e4" else 1)
        assert len(importance.source_encounters) == 1
        assert captured["game"]["id"] == case.game_ids[0]
        assert captured["game"]["moves_json"] == '["' + played + '"]'
        assert case.readback() == before
        assert len(before["origins"]) == 1 and before["queue"][0][1] == case.protected_id
        assert sum(row[1] == case.card_id for row in before["reviews"]) == (0 if state == "unintroduced" else 1)


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_unintroduced_encounter_retains_one_honest_pending_receipt_without_a_policy(
    owned_postgres, monkeypatch, played,
):
    from app.product_composition.encounters import receive_captured_encounter
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with retained_history_case(owned_postgres, state="unintroduced", played=played) as case:
        capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        before = case.readback()
        with case.transaction() as database:
            first = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                introductions=None, introduction_policy=None, clock=lambda: NOW)
        with case.transaction() as database:
            duplicate = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                introductions=None, introduction_policy=None, clock=lambda: NOW)
        assert first.observation_recorded and first.disposition == "first_introduction"
        assert first.admission_status == "pending" and first.available_by is None
        assert first.reason == "encounter_introduction_policy_pending"
        assert duplicate.owner_receipt_id == first.owner_receipt_id and duplicate.detected_at == first.detected_at
        assert case.readback() == before


def test_historical_priority_reaches_ordinary_study_without_rewinding_later_learning(owned_postgres, monkeypatch):
    from app.product_composition.encounters import receive_captured_encounter
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with retained_history_case(owned_postgres, state="later_learning") as case:
        with case.transaction() as database:
            protected = _issue(database, case.protected_id)
        before = case.readback(); capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        with case.transaction() as database:
            receipt = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                introductions=None, introduction_policy=None, clock=lambda: NOW)
        assert receipt.disposition == "review_priority" and receipt.admission_status == "available"
        after = case.readback()
        assert after["cards"] == before["cards"] and after["reviews"] == before["reviews"]
        assert tuple(row for row in after["origins"] if row[1] == case.protected_id) == before["origins"]
        assert len(tuple(row for row in after["origins"] if row[1] == case.card_id)) == 1
        with case.transaction() as database:
            _ordinary_review(database, case.protected_id, protected, "protected:"+case.card_id)
            target = _issue(database, case.card_id)
            assert _ordinary_review(database, case.card_id, target, "priority:"+case.card_id)["persisted"]
        with case.transaction() as database:
            replay = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                introductions=None, introduction_policy=None, clock=lambda: NOW)
            assert replay.status == "settled" and replay.admission_status == "settled"
            assert replay.owner_receipt_id == receipt.owner_receipt_id and replay.detected_at == receipt.detected_at
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",
                (case.card_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_retained_day_only_history_reaches_the_published_workflow_fact_boundary(owned_postgres, played):
    from app.games.feedback.comparison import compare_game
    from app.repertoire import build_index
    with retained_history_case(owned_postgres, state="later_learning", played=played) as case:
        capture = case.capture(case.game_ids[0]); game = retained_canonical_game(capture)
        snapshot = project_capture(capture, comparison_game=game)
        assert snapshot.decision_presentations[0].introduced_before_game is False
        findings = compare_game(game, provider="lichess", time_precision="second", snapshot=snapshot,
            reentry_lookup=build_index(snapshot))
        # #489's existing consumer admits factual commands from these covered
        # non-lapse findings. Date-only chronology must not erase old relevance.
        assert len(findings) == 1 and findings[0].kind == "known_unintroduced"
        assert not findings[0].lapse_eligible


def test_ordinary_admission_winning_capture_race_is_not_a_new_lapse(owned_postgres, monkeypatch):
    from app.product_composition.encounters import receive_captured_encounter
    from app.services.queue_application import seed_queue
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with retained_history_case(owned_postgres, state="unintroduced") as case:
        capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        with case.transaction() as database:
            _issue(database, case.protected_id)
            seed_queue(database, NOW.date().isoformat())
            introduced = database.execute_native("SELECT introduced_at FROM cards WHERE id=%s",
                (case.card_id,)).fetchone()[0]
            assert introduced is not None
        before = case.readback()
        with case.transaction() as database:
            first = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                introductions=None, introduction_policy=None, clock=lambda: NOW)
            duplicate = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                introductions=None, introduction_policy=None, clock=lambda: NOW)
            assert first.disposition == "review_priority" and duplicate.owner_receipt_id == first.owner_receipt_id
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
        after = case.readback()
        assert after["cards"] == before["cards"] and after["reviews"] == before["reviews"]


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_targeted_zero_allowance_study_only_encounter_reaches_first_real_study_once(
    owned_postgres, monkeypatch, played,
):
    from threading import Event
    from app.product_composition.encounters import receive_admitted_encounter
    from app.services.queue_application import seed_queue
    from app.study.encounter_feedback import EncounterIntroductionPolicy
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    policy, stopped = EncounterIntroductionPolicy("encounter_exception"), Event()
    with retained_history_case(owned_postgres, state="unintroduced", played=played,
            ordinary_limit=0, study_only=True) as case:
        capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        with case.transaction() as database:
            protected = _issue(database, case.protected_id)
        before = case.readback()
        def receive(database):
            return receive_admitted_encounter(database, capture=capture, snapshot=snapshot,
                command=command, operation_stopped=stopped.is_set,
                introduction_policy=policy, clock=lambda: NOW)
        with case.transaction() as database:
            first = receive(database)
            assert first.disposition == "first_introduction" and first.admission_status == "available"
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
            assert tuple(database.execute_native("SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1").fetchone()) == (0, 1)
            assert database.execute_native("SELECT introduced_at FROM cards WHERE id=%s", (case.card_id,)).fetchone()[0] == NOW.date().isoformat()
            # The ordinary seed/refresh must keep the targeted introduction and
            # protect the already presented unrelated head under saved controls.
            seed_queue(database, NOW.date().isoformat())
        introduced = case.readback()
        assert introduced["reviews"] == before["reviews"]
        assert tuple(row for row in introduced["origins"] if row[1] == case.protected_id) == before["origins"]
        with case.transaction() as database:
            assert receive(database).owner_receipt_id == first.owner_receipt_id
            _ordinary_review(database, case.protected_id, protected, "protected-first:"+case.card_id)
            target = _issue(database, case.card_id)
            assert _ordinary_review(database, case.card_id, target, "first-real:"+case.card_id)["persisted"]
        learned = case.readback()
        with case.transaction() as database:
            replay = receive(database)
            assert replay.owner_receipt_id == first.owner_receipt_id and replay.admission_status == "settled"
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'", (case.card_id,)).fetchone()[0] == 0
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 1
        assert case.readback() == learned


def test_stopped_operation_defers_before_first_introduction_and_resumes_once(owned_postgres, monkeypatch):
    from threading import Event
    from app.product_composition.encounters import receive_admitted_encounter
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    from app.study.encounter_feedback import EncounterIntroductionPolicy
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    stopped = Event(); stopped.set()
    with retained_history_case(owned_postgres, state="unintroduced", ordinary_limit=0, study_only=True) as case:
        capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        policy = EncounterIntroductionPolicy("encounter_exception")
        before = case.readback()
        def receive(database):
            return receive_admitted_encounter(database, capture=capture, snapshot=snapshot,
                command=command, operation_stopped=stopped.is_set,
                introduction_policy=policy, clock=lambda: NOW)
        with pytest.raises(BackgroundAdmissionDeferred, match="operation is stopped"):
            with case.transaction() as database:
                receive(database)
        assert case.readback() == before
        with case.transaction() as database:
            assert database.execute_native("SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
        stopped.clear()
        with case.transaction() as database:
            first = receive(database)
            assert first.admission_status == "available"
            assert receive(database).owner_receipt_id == first.owner_receipt_id
            assert database.execute_native("SELECT COUNT(*) FROM daily_queue WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 1
