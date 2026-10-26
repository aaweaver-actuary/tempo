"""Next-release normal-Study composition; current cases qualify fixture facts.

No encounter DTO or effect is fabricated while #482/#486/#487 publish their
additive interfaces. The following tests are not encounter-effect acceptance.
"""
import pytest

from app.product_composition.retained_game import retained_canonical_game
from app.repertoire import project_capture
from product_encounter_fixtures import retained_history_case
from test_product_feedback_integration import _issue, _ordinary_review
from test_product_game_lapse_postgres import owned_postgres


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
        assert after["origins"] == before["origins"]
        with case.transaction() as database:
            _ordinary_review(database, case.protected_id, protected, "protected:"+case.card_id)
            target = _issue(database, case.card_id)
            assert _ordinary_review(database, case.card_id, target, "priority:"+case.card_id)["persisted"]
