"""Versioned, pure readiness projection; scores never participate in scheduling."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import json
import math
from typing import Literal

import chess
from fsrs import Card

from .opening_segmentation import decision_identity, presentation_occurrences
from .scheduler import _scheduler

READINESS_VERSION = 1
ReadinessBasis = Literal["fsrs", "conservative_failure", "unknown"]
ReadinessAvailability = Literal["available", "coarse", "unknown", "unavailable"]
ReadinessConfidence = Literal["model_based", "limited", "coarse", "none"]


@dataclass(frozen=True)
class PresentationIdentity:
    card_id: str
    card_revision: int
    repertoire_id: str
    trained_color: Literal["white", "black"]


@dataclass(frozen=True)
class StudyReviewEvidence:
    review_id: int
    card_id: str
    reviewed_at: datetime
    outcome: Literal["correct", "again"]
    card_revision: int | None = None
    guided: bool = False
    invalidated: bool = False
    source_kind: str = "study"
    attempt_id: str | None = None


@dataclass(frozen=True)
class DecisionObservationEvidence:
    attempt_id: str
    presentation: PresentationIdentity
    decision_index: int
    decision_id: str
    expected_uci: str
    observed_at: datetime
    response_at: datetime | None = None
    first_response_uci: str | None = None
    assistance_before_response: tuple[str, ...] = ()
    manual_failure: bool = False
    disposition: str | None = None
    corrected: bool = False


@dataclass(frozen=True)
class GameDecisionEvidence:
    event_id: str
    card_id: str
    repertoire_id: str
    trained_color: Literal["white", "black"]
    fen_key: str
    expected_uci: str
    actual_uci: str
    outcome: Literal["success", "miss"]
    played_at: datetime
    eligible: bool


@dataclass(frozen=True)
class InheritedScheduleEvidence:
    source_card_id: str
    created_at: datetime


@dataclass(frozen=True)
class ReadinessSnapshot:
    presentation: PresentationIdentity
    start_fen: str
    moves: tuple[str, ...]
    fsrs_card_json: str | None = None
    state: str = "new"
    introduced_at: date | None = None
    due_date: date | None = None
    reinforcement_pending: bool = False
    reviews: tuple[StudyReviewEvidence, ...] = ()
    observations: tuple[DecisionObservationEvidence, ...] = ()
    game_events: tuple[GameDecisionEvidence, ...] = ()
    inherited_schedule: InheritedScheduleEvidence | None = None


@dataclass(frozen=True)
class ReadinessEstimate:
    version: int
    presentation: PresentationIdentity
    decision_index: int | None
    decision_id: str | None
    readiness_score: float | None
    fsrs_retrievability: float | None
    basis: ReadinessBasis
    availability: ReadinessAvailability
    confidence: ReadinessConfidence
    evidence_scope: Literal["card", "decision", "inherited_card"]
    evidence_group_id: str
    evidence_sources: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    as_of: datetime
    latest_evidence_at: datetime | None
    memory_reference_at: datetime | None
    reasons: tuple[str, ...]
    card_estimate: ReadinessEstimate | None = None
    latest_observation_outcome: Literal["clean", "failed", "unproven"] | None = None


@dataclass(frozen=True)
class _RecallFact:
    at: datetime
    source: str
    reference: str
    decision_indexes: tuple[int, ...]
    clean: bool = False
    failed: bool = False
    whole_card: bool = False
    current_review: bool = False


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("Readiness instants require an explicit timezone")
    return moment.astimezone(timezone.utc)


def _fsrs_memory_estimate(snapshot: ReadinessSnapshot, as_of: datetime) -> tuple[float | None, datetime | None, str | None]:
    if not snapshot.fsrs_card_json:
        return None, None, "missing_scheduler_state"
    try:
        serialized = json.loads(snapshot.fsrs_card_json)
        # Card's constructor supplies wall-clock defaults for absent identity/due.
        if not isinstance(serialized, dict) or not serialized.get("card_id") or not serialized.get("due"):
            raise ValueError("Incomplete scheduler state")
        memory_card = Card.from_json(snapshot.fsrs_card_json)
        reference = _utc(memory_card.last_review) if memory_card.last_review else None
        _utc(memory_card.due)
        if reference is None:
            return None, None, "unreviewed_scheduler_state"
        if reference > as_of:
            return None, reference, "scheduler_state_after_as_of"
        if (memory_card.stability is None or not math.isfinite(memory_card.stability)
                or memory_card.stability <= 0 or memory_card.difficulty is None
                or not math.isfinite(memory_card.difficulty) or not 1 <= memory_card.difficulty <= 10):
            raise ValueError("Invalid scheduler memory parameters")
        probability = _scheduler().get_card_retrievability(memory_card, as_of)
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid retrievability")
        return probability, reference, None
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None, None, "invalid_scheduler_state"


def _validated_recall_facts(snapshot: ReadinessSnapshot, occurrences: tuple[dict, ...], as_of: datetime) -> tuple[list[_RecallFact], set[str]]:
    facts: list[_RecallFact] = []
    reasons: set[str] = set()
    invalidated_attempts = {review.attempt_id for review in snapshot.reviews
                            if review.invalidated and review.attempt_id is not None}
    assisted_attempts: set[str] = set()
    for observation in snapshot.observations:
        if observation.attempt_id in invalidated_attempts:
            continue
        if observation.presentation != snapshot.presentation:
            reasons.add("excluded_observation_identity")
            continue
        if not 0 <= observation.decision_index < len(occurrences):
            reasons.add("invalid_observation")
            continue
        occurrence = occurrences[observation.decision_index]
        if (observation.decision_id != occurrence["decision_id"]
                or observation.expected_uci != occurrence["expected_uci"]):
            reasons.add("excluded_observation_identity")
            continue
        try:
            observed_at = _utc(observation.observed_at)
            response_at = _utc(observation.response_at) if observation.response_at else None
            if observed_at > as_of or (response_at is not None and response_at > as_of):
                continue
            if response_at is not None and response_at < observed_at:
                raise ValueError("Response precedes observation")
            responded = observation.first_response_uci is not None or observation.manual_failure
            if responded and response_at is None:
                raise ValueError("Response has no recorded instant")
        except (ValueError, TypeError, AttributeError):
            reasons.add("invalid_observation")
            continue
        clean = (observation.first_response_uci == occurrence["expected_uci"]
                 and not observation.assistance_before_response and not observation.manual_failure
                 and not observation.corrected and observation.disposition not in {"illegal", "unverified"})
        failed = (observation.manual_failure or (observation.first_response_uci is not None
                  and observation.first_response_uci != occurrence["expected_uci"]
                  and observation.disposition != "unverified"))
        if responded and not clean:
            assisted_attempts.add(observation.attempt_id)
        facts.append(_RecallFact(response_at or observed_at, "decision_observation",
                     f"observation:{observation.attempt_id}:{observation.decision_index}",
                     (observation.decision_index,), clean, failed))

    for review in snapshot.reviews:
        if review.invalidated or review.source_kind != "study" or review.card_id != snapshot.presentation.card_id:
            continue
        if review.card_revision not in (None, snapshot.presentation.card_revision):
            reasons.add("excluded_review_revision")
            continue
        try:
            reviewed_at = _utc(review.reviewed_at)
            if review.outcome not in {"correct", "again"}:
                raise ValueError("Unknown review outcome")
        except (ValueError, TypeError, AttributeError):
            reasons.add("invalid_review")
            continue
        if reviewed_at > as_of:
            reasons.add("scheduler_state_after_as_of")
            continue
        current_review = review.card_revision == snapshot.presentation.card_revision
        clean = review.outcome == "correct" and not review.guided and review.attempt_id not in assisted_attempts
        facts.append(_RecallFact(reviewed_at, "study_review", f"review:{review.review_id}",
                     tuple(range(len(occurrences))) if current_review else (), clean,
                     review.outcome == "again" or review.guided, True, current_review))

    for event in snapshot.game_events:
        if not event.eligible:
            continue
        if (event.card_id != snapshot.presentation.card_id or event.repertoire_id != snapshot.presentation.repertoire_id
                or event.trained_color != snapshot.presentation.trained_color):
            reasons.add("excluded_game_identity")
            continue
        try:
            played_at = _utc(event.played_at)
            if played_at > as_of:
                continue
            identity = decision_identity(event.repertoire_id, event.fen_key, event.trained_color, event.expected_uci)
            indexes = tuple(item["decision_index"] for item in occurrences if item["decision_id"] == identity)
            if not indexes or event.outcome not in {"success", "miss"}:
                raise ValueError("Game event does not identify a current decision")
            if (event.actual_uci == event.expected_uci) != (event.outcome == "success"):
                raise ValueError("Game outcome disagrees with its move")
        except (ValueError, TypeError, AttributeError):
            reasons.add("invalid_game_evidence")
            continue
        facts.append(_RecallFact(played_at, "real_game", f"game_event:{event.event_id}", indexes,
                                failed=event.outcome == "miss"))
    return facts, reasons


def _has_unresolved_failure(facts: list[_RecallFact], *, decision_index: int | None = None) -> bool:
    relevant = [fact for fact in facts if decision_index is None or decision_index in fact.decision_indexes]
    clean_times = [fact.at for fact in relevant if fact.clean and
                   (fact.whole_card if decision_index is None else True)]
    latest_clean_study_at = max(clean_times, default=None)
    return any(fact.failed and (decision_index is None or not fact.whole_card or len(fact.decision_indexes) == 1)
               and (latest_clean_study_at is None or fact.at >= latest_clean_study_at) for fact in relevant)


def decision_readiness(snapshot: ReadinessSnapshot, as_of: datetime, *, decision_index: int | None = None) -> ReadinessEstimate:
    """Project one supplied presentation; unknown is not zero, and prefixes stay correlated.

    Callers supply bounded, current evidence snapshots. No database, wall clock,
    scheduler replay, calibration, or queue changes occur here. A decision's raw
    retrievability is available only for a directly reviewed one-decision card;
    all other card-model context lives in ``card_estimate``.
    """
    as_of = _utc(as_of)
    memory_probability, memory_reference, memory_reason = _fsrs_memory_estimate(snapshot, as_of)
    try:
        if not snapshot.presentation.card_id or not snapshot.presentation.repertoire_id or snapshot.presentation.card_revision < 1:
            raise ValueError("Missing presentation identity")
        if not chess.Board(snapshot.start_fen).is_valid():
            raise ValueError("Invalid opening position")
        occurrences = presentation_occurrences(snapshot.presentation.repertoire_id, {
            "id": snapshot.presentation.card_id, "revision": snapshot.presentation.card_revision,
            "trained_color": snapshot.presentation.trained_color, "start_fen": snapshot.start_fen,
            "moves_json": json.dumps(snapshot.moves),
        })
        if decision_index is not None and (isinstance(decision_index, bool) or not isinstance(decision_index, int)
                                          or not 0 <= decision_index < len(occurrences)):
            raise ValueError("Decision index is outside its presentation")
    except (ValueError, TypeError, KeyError, IndexError):
        return ReadinessEstimate(READINESS_VERSION, snapshot.presentation, decision_index, None,
                                 None, None, "unknown", "unavailable", "none", "card",
                                 f"fsrs-card:{snapshot.presentation.card_id}", (), (), as_of, None,
                                 None, ("invalid_presentation_or_decision",))

    facts, reasons = _validated_recall_facts(snapshot, occurrences, as_of)
    if memory_reason:
        reasons.add(memory_reason)
    has_valid_study_review = any(fact.whole_card for fact in facts)
    inherited_memory_source = snapshot.inherited_schedule if not has_valid_study_review else None
    inheritance_created_at = None
    if snapshot.inherited_schedule:
        try:
            inheritance_created_at = _utc(snapshot.inherited_schedule.created_at)
            if inheritance_created_at > as_of:
                reasons.add("scheduler_state_after_as_of")
            if not snapshot.inherited_schedule.source_card_id or snapshot.inherited_schedule.source_card_id == snapshot.presentation.card_id:
                raise ValueError("Invalid inherited source")
        except (ValueError, TypeError, AttributeError):
            inherited_memory_source = None
            reasons.add("invalid_inherited_state")
    if "scheduler_state_after_as_of" in reasons or "invalid_inherited_state" in reasons:
        memory_probability = None
    if not has_valid_study_review and inherited_memory_source is None:
        reasons.add("never_studied" if snapshot.fsrs_card_json is None else "unverified_memory_provenance")
        memory_probability = None
    memory_evidence_group_id = f"fsrs-card:{inherited_memory_source.source_card_id if inherited_memory_source else snapshot.presentation.card_id}"
    failed = _has_unresolved_failure(facts)
    score = 0.0 if failed else memory_probability
    basis: ReadinessBasis = "conservative_failure" if failed else "fsrs" if score is not None else "unknown"
    availability: ReadinessAvailability = "available" if score is not None else "unknown" if "never_studied" in reasons else "unavailable"
    confidence: ReadinessConfidence = "limited" if (failed or snapshot.state != "mature" or snapshot.reinforcement_pending
                                           or not any(fact.current_review for fact in facts)) else "model_based"
    scope: Literal["card", "decision", "inherited_card"] = "card"
    if inherited_memory_source:
        scope, confidence = "inherited_card", "coarse"
        if score is not None:
            availability = "coarse"
        reasons.add("inherited_scheduler_seed")
    if snapshot.reinforcement_pending:
        reasons.add("reinforcement_pending")
    if failed:
        reasons.add("unresolved_failure_requires_clean_study")
    if score is None:
        confidence = "none"
    evidence_source_names = {fact.source for fact in facts}
    evidence_references = {fact.reference for fact in facts}
    evidence_times = [fact.at for fact in facts]
    if memory_probability is not None:
        evidence_source_names.add("fsrs")
    if inherited_memory_source and inheritance_created_at is not None and inheritance_created_at <= as_of:
        evidence_source_names.add("inherited_schedule")
        evidence_references.add(f"seed:{inherited_memory_source.source_card_id}")
        evidence_times.append(inheritance_created_at)
    card_estimate = ReadinessEstimate(READINESS_VERSION, snapshot.presentation, None, None, score,
        memory_probability, basis, availability, confidence, scope, memory_evidence_group_id, tuple(sorted(evidence_source_names)),
        tuple(sorted(evidence_references)), as_of, max(evidence_times, default=None), memory_reference, tuple(sorted(reasons)))
    if decision_index is None:
        return card_estimate

    decision_facts = [fact for fact in facts if decision_index in fact.decision_indexes]
    latest_observation = max((fact for fact in decision_facts if fact.source == "decision_observation"),
                             key=lambda fact: (fact.at, fact.failed, fact.reference), default=None)
    independently_reviewed_decision_card = len(occurrences) == 1 and any(fact.current_review for fact in facts)
    failed = _has_unresolved_failure(facts, decision_index=decision_index)
    score = 0.0 if failed else memory_probability if independently_reviewed_decision_card else None
    decision_reasons = set(card_estimate.reasons)
    # Aggregate failure elsewhere must not masquerade as this move's failure.
    if not failed:
        decision_reasons.discard("unresolved_failure_requires_clean_study")
    else:
        decision_reasons.add("unresolved_failure_requires_clean_study")
    if not independently_reviewed_decision_card:
        decision_reasons.add("no_independent_decision_memory")
    return replace(card_estimate, decision_index=decision_index,
        decision_id=occurrences[decision_index]["decision_id"], readiness_score=score,
        fsrs_retrievability=card_estimate.fsrs_retrievability if independently_reviewed_decision_card else None,
        basis="conservative_failure" if failed else "fsrs" if score is not None else "unknown",
        availability="available" if score is not None else "unavailable" if card_estimate.availability == "unavailable" else "unknown", evidence_scope="decision",
        confidence="limited" if failed else card_estimate.confidence if score is not None else "none",
        evidence_sources=tuple(sorted({fact.source for fact in decision_facts} | ({"fsrs"} if independently_reviewed_decision_card and memory_probability is not None else set()))),
        evidence_refs=tuple(sorted({fact.reference for fact in decision_facts})),
        latest_evidence_at=max((fact.at for fact in decision_facts), default=None),
        latest_observation_outcome=(None if latest_observation is None else "failed" if latest_observation.failed
                                    else "clean" if latest_observation.clean else "unproven"),
        reasons=tuple(sorted(decision_reasons)), card_estimate=card_estimate)
