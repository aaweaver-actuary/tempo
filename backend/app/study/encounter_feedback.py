"""Current Study priority for real encounters, independently of lapse authority.

The existing effect ledger, holds, settlement and card/day lock order are reused.
An encounter never calls the scheduler; introduction belongs to its existing
canonical owner on this same borrowed transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from app.contracts.product_v02.learning import GameEncounter, EncounterReceipt
from app.contracts.product_v02.repertoire import DecisionPresentation
from app.study.game_feedback import CardFacts, CurrentDecisionPort, Effect, GameFeedback, GameFeedbackStore, encoded, instant

if TYPE_CHECKING:
    from app.study.introductions.results import CommitResult


class EncounterAuthorityUnavailable(RuntimeError):
    """A failed nonblocking current-owner fence; not a database error/rollback."""


@dataclass(frozen=True)
class EncounterIntroductionPolicy:
    """Explicit owner input; targeted exceptions never change saved settings.

    Real-game first introductions may bypass ordinary caps and Study-only. Full
    pause and current owner safety/authority guards remain effective. The
    canonical admission capability implements these controls, not this receiver.
    """
    allowance: Literal["ordinary", "encounter_exception"]

    def __post_init__(self):
        if self.allowance not in {"ordinary", "encounter_exception"}:
            raise ValueError("explicit ordinary or targeted encounter exception required")


class EncounterIntroductions(Protocol):
    """#495's existing canonical writer; receiver and capability share one tx."""
    def admit_encounter(self, day: str, *, repertoire_id: str, decision_id: str,
                        target: DecisionPresentation) -> CommitResult: ...


class CurrentEncounterPort(CurrentDecisionPort, Protocol):
    def current_encounter(self, encounter: GameEncounter) -> tuple[DecisionPresentation, ...]: ...


class EncounterStore(GameFeedbackStore, Protocol):
    def encounter_card_facts(self, card_id: str, day: str) -> CardFacts | None: ...
    def promote_encounter(self, card_id: str, day: str) -> None: ...


def encounter_digest(command: GameEncounter) -> str:
    # Deliberately identical to the lapse semantic hash. Changing chronology,
    # comparison labels or delivery time cannot turn this encounter into Again.
    return hashlib.sha256(encoded(dict(source_digest=command.source.source_digest,
        position=command.decision.position_key, expected=(command.expected_uci,),
        played=command.played_uci, repertoire=command.decision.repertoire_id,
        occurred_at=command.occurred_at)).encode()).hexdigest()


