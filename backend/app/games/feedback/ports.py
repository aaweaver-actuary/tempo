"""Games-owned workflow facts and capabilities; shared DTOs stay with T01."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from app.contracts.product_v02 import UtcTimestamp, Value
from app.contracts.product_v02.games import CanonicalGame, Finding, FirstDepartureSelection, GamePage, GameRecord, GameRejection, GameWindow
from app.contracts.product_v02.learning import EncounterReceipt, FirstDepartureWitness, GameEffectControl, GameEncounter, GameLapse, LearningReceipt
from app.contracts.product_v02.repertoire import RepertoireSnapshot


class JobSpec(Value):
    job_id: str
    workspace_id: str
    provider: Literal["lichess", "chesscom"]
    account: str
    window: GameWindow
    repertoire_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    filters: tuple[tuple[str, str], ...] = ()
    configuration_revision: int | None = Field(default=None, ge=1)
    stream: Literal["forward", "reconciliation", "manual"] = "manual"


class JobState(Value):
    spec: JobSpec
    version: int = 0
    status: Literal["queued", "running", "retryable", "failed", "quarantined", "complete"] = "queued"
    continuation: str | None = None
    window_complete: bool = False
    last_checkpoint_at: str | None = None
    pages_committed: int = 0
    next_attempt_at: float = 0
    lease_until: float = 0
    attempts: int = 0
    error: str | None = None
    rejections: tuple[GameRejection, ...] = ()


class SourceState(Value):
    source_key: str
    version: int = 0
    original: GameRecord
    record_fingerprint: str
    normalized: CanonicalGame | None
    findings: tuple[Finding, ...]
    comparison_revision: str
    effects: tuple[GameLapse | GameEncounter, ...] = ()
    encounters: tuple[GameEncounter, ...] = ()
    quarantined: bool = False
    diagnostic: str | None = None
    first_departure: FirstDepartureSelection | None = None
    determined_at: UtcTimestamp | None = None


class Capture(Value):
    source_key: str
    account: str
    record: GameRecord
    provider_record_json: str
    normalized: CanonicalGame | None
    findings: tuple[Finding, ...]
    comparison_revision: str
    diagnostic: str | None = None
    encounters: tuple[GameEncounter, ...] = ()
    first_departure: FirstDepartureSelection | None = None
    determined_at: UtcTimestamp | None = None


class Delivery(Value):
    operation_id: str
    source_key: str
    lapse: GameLapse | None = None
    encounter: GameEncounter | None = None
    control: GameEffectControl | None = None
    state: Literal["pending", "acknowledged", "superseded", "failed"] = "pending"
    attempts: int = 0
    next_attempt_at: float = 0
    receipt: EncounterReceipt | LearningReceipt | None = None
    error: str | None = None
    # Qualification never replaces the original command or its logical key.
    qualification_status: Literal["pending", "ready", "held", "ineligible"] = "pending"
    qualification_reason: str | None = None
    qualification_retryable: bool = True
    qualification: FirstDepartureWitness | None = None
    determined_at: UtcTimestamp | None = None
    origin_job_id: str | None = None
    configuration_revision: int | None = Field(default=None, ge=1)
    stream: Literal["forward", "reconciliation", "manual"] | None = None
    service_sequence: int = Field(default=0, ge=0)
    owner_effect_id: str | None = None

    @model_validator(mode="after")
    def exactly_one_command(self):
        if sum(item is not None for item in (self.lapse, self.encounter, self.control)) != 1:
            raise ValueError("delivery requires exactly one semantic owner command")
        if self.qualification is not None:
            if self.control is not None or not self.qualification.matches(self.command):
                raise ValueError("qualification must bind the original automatic command")
            if self.determined_at != self.qualification.determined_at:
                raise ValueError("qualification cannot restart the actionable determination")
        if self.qualification_status == "ready" and self.qualification is None:
            raise ValueError("ready delivery requires complete first-departure qualification")
        if self.qualification_status in {"held", "ineligible"} and not self.qualification_reason:
            raise ValueError("held or ineligible delivery requires an explicit reason")
        return self

    @property
    def command_kind(self) -> str:
        return "control" if self.control is not None else ("encounter" if self.encounter is not None else "lapse")

    @property
    def command(self) -> GameLapse | GameEncounter | GameEffectControl:
        return self.control if self.control is not None else (self.encounter if self.encounter is not None else self.lapse)


class PageCommit(Value):
    job: JobState
    sources: tuple[SourceState, ...]
    captures: tuple[Capture, ...]
    deliveries: tuple[Delivery, ...]
    provider_page_json: str | None = None
    input_continuation: str | None = None


class ConcurrentUpdate(RuntimeError):
    """Another commit won; discard this uncommitted calculation and retry."""


class DeliveryDeferred(RuntimeError):
    """Known uncommitted semantic/preparation hold, not uncertain transport."""
    def __init__(self, reason: str, *, retry_after: float = 1, retryable: bool = True):
        self.reason, self.retry_after = reason, max(0, retry_after)
        self.retryable = retryable
        super().__init__(reason)


class AcquisitionFailure(RuntimeError):
    def __init__(self, reason: str, *, retry_after: float = 0, retryable: bool = True):
        super().__init__(reason)
        self.retry_after = max(0, retry_after)
        self.retryable = retryable


class Clock(Protocol):
    def now(self) -> float: ...
    def utc_now(self) -> str: ...


class Acquisition(Protocol):
    """Composition supplies a finite-timeout, bounded-page provider adapter."""
    def fetch_page(self, *, account: str, window: GameWindow, continuation: str | None = None) -> GamePage: ...


class Normalizer(Protocol):
    def normalize(self, record: GameRecord, spec: JobSpec) -> CanonicalGame: ...


class SourceIdentity(Protocol):
    """Bind verified PGN history to its canonical storage identity, without writes.

    Raw acquisition provenance stays in Capture.record. The returned game's
    digest/revision/time spelling are the canonical source owner's facts;
    commit_page must fence the same identity before publishing any command.
    """
    def bind_source(self, game: CanonicalGame, *, record: GameRecord, spec: JobSpec) -> CanonicalGame: ...


class Comparer(Protocol):
    def compare(self, game: CanonicalGame, snapshot: RepertoireSnapshot, *,
                record: GameRecord, spec: JobSpec) -> tuple[Finding, ...]: ...


class GamesStore(Protocol):
    """Each operation owns a short transaction and releases it before returning.

    commit_page checks expected job/source versions, preserves capture revisions,
    inserts commands idempotently, supersedes unsent lapse/encounter commands for controlled keys,
    and publishes all facts/checkpoint atomically. No external code runs inside it.
    """
    def enqueue(self, spec: JobSpec) -> JobState: ...
    def claim_job(self, now: float, lease_until: float) -> JobState | None: ...
    def load_job(self, job_id: str) -> JobState | None: ...
    def save_job(self, job: JobState) -> None: ...
    def source(self, source_key: str) -> SourceState | None: ...
    def commit_page(self, commit: PageCommit) -> None: ...
    def pending_delivery(self, now: float, *, ready_only: bool = False,
                         workspace: str | None = None, priority_order: bool = False) -> Delivery | None: ...
    def has_determined_delivery(self, workspace: str, *, before_stream: str | None = None,
                               before_determined_at: str | None = None,
                               before_effect_id: str | None = None) -> bool: ...
    def save_delivery(self, delivery: Delivery, *, serviced: bool = False) -> None: ...
    def delivery(self, operation_id: str) -> Delivery | None: ...


class DeliveryQualifier(Protocol):
    """Complete local comparison before owner delivery, outside its writer."""
    def prepare(self, delivery: Delivery) -> Delivery: ...

