"""Study's game-effect policy over borrowed, semantic storage capabilities.

No Games, provider, database or composition imports. Transaction lifetime is the
caller's; exceptions must roll back schedule, queue and receipt together.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from app.contracts.product_v02.learning import GameEncounter

from app.contracts.product_v02.learning import (
    GameEffectControl, GameEffectKey, GameLapse, LearningReceipt,
)
from app.contracts.product_v02.repertoire import DecisionPresentation
from app.services.scheduling_review_application import apply_scheduling_review
from app.services.scheduling_review_contracts import SchedulingReviewStore


def instant(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.utcoffset() is None:
        raise ValueError("an aware clock is required")
    return result.astimezone(timezone.utc)


def encoded(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def effect_id(key: GameEffectKey) -> str:
    return "game-effect:" + hashlib.sha256(key.logical_id().encode()).hexdigest()


def semantic_digest(lapse: GameLapse) -> str:
    # Detection/recomparison/retry metadata is not another logical failure.
    finding = lapse.finding
    return hashlib.sha256(encoded(dict(
        source_digest=finding.source_digest, position=finding.position_key,
        expected=finding.expected_uci, played=finding.played_uci,
        repertoire=finding.repertoire_id, occurred_at=lapse.occurred_at,
    )).encode()).hexdigest()


def game_scoped_introduction(mapping: DecisionPresentation, lapse: GameLapse) -> bool:
    """Explicit owner chronology for this exact canonical source, never an instant."""
    scope = mapping.introduction_game
    return bool(mapping.introduced_before_game is True and scope is not None
        and (scope.provider,scope.game_id,scope.source_revision,scope.source_digest) == (
            lapse.key.provider,lapse.key.game_id,lapse.finding.source_revision,lapse.finding.source_digest))


@dataclass(frozen=True)
class Effect:
    key: GameEffectKey
    source_revision: int
    received_at: datetime
    status: str = "pending"
    lapse: GameLapse | None = None
    payload_hash: str | None = None
    available_by: datetime | None = None
    card_id: str | None = None
    card_revision: int | None = None
    reason: str | None = None
    applied_at: datetime | None = None
    review_id: int | None = None
    settled_at: datetime | None = None
    settlement_review_id: int | None = None
    control: GameEffectControl | None = None
    conflict_json: str | None = None
    # The next-release command shares this ledger and control tombstones. Its
    # original source/observation is immutable; owner admission facts may advance.
    encounter: GameEncounter | None = None
    disposition: str | None = None
    admission_status: str | None = None
    deferred_reason: str | None = None
    observation_recorded: bool = False


@dataclass(frozen=True)
class CardFacts:
    revision: int
    introduced_at: datetime | None
    available: bool
    active_attempt: bool = False
    buried: bool = False
    latest_review_at: datetime | None = None


@dataclass(frozen=True)
class Completion:
    review_id: int
    card_id: str
    revision: int
    reviewed_at: datetime
    clean: bool
    source_kind: str


class CurrentDecisionPort(Protocol):
    """Current immutable owner mapping, fenced by the caller's transaction.

    Composition injects this capability; Study never imports an index or Games.
    Missing/conflicted/ambiguous authority returns zero/multiple mappings.
    """
    def current(self, lapse: GameLapse) -> tuple[DecisionPresentation, ...]: ...


class GameFeedbackStore(Protocol):
    scheduling: SchedulingReviewStore
    def lock_target(self, card_id: str, day: str) -> None: ...
    def lock_effect(self, key: GameEffectKey) -> None: ...
    def get(self, key: GameEffectKey) -> Effect | None: ...
    def save(self, effect: Effect, now: datetime) -> None: ...
    def card_facts(self, card_id: str, day: str) -> CardFacts | None: ...
    def latest_clean_review(self, card_id: str, revision: int, after: datetime) -> Completion | None: ...
    def last_game_review_id(self, key: GameEffectKey) -> int: ...
    def promote(self, card_id: str, day: str) -> None: ...
    def owed_page(self, after: str, limit: int) -> tuple[Effect, ...]: ...
    def card_effects(self, card_id: str) -> tuple[Effect, ...]: ...


class GameFeedback:
    """Implements #482 LearningFeedbackPort; no connection/commit authority."""
    def __init__(self, store: GameFeedbackStore, decisions: CurrentDecisionPort,
                 *, clock: Callable[[], datetime], light_first_interval_days: int = 7):
        self.store, self.decisions, self.clock = store, decisions, clock
        self.light_interval = light_first_interval_days

    def _receipt(self, effect: Effect, outcome: str) -> LearningReceipt:
        return LearningReceipt(
            key=effect.key, source_revision=effect.source_revision,
            status=effect.status, outcome=outcome, owner_receipt_id=effect_id(effect.key),
            admitted_at=effect.received_at.isoformat(),
            available_by=effect.available_by.isoformat() if effect.available_by else None,
            applied_at=effect.applied_at.isoformat() if effect.applied_at else None,
            reason=effect.reason,
        )

    def effect_status(self, key: GameEffectKey) -> LearningReceipt | None:
        effect = self.store.get(key)
        return self._receipt(effect, "duplicate") if effect else None

    def accept_lapse(self, lapse: GameLapse) -> LearningReceipt:
        now = instant(self.clock())
        self.store.lock_target(lapse.finding.presentation_id, now.date().isoformat())
        self.store.lock_effect(lapse.key)
        prior = self.store.get(lapse.key)
        if prior:
            if prior.control or prior.status == "held":
                return self._receipt(prior, "historical_preserved" if prior.applied_at else "withdrawn")
            if prior.payload_hash != semantic_digest(lapse):
                changed = replace(prior, status="held", reason="changed_source_quarantined",
                                  conflict_json=lapse.model_dump_json())
                self.store.save(changed, now)
                return self._receipt(changed, "conflict")
            # Equivalent recomparison never reapplies or postpones the deadline.
            return self._receipt(prior, "duplicate")
        detected = min(instant(lapse.detected_at), now)
        effect = Effect(lapse.key, lapse.finding.source_revision, now, lapse=lapse,
                        payload_hash=semantic_digest(lapse), available_by=detected + timedelta(hours=48),
                        card_id=lapse.finding.presentation_id,
                        card_revision=int(lapse.finding.presentation_revision))
        effect = self._advance(effect, now)
        self.store.save(effect, now)
        return self._receipt(effect, "accepted" if effect.status != "held" else "stale_revision")

    def control_effect(self, command: GameEffectControl) -> LearningReceipt:
        now = instant(self.clock())
        self.store.lock_effect(command.key)
        prior = self.store.get(command.key)
        if prior and prior.control:
            return self._receipt(prior, "historical_preserved" if prior.applied_at else "duplicate")
        effect = prior or Effect(command.key, command.source_revision, now)
        # Opaque source revisions cannot be ordered. A correction is conservative
        # and permanent until explicit D08 resolution; it cannot undo history.
        effect = replace(effect, status="held", reason="source_" + command.action,
                         control=command)
        self.store.save(effect, now)
        return self._receipt(effect, "historical_preserved" if effect.applied_at else "withdrawn")

    def _advance(self, effect: Effect, now: datetime) -> Effect:
        lapse, day = effect.lapse, now.date().isoformat()
        if lapse is None or effect.status not in {"pending", "applied"}:
            return effect
        mappings = self.decisions.current(lapse)
        if len(mappings) != 1:
            return replace(effect, status="held", reason="ambiguous_or_conflicted_decision")
        mapping, finding = mappings[0], lapse.finding
        if (mapping.decision_id, mapping.repertoire_id, mapping.repertoire_revision,
            mapping.position_key, mapping.selected_response_uci, mapping.presentation_id,
            mapping.presentation_revision) != (
                lapse.key.decision_id, finding.repertoire_id, finding.repertoire_revision,
                finding.position_key, finding.expected_uci[0], effect.card_id,
                finding.presentation_revision):
            return replace(effect, status="held", reason="stale_current_mapping")
        facts = self.store.card_facts(effect.card_id, day)
        occurred = instant(lapse.occurred_at) if lapse.occurred_at else None
        mapping_before_game = occurred is not None and (
            (mapping.introduced_at is not None and instant(mapping.introduced_at) <= occurred)
            or game_scoped_introduction(mapping,lapse))
        if (facts is None or facts.revision != effect.card_revision or not facts.available
                or not mapping.available or not mapping.introduction_matches_current_content
                or mapping.introduction != "introduced" or not mapping_before_game
                or facts.introduced_at is None or occurred is None
                or facts.introduced_at > occurred):
            return replace(effect, status="held", reason="unavailable_or_unintroduced")
        # D10: a genuine later clean review already satisfies old evidence. Never
        # insert a backdated FSRS event or invent a successful Study completion.
        clean = self.store.latest_clean_review(effect.card_id, effect.card_revision, occurred)
        if clean and clean.reviewed_at <= now:
            return replace(effect, status="settled", reason="later_genuine_review",
                           settled_at=clean.reviewed_at, settlement_review_id=clean.review_id)
        if facts.active_attempt:
            return replace(effect, reason="active_attempt")
        if facts.buried:
            return replace(effect, reason="buried")
        if facts.latest_review_at and facts.latest_review_at > now:
            return replace(effect, reason="later_review_clock")
        if effect.applied_at is None:
            apply_scheduling_review(
                self.store.scheduling, effect.card_id, "again", guided=False,
                source_kind="game_lapse", source_ref=effect_id(effect.key),
                light_first_interval_days=self.light_interval, reviewed_at=now,
                review_day=now.date(),
            )
            effect = replace(effect, status="applied", applied_at=now,
                             review_id=self.store.last_game_review_id(effect.key))
        self.store.promote(effect.card_id, day)
        return replace(effect, reason=None)

    def maintain(self, *, after: str = "", limit: int = 1) -> str | None:
        """One effect per borrowed transaction; caller persists the cursor.

        Acquiring another card after a date/effect lock could invert Study's
        card-before-day ordering. Commit this unit before advancing the cursor.
        """
        if limit != 1:
            raise ValueError("maintenance applies one effect per transaction")
        now = instant(self.clock())
        page = self.store.owed_page(after, limit)
        for observed in page:
            self.store.lock_target(observed.card_id, now.date().isoformat())
            self.store.lock_effect(observed.key)
            current = self.store.get(observed.key)
            self.store.save(self._advance(current, now), now)
        return effect_id(page[-1].key) if len(page) == limit else None

    def settle(self, completion: Completion) -> None:
        """Called after the owning review write in the same card transaction."""
        if not completion.clean or completion.source_kind not in {"study", "study_exercise"}:
            return
        now = instant(self.clock())
        for observed in self.store.card_effects(completion.card_id):
            self.store.lock_effect(observed.key)
            effect = self.store.get(observed.key)
            if (effect.status in {"pending", "applied"} and effect.card_revision == completion.revision
                    and completion.reviewed_at >= effect.received_at):
                self.store.save(replace(effect, status="settled", reason="genuine_review",
                    settled_at=completion.reviewed_at, settlement_review_id=completion.review_id), now)