class EncounterFeedback(GameFeedback):
    def __init__(self, store: EncounterStore, decisions: CurrentEncounterPort, *,
                 clock: Callable[[], datetime], introductions: EncounterIntroductions | None,
                 introduction_policy: EncounterIntroductionPolicy | None,
                 light_first_interval_days: int = 7):
        super().__init__(store, decisions, clock=clock, light_first_interval_days=light_first_interval_days)
        self.introductions, self.introduction_policy = introductions, introduction_policy

    def _encounter_receipt(self, effect, command, outcome):
        original = effect.encounter or command
        disposition = effect.disposition
        admission = effect.admission_status or "pending"
        deferred = effect.deferred_reason
        if effect.lapse is not None:
            disposition = "existing_lapse"
            admission = "settled" if effect.status == "settled" else "pending"
        elif effect.status == "settled" and admission != "not_requested":
            admission, deferred = "settled", None
        elif effect.status == "held":
            admission, deferred = (("not_requested", None) if disposition == "frequency_only"
                                   else ("pending", "authority_unavailable"))
        values = self._receipt(effect, outcome).model_dump(mode="json")
        if effect.lapse is not None and admission == "pending" and values["reason"] is None:
            values["reason"] = "existing_lapse_obligation"
        # This target is not a promise while admission is blocked or its control
        # reason is not yet supplied by the owner. Detection itself stays stored.
        if admission in {"pending", "not_requested"}:
            values["available_by"] = None
        return EncounterReceipt(**values, detected_at=(effect.lapse.detected_at if effect.lapse else original.detected_at),
            source_digest=(effect.lapse.finding.source_digest if effect.lapse else original.source.source_digest),
            observation=("missed" if effect.lapse else original.observation),
            observation_recorded=effect.observation_recorded or effect.lapse is not None,
            disposition=disposition, admission_status=admission, deferred_reason=deferred)

    def encounter_status(self, key):
        effect = self.store.get(key)
        # A key-only control tombstone has no encounter facts to report yet.
        return self._encounter_receipt(effect, effect.encounter, "duplicate") if effect and (effect.encounter or effect.lapse) else None

    def accept_encounter(self, command: GameEncounter) -> EncounterReceipt:
        now = instant(self.clock())
        self.store.lock_target(command.decision.presentation_id, now.date().isoformat())
        self.store.lock_effect(command.key)
        previous = self.store.get(command.key)
        if previous:
            if previous.control or previous.status == "held":
                return self._encounter_receipt(previous, command,
                    "historical_preserved" if previous.applied_at else "withdrawn")
            if previous.payload_hash != encounter_digest(command):
                changed = replace(previous, status="held", reason="changed_source_quarantined",
                                  conflict_json=command.model_dump_json())
                self.store.save(changed, now)
                return self._encounter_receipt(changed, command, "conflict")
            return self._encounter_receipt(previous, command, "duplicate")
        effect = Effect(command.key, command.source.source_revision, now,
            encounter=command, payload_hash=encounter_digest(command),
            available_by=min(instant(command.detected_at), now) + timedelta(hours=48),
            card_id=command.decision.presentation_id,
            card_revision=command.decision.presentation_revision)
        effect = self._advance(effect, now)
        self.store.save(effect, now)
        return self._encounter_receipt(effect, command,
            "stale_revision" if effect.status == "held" else "accepted")

    def _advance(self, effect, now):
        if effect.encounter is None:
            return super()._advance(effect, now)
        if effect.status not in {"pending", "applied"}:
            return effect
        command, day = effect.encounter, now.date().isoformat()
        try:
            mappings = self.decisions.current_encounter(command)
        except EncounterAuthorityUnavailable as error:
            return replace(effect, admission_status="pending", reason=str(error),
                           deferred_reason="authority_unavailable")
        if len(mappings) != 1:
            return replace(effect, status="held", reason="ambiguous_or_conflicted_decision")
        mapping, supplied = mappings[0], command.decision
        fields = ("decision_id", "repertoire_id", "repertoire_revision", "position_key",
                  "selected_response_uci", "presentation_id", "presentation_revision", "route_id", "route_ply")
        if any(getattr(mapping, key) != getattr(supplied, key) for key in fields) or not mapping.available:
            return replace(effect, status="held", reason="stale_current_mapping")
        facts = self.store.encounter_card_facts(effect.card_id, day)
        if facts is None or facts.revision != effect.card_revision or not facts.available:
            return replace(effect, status="held", reason="current_card_unavailable")
        # Keep factual relevance even when old evidence cannot authorize a lapse.
        # A correctly played introduced decision records frequency only.
        disposition = effect.disposition or ("first_introduction" if facts.introduced_at is None
            else "frequency_only" if command.observation == "matched" else "review_priority")
        effect = replace(effect, observation_recorded=True, disposition=disposition)
        if disposition == "frequency_only":
            return replace(effect, status="settled", admission_status="not_requested",
                           settled_at=now, reason="encounter_frequency_recorded")
        if facts.active_attempt or facts.buried:
            reason = "active_attempt" if facts.active_attempt else "buried"
            return replace(effect, admission_status="pending", reason=reason,
                           deferred_reason="active_attempt" if facts.active_attempt else None)
        if facts.introduced_at is None:
            policy = self.introduction_policy
            if policy is None or self.introductions is None:
                return replace(effect, admission_status="pending", reason="encounter_introduction_policy_pending")
            if policy.allowance != "encounter_exception":
                return replace(effect, admission_status="pending", reason="encounter_exception_policy_required")
            result = self.introductions.admit_encounter(day,
                repertoire_id=mapping.repertoire_id, decision_id=mapping.decision_id, target=mapping)
            if result.status not in {"introduced", "already_introduced", "replayed"}:
                reason = getattr(result, "reason", None)
                if reason in {"study_only", "zero_limit", "introduction_allowance"}:
                    return replace(effect, admission_status="pending", reason="encounter_exception_capability_required")
                known = {"disabled", "paused", "active_attempt", "authority_unavailable"}
                return replace(effect, admission_status="pending", reason=reason or "encounter_introduction_deferred",
                               deferred_reason=reason if reason in known else None)
            # A concurrent ordinary introduction is current state, never a new
            # first introduction, fresh charge, rewind or fabricated failure.
            current = self.store.encounter_card_facts(effect.card_id, day)
            if (current is None or current.revision != effect.card_revision
                    or current.introduced_at is None or not current.available):
                raise RuntimeError("canonical encounter introduction did not establish the fenced current card")
        self.store.promote_encounter(effect.card_id, day)
        return replace(effect, status="applied", applied_at=effect.applied_at or now,
                       admission_status="available", reason=None, deferred_reason=None)
