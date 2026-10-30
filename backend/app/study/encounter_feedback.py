"""Current Study priority for real encounters, independently of lapse authority.

Intake stores the existing effect ledger obligation using target-only fences.
Study independently consumes it. Qualified introduced misses use the existing
Again application; canonical introduction remains a separate disposition.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from app.contracts.product_v02.learning import (EncounterProgress, EncounterReceipt,
    EncounterTarget, FirstDepartureWitness, GameEncounter, GameLapse)
from app.contracts.product_v02.repertoire import DecisionPresentation
from app.study.game_feedback import (CardFacts, CurrentDecisionPort, Effect, GameFeedback,
    GameFeedbackStore, effect_id, encoded, instant, semantic_digest)
from app.services.scheduling_review_application import apply_scheduling_review
from app.study.interfaces.feedback_observation import FeedbackObservation

if TYPE_CHECKING:
    from app.study.introductions.application import CommitResult


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
    def current_qualified(self, command: GameEncounter | GameLapse,
                          qualification: FirstDepartureWitness) -> tuple[DecisionPresentation, ...]: ...


class EncounterStore(GameFeedbackStore, Protocol):
    def encounter_card_facts(self, card_id: str, day: str) -> CardFacts | None: ...
    def lock_intake_target(self, card_id: str) -> None: ...
    def promote_encounter(self, card_id: str, day: str) -> None: ...


def encounter_digest(command: GameEncounter) -> str:
    # Qualification changes neither original command facts nor logical identity.
    return hashlib.sha256(encoded(dict(source_digest=command.source.source_digest,
        position=command.decision.position_key, expected=(command.expected_uci,),
        played=command.played_uci, repertoire=command.decision.repertoire_id,
        occurred_at=command.occurred_at)).encode()).hexdigest()


def qualified_encounter_view(command, qualification):
    """Internal factual adapter; original old command bytes remain durable."""
    if isinstance(command,GameEncounter):
        return command.model_copy(update={'decision':qualification.selection.decision})
    selected = qualification.selection
    return GameEncounter(key=command.key,source=selected.source,decision=selected.decision,
        expected_uci=command.finding.expected_uci[0],played_uci=command.finding.played_uci,
        observation='missed',detected_at=command.detected_at,occurred_at=command.occurred_at,
        payload_digest=command.payload_digest)


class EncounterFeedback(GameFeedback):
    def __init__(self, store: EncounterStore, decisions: CurrentEncounterPort, *,
                 clock: Callable[[], datetime], introductions: EncounterIntroductions | None,
                 introduction_policy: EncounterIntroductionPolicy | None,
                 light_first_interval_days: int = 7):
        super().__init__(store, decisions, clock=clock, light_first_interval_days=light_first_interval_days)
        self.introductions, self.introduction_policy = introductions, introduction_policy

    @staticmethod
    def _command(effect):
        if effect.encounter is not None:
            return effect.encounter
        if effect.lapse is None or effect.qualification is None:
            return None
        # Compatibility view only: the durable original lapse stays unchanged.
        # The exact selected decision comes from the qualified owner witness;
        # no introduction-before-game fact is fabricated.
        return qualified_encounter_view(effect.lapse,effect.qualification)

    def _encounter_receipt(self, effect, command, outcome):
        original = self._command(effect) or command
        lapse = effect.lapse or (command if isinstance(command, GameLapse) else None)
        disposition = effect.disposition
        admission = effect.admission_status or "pending"
        deferred = effect.deferred_reason
        if lapse is not None and effect.progress is None:
            disposition = "existing_lapse"
            admission = "settled" if effect.status == "settled" else "pending"
        elif effect.status == "settled" and admission != "not_requested":
            admission, deferred = "settled", None
        elif effect.status == "held":
            admission, deferred = (("not_requested", None) if disposition == "frequency_only"
                                   else ("pending", "authority_unavailable"))
        values = self._receipt(effect, outcome).model_dump(mode="json")
        if lapse is not None and admission == "pending" and values["reason"] is None:
            values["reason"] = "existing_lapse_obligation"
        # This target is not a promise while admission is blocked or its control
        # reason is not yet supplied by the owner. Detection itself stays stored.
        if admission in {"pending", "not_requested"} or effect.progress is not None:
            values["available_by"] = None
        progress = self.store.observed_progress(effect)
        extra = dict(interface_version="0.2.2", progress=progress) if progress else {}
        return EncounterReceipt(**values, **extra,
            detected_at=(lapse.detected_at if lapse else original.detected_at),
            source_digest=(lapse.finding.source_digest if lapse else original.source.source_digest),
            observation=("missed" if lapse else original.observation),
            observation_recorded=effect.observation_recorded or effect.lapse is not None,
            disposition=disposition, admission_status=admission, deferred_reason=deferred)

    def encounter_status(self, key):
        effect = self.store.get(key)
        # A key-only control tombstone has no encounter facts to report yet.
        return self._encounter_receipt(effect, effect.encounter, "duplicate") if effect and (effect.encounter or effect.lapse) else None

    def feedback_observation(self,key) -> FeedbackObservation | None:
        """Current receiving-owner queue/issuance facts without mutation."""
        return self.store.feedback_observation(key,instant(self.clock()))

    def accept_encounter(self, command: GameEncounter) -> EncounterReceipt:
        return self._accept(command, qualification=None)

    def accept_qualified(self, command: GameEncounter | GameLapse, *,
                         qualification: FirstDepartureWitness) -> EncounterReceipt:
        """The same immutable occurrence, qualified through current Study authority."""
        if not qualification.matches(command):
            raise EncounterAuthorityUnavailable("first_departure_command_mismatch")
        return self._accept(command, qualification=qualification)

    def _accept(self, command, *, qualification):
        now = instant(self.clock())
        card = command.decision if isinstance(command, GameEncounter) else command.finding
        digest = encounter_digest(command) if isinstance(command, GameEncounter) else semantic_digest(command)
        self.store.lock_intake_target(card.presentation_id)
        self.store.lock_effect(command.key)
        previous = self.store.get(command.key)
        if previous:
            if previous.control or previous.status == "held":
                return self._encounter_receipt(previous, command,
                    "historical_preserved" if previous.applied_at else "withdrawn")
            if previous.payload_hash != digest:
                changed = replace(previous, status='settled' if previous.status=='settled' else "held", reason="changed_source_quarantined",
                                  conflict_json=command.model_dump_json())
                self.store.save(changed, now)
                return self._encounter_receipt(changed, command, "conflict")
            if (qualification is not None and previous.status != "settled"
                    and previous.progress is None):
                qualified = self._accept_priority(replace(previous, qualification=qualification), now)
                self.store.save(qualified, now)
                return self._encounter_receipt(qualified, command, "duplicate")
            return self._encounter_receipt(previous, command, "duplicate")
        source_revision = (command.source.source_revision if isinstance(command, GameEncounter)
                           else command.finding.source_revision)
        effect = Effect(command.key, source_revision, now,
            encounter=command if isinstance(command, GameEncounter) else None,
            lapse=command if isinstance(command, GameLapse) else None, payload_hash=digest,
            qualification=qualification,
            available_by=None,
            card_id=card.presentation_id, card_revision=card.presentation_revision)
        effect = self._accept_priority(effect, now)
        self.store.save(effect, now)
        return self._encounter_receipt(effect, command,
            "stale_revision" if effect.status == "held" else "accepted")

    def refresh_encounter(self, key, *, qualification=None):
        """Study-owned consumption of one accepted identity; never a producer action."""
        observed = self.store.get(key)
        if observed is None or (observed.encounter is None and observed.lapse is None):
            return None
        now = instant(self.clock())
        self.store.lock_target(observed.card_id,self._day(now).isoformat())
        self.store.lock_effect(key)
        current = self.store.get(key)
        if qualification is not None and not current.control and current.status in {"pending", "applied"}:
            current = replace(current, qualification=qualification)
            if current.progress is None:
                current = self._accept_priority(current, now)
        advanced = self._advance(current,now)
        self.store.save(advanced,now)
        return self._encounter_receipt(advanced,self._command(advanced),'duplicate')

    def _current_encounter(self, effect, now):
        command = self._command(effect)
        if command is None:
            return effect,None,None
        if effect.status not in {"pending", "applied"}:
            return effect,None,None
        day = self._day(now).isoformat()
        try:
            if effect.qualification is not None:
                original = effect.encounter or effect.lapse
                if not effect.qualification.matches(original):
                    raise EncounterAuthorityUnavailable("first_departure_command_mismatch")
                mappings = self.decisions.current_qualified(original, effect.qualification)
            else:
                mappings = self.decisions.current_encounter(command)
        except EncounterAuthorityUnavailable as error:
            return replace(effect, admission_status="pending", reason=str(error),
                           deferred_reason="authority_unavailable"),None,None
        if len(mappings) != 1:
            return replace(effect, status="held", reason="ambiguous_or_conflicted_decision"),None,None
        mapping, supplied = mappings[0], command.decision
        fields = ("decision_id", "repertoire_id", "position_key", "selected_response_uci")
        if effect.qualification is None:
            fields += ("repertoire_revision", "presentation_id", "presentation_revision", "route_id", "route_ply")
        if any(getattr(mapping, key) != getattr(supplied, key) for key in fields) or not mapping.available:
            return replace(effect, status="held", reason="stale_current_mapping"),None,None
        facts = self.store.encounter_card_facts(mapping.presentation_id, day)
        if facts is None or facts.revision != mapping.presentation_revision or not facts.available:
            return replace(effect, status="held", reason="current_card_unavailable"),None,None
        return effect,mapping,facts

    def _accept_priority(self, effect, now):
        """Intake owns only exact validation and durable acceptance, never queue work."""
        effect, mapping, facts = self._current_encounter(effect,now)
        if mapping is None:
            return effect
        command = self._command(effect)
        # Correct observations remain usable frequency evidence. Raw old missed
        # commands are retained for qualification, never new urgency authority.
        if command.observation == "matched":
            disposition = "frequency_only"
        elif effect.qualification is None:
            return replace(effect, observation_recorded=True, admission_status="pending",
                reason="first_departure_qualification_pending")
        else:
            disposition = ("first_introduction" if effect.disposition == "first_introduction"
                           or effect.disposition is None and facts.introduced_at is None else "introduced_again")
        if disposition == 'frequency_only':
            return replace(effect,observation_recorded=True,disposition=disposition,
                status='settled',admission_status='not_requested',settled_at=now,
                reason='encounter_frequency_recorded')
        target = EncounterTarget(card_id=mapping.presentation_id, card_revision=mapping.presentation_revision)
        progress = effect.progress or EncounterProgress(determined_at=effect.qualification.determined_at,
            stream=effect.qualification.stream, target=target, scheduling_review_id=None,
            schedule_applied_at=None, introduction_applied_at=None, remediation_fence=None,
            first_issued=None, first_issued_ordinal=None, settlement=None)
        return replace(effect,observation_recorded=True,disposition=disposition, progress=progress,
            status="pending", admission_status='pending',reason='study_priority_pending',preparation_retry_at=None)

    def _advance(self, effect, now):
        """Study-owned refresh only; acceptance never calls this queue capability."""
        if effect.encounter is None and effect.qualification is None:
            return super()._advance(effect,now)
        if effect.progress is None:
            effect = self._accept_priority(effect, now)
        if effect.progress is None:
            return effect
        effect, mapping, facts = self._current_encounter(effect,now)
        if mapping is None:
            return effect
        command, day = self._command(effect),self._day(now).isoformat()
        disposition = effect.disposition
        effect = replace(effect, observation_recorded=True, disposition=disposition)
        if disposition == "frequency_only":
            return replace(effect, status="settled", admission_status="not_requested",
                           settled_at=now, reason="encounter_frequency_recorded")
        if facts.active_attempt and effect.progress.remediation_fence is not None:
            return effect
        if facts.active_attempt or facts.buried:
            reason = "active_attempt" if facts.active_attempt else "buried"
            return replace(effect, admission_status="pending", reason=reason,
                           deferred_reason="active_attempt" if facts.active_attempt else None)
        target = EncounterTarget(card_id=mapping.presentation_id, card_revision=mapping.presentation_revision)
        progress = effect.progress
        if target != progress.target:
            # Only the current owner may supply a different physical binding.
            # Preserve the real application receipt and any actual first service
            # on the prior target; establish fresh issuance authority for this
            # current target without repeating the accepted consequence.
            progress = self.store.observed_progress(effect)
            changes = dict(target=target)
            if progress.remediation_fence is not None:
                changes.update(prior_remediation_fences=progress.prior_remediation_fences+
                    (progress.remediation_fence,),remediation_fence=self._fence(target,now))
            progress = progress.model_copy(update=changes)
            effect = replace(effect,progress=progress)
        if progress.remediation_fence is not None:
            settled = self._saved_settlement(effect)
            if settled is not None:
                return settled
        if progress.remediation_fence is None:
            fence = self._fence(target, now)
            if disposition == "introduced_again":
                if facts.introduced_at is None:
                    return replace(effect, admission_status="pending", reason="current_target_unintroduced")
                if facts.latest_review_at and instant(facts.latest_review_at) > now:
                    return replace(effect, admission_status="pending", reason="later_review_clock")
                if effect.review_id is None:
                    apply_scheduling_review(self.store.scheduling, target.card_id, "again", guided=False,
                        source_kind="game_lapse", source_ref=effect_id(effect.key),
                        light_first_interval_days=self.light_interval, reviewed_at=now,
                        review_day=self._day(now))
                    review_id = self.store.last_game_review_id(effect.key)
                    application = now
                else:
                    # Existing actual legacy scheduling receipts never repeat.
                    review_id, application = effect.review_id, effect.applied_at
                progress = progress.model_copy(update=dict(target=target,
                    scheduling_review_id=review_id, schedule_applied_at=application.isoformat(),
                    remediation_fence=fence))
                effect = replace(effect, review_id=review_id, progress=progress)
        if disposition == "first_introduction" and facts.introduced_at is None:
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
            current = self.store.encounter_card_facts(target.card_id, day)
            if (current is None or current.revision != target.card_revision
                    or current.introduced_at is None or not current.available):
                raise RuntimeError("canonical encounter introduction did not establish the fenced current card")
        if disposition == "first_introduction" and progress.remediation_fence is None:
            progress = progress.model_copy(update=dict(target=target,
                introduction_applied_at=now.isoformat(), remediation_fence=self._fence(target, now)))
        if self.targets:
            if not self.targets.promote_effect(effect, day):
                raise EncounterAuthorityUnavailable("current_presentation_unavailable_or_busy")
        else:
            self.store.promote_encounter(target.card_id, day)
        return replace(effect, status="applied", applied_at=effect.applied_at or now, progress=progress,
                       admission_status="available", reason=None, deferred_reason=None,preparation_retry_at=None)
