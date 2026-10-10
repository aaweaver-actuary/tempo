"""Named regular-suite protection for read-only learner readiness, version 1."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json

import chess
from fsrs import Card
import pytest

from app.services.decision_readiness import (
    DecisionObservationEvidence, GameDecisionEvidence, InheritedScheduleEvidence,
    PresentationIdentity, ReadinessSnapshot, StudyReviewEvidence, decision_readiness,
)
from app.services.opening_segmentation import presentation_occurrences
from app.services.scheduler import _scheduler, schedule_review

REVIEW_TIME = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
PREFIX = ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5")


def snapshot(moves=("e2e4",), **changes):
    return ReadinessSnapshot(PresentationIdentity("card", 1, "rep", "white"),
                             chess.STARTING_FEN, moves, **changes)


def reviewed(outcome="correct", at=REVIEW_TIME, moves=("e2e4",), **changes):
    schedule = schedule_review(outcome, reviewed_at=at)
    return snapshot(moves, fsrs_card_json=schedule.fsrs_card_json, state="learning",
                    due_date=schedule.due_date, reinforcement_pending=schedule.reinforcement_pending,
                    reviews=(StudyReviewEvidence(1, "card", at, outcome, card_revision=1),), **changes)


def occurrences(current):
    return presentation_occurrences(current.presentation.repertoire_id, {
        "id": current.presentation.card_id, "revision": current.presentation.card_revision,
        "trained_color": current.presentation.trained_color, "start_fen": current.start_fen,
        "moves_json": json.dumps(current.moves),
    })


def observation(current, index=0, *, at=REVIEW_TIME, response=None, **changes):
    decision = occurrences(current)[index]
    return DecisionObservationEvidence("attempt", current.presentation, index,
        decision["decision_id"], decision["expected_uci"], at, response_at=at,
        first_response_uci=response or decision["expected_uci"], **changes)


def game_event(current, index=0, *, at=REVIEW_TIME, outcome="miss", **changes):
    decision = occurrences(current)[index]
    return GameDecisionEvidence("game:0", current.presentation.card_id,
        current.presentation.repertoire_id, current.presentation.trained_color,
        " ".join(decision["decision_fen"].split()[:4]), decision["expected_uci"],
        decision["expected_uci"] if outcome == "success" else "a2a3", outcome, at,
        eligible=True, **changes)


def test_decision_readiness_recent_failure_overrides_same_day_fsrs():
    failed = reviewed("again")
    estimate = decision_readiness(failed, REVIEW_TIME, decision_index=0)
    assert estimate.fsrs_retrievability == 1.0
    assert estimate.readiness_score == 0.0
    assert estimate.basis == "conservative_failure"
    assert estimate.confidence == "limited"
    assert estimate.card_estimate.readiness_score == 0.0
    assert estimate.evidence_sources == ("fsrs", "study_review")


def test_decision_readiness_clean_practice_restores_fsrs_estimate():
    failed = reviewed("again")
    recovered_at = REVIEW_TIME + timedelta(days=1)
    schedule = schedule_review("correct", fsrs_card_json=failed.fsrs_card_json,
                               reviewed_at=recovered_at)
    recovered = replace(failed, fsrs_card_json=schedule.fsrs_card_json,
        reinforcement_pending=schedule.reinforcement_pending,
        reviews=failed.reviews + (StudyReviewEvidence(2, "card", recovered_at, "correct", card_revision=1),))
    as_of = recovered_at + timedelta(days=2)
    estimate = decision_readiness(recovered, as_of, decision_index=0)
    assert estimate.basis == "fsrs"
    assert estimate.readiness_score == _scheduler().get_card_retrievability(Card.from_json(schedule.fsrs_card_json), as_of)
    assert estimate.readiness_score > decision_readiness(failed, as_of, decision_index=0).readiness_score
    success_model = decision_readiness(reviewed("correct"), REVIEW_TIME + timedelta(days=1))
    failure_model = decision_readiness(failed, REVIEW_TIME + timedelta(days=1))
    assert success_model.fsrs_retrievability > failure_model.fsrs_retrievability


def test_decision_readiness_overdue_decay_uses_explicit_as_of_without_due_penalty():
    current = replace(reviewed(), state="mature", reinforcement_pending=False)
    at_due = REVIEW_TIME + timedelta(days=1)
    overdue = REVIEW_TIME + timedelta(days=90)
    estimate = decision_readiness(current, overdue)
    assert 0 < estimate.readiness_score < decision_readiness(current, at_due).readiness_score
    assert estimate.readiness_score == _scheduler().get_card_retrievability(Card.from_json(current.fsrs_card_json), overdue)
    assert estimate.readiness_score < 0.92
    assert estimate.confidence == "model_based"
    assert estimate == decision_readiness(replace(current, due_date=overdue.date()), overdue)


@pytest.mark.parametrize("state", ["new", "locked", "learning", "mature"])
def test_decision_readiness_never_studied_is_unknown_despite_admission_or_maturity(state):
    current = snapshot(state=state, introduced_at=REVIEW_TIME.date())
    estimate = decision_readiness(current, REVIEW_TIME)
    assert estimate.readiness_score is None and estimate.fsrs_retrievability is None
    assert estimate.availability == "unknown" and estimate.confidence == "none"
    assert "never_studied" in estimate.reasons


def test_decision_readiness_new_clean_practice_has_limited_reinforcement_confidence():
    estimate = decision_readiness(reviewed(), REVIEW_TIME)
    assert estimate.readiness_score == estimate.fsrs_retrievability == 1
    assert estimate.confidence == "limited"
    assert "reinforcement_pending" in estimate.reasons


def test_decision_readiness_real_game_miss_requires_clean_study_recovery():
    current = reviewed(at=REVIEW_TIME - timedelta(days=7))
    miss = game_event(current)
    current = replace(current, game_events=(miss,))
    as_of = REVIEW_TIME + timedelta(days=1)
    before = decision_readiness(current, as_of, decision_index=0)
    assert before.readiness_score == 0 and before.fsrs_retrievability > 0
    assert "real_game" in before.evidence_sources
    success = replace(miss, event_id="game:1", outcome="success", actual_uci=miss.expected_uci,
                      played_at=REVIEW_TIME + timedelta(hours=1))
    assert decision_readiness(replace(current, game_events=(miss, success)), as_of).readiness_score == 0
    guided = StudyReviewEvidence(2, "card", REVIEW_TIME + timedelta(hours=2), "correct", card_revision=1, guided=True)
    assert decision_readiness(replace(current, reviews=current.reviews + (guided,)), as_of).readiness_score == 0
    clean = replace(guided, review_id=3, guided=False, reviewed_at=REVIEW_TIME + timedelta(hours=3))
    assert decision_readiness(replace(current, reviews=current.reviews + (clean,)), as_of, decision_index=0).basis == "fsrs"


def test_decision_readiness_real_game_miss_without_memory_is_observed_zero():
    current = snapshot()
    estimate = decision_readiness(replace(current, game_events=(game_event(current),)), REVIEW_TIME, decision_index=0)
    assert estimate.readiness_score == 0 and estimate.fsrs_retrievability is None
    assert estimate.basis == "conservative_failure"


def test_decision_readiness_prefix_evidence_is_not_independent():
    current = reviewed("again", moves=PREFIX)
    current = replace(current, observations=(
        observation(current, 0), observation(current, 1, response="a2a3")))
    predecessor, failed, unreached = [decision_readiness(current, REVIEW_TIME, decision_index=index) for index in range(3)]
    assert predecessor.readiness_score is None
    assert predecessor.latest_observation_outcome == "clean"
    assert failed.readiness_score == 0 and failed.basis == "conservative_failure"
    assert failed.latest_observation_outcome == "failed"
    assert unreached.readiness_score is None
    assert unreached.latest_observation_outcome is None
    assert predecessor.card_estimate.readiness_score == unreached.card_estimate.readiness_score == 0
    assert predecessor.evidence_group_id == failed.evidence_group_id == unreached.evidence_group_id
    assert all(estimate.fsrs_retrievability is None for estimate in (predecessor, failed, unreached))
    assert "unresolved_failure_requires_clean_study" not in predecessor.reasons


def test_decision_readiness_prefix_clean_aggregate_does_not_create_move_observations():
    current = reviewed(moves=PREFIX)
    estimate = decision_readiness(current, REVIEW_TIME, decision_index=1)
    assert estimate.readiness_score is None and estimate.basis == "unknown"
    assert estimate.card_estimate.readiness_score == 1
    assert "decision_observation" not in estimate.evidence_sources


def test_decision_readiness_partial_decision_recovery_does_not_clear_whole_card_failure():
    current = reviewed("again", moves=PREFIX)
    wrong = observation(current, 1, response="a2a3")
    clean = replace(observation(current, 1, at=REVIEW_TIME + timedelta(hours=1)), attempt_id="later")
    current = replace(current, observations=(wrong, clean))
    estimate = decision_readiness(current, REVIEW_TIME + timedelta(hours=2), decision_index=1)
    assert estimate.readiness_score is None and estimate.basis == "unknown"
    assert estimate.card_estimate.readiness_score == 0
    assert estimate.latest_evidence_at == clean.response_at


@pytest.mark.parametrize("change", [
    {"assistance_before_response": ("hint",)},
    {"assistance_before_response": ("revealed",)},
    {"first_response_uci": "a2a3", "corrected": True},
    {"corrected": True},
    {"first_response_uci": None, "manual_failure": True, "corrected": True},
    {"disposition": "unverified"},
])
def test_decision_readiness_assisted_or_corrected_response_does_not_clear_failure(change):
    current = snapshot(moves=PREFIX)
    wrong = observation(current, 1, response="a2a3")
    later = replace(observation(current, 1, at=REVIEW_TIME + timedelta(hours=1)), attempt_id="later", **change)
    estimate = decision_readiness(replace(current, observations=(wrong, later)), REVIEW_TIME + timedelta(hours=2), decision_index=1)
    assert estimate.readiness_score == 0


def test_decision_readiness_assisted_checkpoint_prevents_linked_aggregate_clean_recovery():
    current = reviewed("again")
    later = observation(current, at=REVIEW_TIME + timedelta(hours=1), assistance_before_response=("hint",))
    aggregate = StudyReviewEvidence(2, "card", REVIEW_TIME + timedelta(hours=2), "correct",
                                  card_revision=1, attempt_id=later.attempt_id)
    estimate = decision_readiness(replace(current, reviews=current.reviews + (aggregate,), observations=(later,)),
                                  REVIEW_TIME + timedelta(hours=3))
    assert estimate.readiness_score == 0


def test_decision_readiness_repeated_occurrences_keep_separate_observations():
    moves = ("g1f3", "g8f6", "f3g1", "f6g8", "g1f3")
    current = snapshot(moves=moves)
    first = observation(current, 0)
    repeated = observation(current, 2, response="a2a3")
    assert first.decision_id == repeated.decision_id
    current = replace(current, observations=(first, repeated))
    assert decision_readiness(current, REVIEW_TIME, decision_index=0).readiness_score is None
    assert decision_readiness(current, REVIEW_TIME, decision_index=2).readiness_score == 0


def test_decision_readiness_inherited_seed_is_coarse_until_current_card_practice():
    trained = reviewed()
    seed = InheritedScheduleEvidence("old-prefix", REVIEW_TIME)
    inherited = replace(trained, reviews=(), inherited_schedule=seed, state="mature")
    estimate = decision_readiness(inherited, REVIEW_TIME, decision_index=0)
    assert estimate.readiness_score is None
    assert estimate.card_estimate.availability == "coarse"
    assert estimate.card_estimate.evidence_scope == "inherited_card"
    assert estimate.evidence_group_id == "fsrs-card:old-prefix"
    direct = decision_readiness(replace(inherited, reviews=trained.reviews), REVIEW_TIME, decision_index=0)
    assert direct.readiness_score == 1 and direct.evidence_group_id == "fsrs-card:card"


def test_decision_readiness_legacy_unattributed_review_is_card_memory_only():
    trained = reviewed()
    current = replace(trained, reviews=(replace(trained.reviews[0], card_revision=None),))
    estimate = decision_readiness(current, REVIEW_TIME, decision_index=0)
    assert estimate.readiness_score is None
    assert estimate.card_estimate.readiness_score == 1


@pytest.mark.parametrize("field,value", [
    ("stability", 0), ("stability", -1), ("stability", float("inf")),
    ("difficulty", None), ("difficulty", float("nan")), ("difficulty", 11),
    ("last_review", "2026-10-01T12:00:00"), ("due", None), ("card_id", None), ("state", 99),
])
def test_decision_readiness_corrupt_scheduler_state_is_explicit(field, value):
    current = reviewed()
    serialized = json.loads(current.fsrs_card_json)
    serialized[field] = value
    estimate = decision_readiness(replace(current, fsrs_card_json=json.dumps(serialized)), REVIEW_TIME)
    assert estimate.readiness_score is None and estimate.fsrs_retrievability is None
    assert estimate.availability == "unavailable"
    assert "invalid_scheduler_state" in estimate.reasons


@pytest.mark.parametrize("serialized", [None, "not json", "[]", "{}"])
def test_decision_readiness_missing_state_retains_reliable_failure(serialized):
    estimate = decision_readiness(replace(reviewed("again"), fsrs_card_json=serialized), REVIEW_TIME)
    assert estimate.readiness_score == 0 and estimate.fsrs_retrievability is None
    assert estimate.basis == "conservative_failure"


def test_decision_readiness_rejects_historical_evaluation_of_newer_scheduler_state():
    estimate = decision_readiness(reviewed(), REVIEW_TIME - timedelta(seconds=1))
    assert estimate.readiness_score is None
    assert "scheduler_state_after_as_of" in estimate.reasons
    # Tempo can clamp the memory reference earlier than the actual review.
    later = reviewed(at=REVIEW_TIME + timedelta(days=1))
    serialized = json.loads(later.fsrs_card_json)
    serialized["last_review"] = REVIEW_TIME.isoformat()
    estimate = decision_readiness(replace(later, fsrs_card_json=json.dumps(serialized)), REVIEW_TIME)
    assert estimate.readiness_score is None and "scheduler_state_after_as_of" in estimate.reasons


def test_decision_readiness_clamped_memory_reference_and_actual_freshness_remain_distinct():
    current = reviewed(at=REVIEW_TIME + timedelta(days=3))
    serialized = json.loads(current.fsrs_card_json)
    serialized["last_review"] = REVIEW_TIME.isoformat()
    estimate = decision_readiness(replace(current, fsrs_card_json=json.dumps(serialized)), REVIEW_TIME + timedelta(days=4))
    assert estimate.memory_reference_at == REVIEW_TIME
    assert estimate.latest_evidence_at == REVIEW_TIME + timedelta(days=3)
    assert estimate.readiness_score == _scheduler().get_card_retrievability(Card.from_json(json.dumps(serialized)), estimate.as_of)


@pytest.mark.parametrize("change", [{"card_revision": 2}, {"repertoire_id": "other"},
                                    {"trained_color": "black"}, {"card_id": "other"}])
def test_decision_readiness_stale_observation_identity_cannot_lower_readiness(change):
    current = reviewed(moves=PREFIX)
    wrong = observation(current, 1, response="a2a3")
    wrong = replace(wrong, presentation=replace(wrong.presentation, **change))
    estimate = decision_readiness(replace(current, observations=(wrong,)), REVIEW_TIME, decision_index=1)
    assert estimate.readiness_score is None and estimate.card_estimate.readiness_score == 1
    assert "excluded_observation_identity" in estimate.reasons


def test_decision_readiness_invalidated_reviews_and_linked_observations_are_excluded():
    current = snapshot()
    wrong = observation(current, response="a2a3")
    invalid = StudyReviewEvidence(1, "card", REVIEW_TIME, "again", card_revision=1,
                                 invalidated=True, attempt_id=wrong.attempt_id)
    estimate = decision_readiness(replace(current, reviews=(invalid,), observations=(wrong,)), REVIEW_TIME)
    assert estimate.readiness_score is None and estimate.evidence_refs == ()


@pytest.mark.parametrize("change", [{"eligible": False}, {"repertoire_id": "other"},
                                    {"card_id": "other"}, {"expected_uci": "d2d4"}])
def test_decision_readiness_excluded_or_mismatched_game_evidence_is_not_used(change):
    current = reviewed()
    event = replace(game_event(current), **change)
    estimate = decision_readiness(replace(current, game_events=(event,)), REVIEW_TIME)
    assert estimate.readiness_score == 1 and "real_game" not in estimate.evidence_sources


def test_decision_readiness_timestamp_ties_do_not_clear_failure_and_offsets_normalize():
    current = reviewed()
    miss = game_event(current, at=REVIEW_TIME.astimezone(timezone(timedelta(hours=-4))))
    estimate = decision_readiness(replace(current, game_events=(miss,)), REVIEW_TIME)
    assert estimate.readiness_score == 0 and estimate.latest_evidence_at == REVIEW_TIME
    assert estimate == decision_readiness(replace(current, game_events=(miss,)), miss.played_at)


def test_decision_readiness_fixed_as_of_is_stable_read_only_and_order_independent():
    current = reviewed(moves=PREFIX)
    current = replace(current, observations=(observation(current, 0), observation(current, 1, response="a2a3")))
    saved = deepcopy(current)
    first = decision_readiness(current, REVIEW_TIME, decision_index=1)
    assert first == decision_readiness(current, REVIEW_TIME, decision_index=1)
    assert first == decision_readiness(replace(current, observations=tuple(reversed(current.observations))), REVIEW_TIME, decision_index=1)
    assert current == saved
    with pytest.raises(FrozenInstanceError):
        current.state = "mature"
    with pytest.raises(FrozenInstanceError):
        first.readiness_score = 1


@pytest.mark.parametrize("index", [-1, 1, True, "0"])
def test_decision_readiness_invalid_target_is_unavailable(index):
    estimate = decision_readiness(reviewed(), REVIEW_TIME, decision_index=index)
    assert estimate.availability == "unavailable" and estimate.readiness_score is None


def test_decision_readiness_requires_timezone_aware_as_of():
    with pytest.raises(ValueError, match="explicit timezone"):
        decision_readiness(snapshot(), REVIEW_TIME.replace(tzinfo=None))


def test_decision_readiness_uses_no_implicit_clock(monkeypatch):
    import fsrs.card
    import fsrs.scheduler
    import app.services.decision_readiness as adapter

    class ForbiddenClock(datetime):
        @classmethod
        def now(cls, *args, **kwargs):
            raise AssertionError("Readiness consulted the wall clock")

    current = reviewed()
    monkeypatch.setattr(fsrs.card, "datetime", ForbiddenClock)
    monkeypatch.setattr(fsrs.scheduler, "datetime", ForbiddenClock)
    monkeypatch.setattr(adapter, "datetime", ForbiddenClock)
    assert decision_readiness(current, REVIEW_TIME).readiness_score == 1


@pytest.mark.parametrize("changes", [
    {"start_fen": "not a fen"}, {"start_fen": "8/8/8/8/8/8/8/8 w - - 0 1"},
    {"moves": ()}, {"moves": ("e2e5",)}, {"moves": ("e2e4", "e7e5")},
    {"moves": PREFIX * 20},
])
def test_decision_readiness_invalid_presentation_cannot_inherit_valid_memory(changes):
    estimate = decision_readiness(replace(reviewed(), **changes), REVIEW_TIME)
    assert estimate.availability == "unavailable"
    assert estimate.readiness_score is None and estimate.fsrs_retrievability is None


def test_decision_readiness_black_decision_and_canonical_game_attribution():
    current = snapshot(moves=("e2e4", "e7e5"))
    current = replace(current, presentation=replace(current.presentation, trained_color="black"))
    miss = game_event(current)
    estimate = decision_readiness(replace(current, game_events=(miss,)), REVIEW_TIME, decision_index=0)
    assert estimate.readiness_score == 0 and estimate.decision_id == occurrences(current)[0]["decision_id"]


def test_decision_readiness_future_optional_observations_do_not_change_current_estimate():
    current = reviewed()
    later = REVIEW_TIME + timedelta(days=1)
    current = replace(current, observations=(observation(current, at=later, response="a2a3"),),
                      game_events=(game_event(current, at=later),))
    assert decision_readiness(current, REVIEW_TIME, decision_index=0).readiness_score == 1


def test_decision_readiness_seed_created_after_as_of_is_not_historical_memory():
    current = replace(reviewed(), reviews=(), inherited_schedule=InheritedScheduleEvidence("old", REVIEW_TIME + timedelta(days=1)))
    estimate = decision_readiness(current, REVIEW_TIME)
    assert estimate.readiness_score is None and "scheduler_state_after_as_of" in estimate.reasons


def test_decision_readiness_transposed_game_miss_matches_current_canonical_decision():
    current = reviewed(moves=("g1f3", "d7d5", "g2g3", "g8f6", "f1g2"))
    board = chess.Board()
    for move in ("g2g3", "d7d5", "g1f3", "g8f6"):
        board.push_uci(move)
    event = replace(game_event(current, 2), fen_key=" ".join(board.fen().split()[:4]), actual_uci="f1h3")
    estimate = decision_readiness(replace(current, game_events=(event,)), REVIEW_TIME, decision_index=2)
    assert estimate.readiness_score == 0 and estimate.evidence_refs == ("game_event:game:0", "review:1")
    assert decision_readiness(replace(current, game_events=(event,)), REVIEW_TIME, decision_index=0).readiness_score is None


def test_decision_readiness_historical_decision_availability_is_explicit():
    estimate = decision_readiness(reviewed(moves=PREFIX), REVIEW_TIME - timedelta(seconds=1), decision_index=1)
    assert estimate.availability == "unavailable" and estimate.readiness_score is None


@pytest.mark.parametrize("review_change", [{"source_kind": "game"}, {"card_revision": 2}, {"card_id": "other"}])
def test_decision_readiness_other_review_sources_do_not_establish_owned_memory(review_change):
    current = reviewed()
    current = replace(current, reviews=(replace(current.reviews[0], **review_change),))
    estimate = decision_readiness(current, REVIEW_TIME, decision_index=0)
    assert estimate.readiness_score is None and estimate.card_estimate.fsrs_retrievability is None
    assert "unverified_memory_provenance" in estimate.reasons


def test_decision_readiness_studied_card_without_scheduler_state_is_unavailable():
    estimate = decision_readiness(replace(reviewed(), fsrs_card_json=None), REVIEW_TIME, decision_index=0)
    assert estimate.readiness_score is None and estimate.availability == "unavailable"
    assert "missing_scheduler_state" in estimate.reasons


@pytest.mark.parametrize("field", ["card_id", "state", "stability", "difficulty"])
def test_decision_readiness_boolean_scheduler_parameters_are_unavailable(field):
    current = reviewed()
    serialized = json.loads(current.fsrs_card_json)
    serialized[field] = True
    estimate = decision_readiness(replace(current, fsrs_card_json=json.dumps(serialized)), REVIEW_TIME)
    assert estimate.readiness_score is None
    assert estimate.availability == "unavailable" and "invalid_scheduler_state" in estimate.reasons
