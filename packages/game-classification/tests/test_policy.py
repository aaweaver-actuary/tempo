from datetime import datetime, timezone

import pytest

from tempo_game_classification.policy import classify_response


def evidence(**changes):
    return dict(played_uci="d2d4", expected_uci=["e2e4"], learner_turn=True,
                mapping_count=1, available=True, introduced=True,
                introduced_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                occurred_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
                occurrence_is_exact=True, introduction_matches_content=True, **changes)


def classify(**changes):
    facts = evidence()
    facts.update(changes)
    return classify_response(**facts)


def test_one_known_introduced_miss():
    assert classify()[0] == "confirmed_miss"


def test_introduced_after_game_is_learning_need_not_lapse():
    assert classify(introduced_at=datetime(2026, 10, 3, tzinfo=timezone.utc))[0] == "known_unintroduced"


def test_unknown_date_precision_is_not_assumed_before_introduction():
    assert classify(occurrence_is_exact=False)[0] == "known_unavailable"


@pytest.mark.parametrize("changes,kind", [
    ({"introduced": False}, "known_unintroduced"),
    ({"introduced": None}, "known_unavailable"),
    ({"introduction_matches_content": False}, "known_unavailable"),
    ({"available": False}, "known_unavailable"),
    ({"mapping_count": 0}, "known_unavailable"),
    ({"mapping_count": 2}, "ambiguous"),
    ({"ambiguous": True}, "ambiguous"),
    ({"complete": False}, "known_unavailable"),
    ({"introduced_at": datetime(2026, 10, 2, tzinfo=timezone.utc)}, "known_unintroduced"),
    ({"occurred_at": datetime(2026, 10, 2)}, "known_unavailable"),
])
def test_unproved_evidence_cannot_become_a_lapse(changes, kind):
    assert classify(**changes)[0] == kind


def test_opponent_gap_and_missing_own_continuation_distinct():
    assert classify(expected_uci=[], learner_turn=False)[0] == "opponent_gap"
    assert classify(expected_uci=[])[0] == "missing_own_continuation"


def test_terminal_route_is_coverage_end():
    assert classify(expected_uci=[], terminal=True)[0] == "coverage_end"
    assert classify(terminal=True)[0] == "coverage_end"


def test_conflicting_own_moves_block_lapse_even_when_played_is_one_response():
    assert classify(expected_uci=["d2d4", "e2e4"])[0] == "conflict"


def test_correct_move_produces_no_lapse_even_without_introduction_proof():
    assert classify(played_uci="e2e4", introduced=None, available=False) is None


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_unintroduced_encounter_is_a_fact_for_correct_or_different_play(played):
    assert classify(played_uci=played, introduced=False, introduced_at=None,
                    introduction_matches_content=False) == ("known_unintroduced", "decision_not_introduced")


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_introduction_after_game_remains_a_non_lapse_encounter(played):
    assert classify(played_uci=played, introduced_at=datetime(2026, 10, 3, tzinfo=timezone.utc)) == (
        "known_unintroduced", "introduction_not_before_game")


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_day_only_negative_introduction_proof_preserves_factual_encounter(played):
    assert classify(played_uci=played, introduced=None, introduced_at=None,
                    introduced_before_game=False) == ("known_unintroduced", "introduction_not_before_game")


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
@pytest.mark.parametrize("proof", [None, True])
def test_unknown_day_only_introduction_cannot_become_a_lapse(played, proof):
    finding = classify(played_uci=played, introduced=None, introduced_at=None, introduced_before_game=proof)
    if played == "e2e4":
        assert finding is None
    else:
        assert finding == ("known_unavailable", "introduction_evidence_unavailable")


def test_correct_already_introduced_move_has_no_failure_finding():
    assert classify(played_uci="e2e4") is None


@pytest.mark.parametrize("changes", [{"mapping_count": 0}, {"mapping_count": 2}, {"available": False}])
def test_correct_unintroduced_play_cannot_invent_a_mapping(changes):
    assert classify(played_uci="e2e4", introduced=False, introduced_at=None, **changes) is None


@pytest.mark.parametrize("changes,kind", [
    ({"introduced_before_game": True}, "confirmed_miss"),
    ({"introduced_before_game": False}, "known_unintroduced"),
    ({}, "known_unavailable"),
    ({"introduced_before_game": True, "occurrence_is_exact": False}, "known_unavailable"),
    ({"introduced_before_game": True, "introduction_matches_content": False}, "known_unavailable"),
    ({"introduced_before_game": True, "introduced_at": datetime(2026, 10, 3, tzinfo=timezone.utc)}, "known_unavailable"),
])
def test_explicit_game_scoped_introduction_preserves_missing_timestamp(changes, kind):
    facts = {"introduced_at": None, **changes}
    assert classify(**facts)[0] == kind


def test_input_containers_unchanged():
    facts = evidence()
    before = facts["expected_uci"].copy()
    classify_response(**facts)
    assert facts["expected_uci"] == before
