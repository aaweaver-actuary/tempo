"""Games-owned workflow facts and capabilities; shared DTOs stay with T01."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from app.contracts.product_v02 import Value
from app.contracts.product_v02.games import CanonicalGame, Finding, GamePage, GameRecord, GameRejection, GameWindow
from app.contracts.product_v02.learning import GameEffectControl, GameLapse, LearningReceipt
from app.contracts.product_v02.repertoire import RepertoireSnapshot


class JobSpec(Value):
    job_id: str
    workspace_id: str
    provider: Literal["lichess", "chesscom"]
    account: str
    window: GameWindow
    repertoire_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    filters: tuple[tuple[str, str], ...] = ()


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
    effects: tuple[GameLapse, ...] = ()
    quarantined: bool = False
    diagnostic: str | None = None


class Capture(Value):
    source_key: str
    account: str
    record: GameRecord
    provider_record_json: str
    normalized: CanonicalGame | None
    findings: tuple[Finding, ...]
    comparison_revision: str
    diagnostic: str | None = None


class Delivery(Value):
    operation_id: str
    source_key: str
    lapse: GameLapse | None = None
    control: GameEffectControl | None = None
    state: Literal["pending", "acknowledged", "superseded", "failed"] = "pending"
    attempts: int = 0
    next_attempt_at: float = 0
    receipt: LearningReceipt | None = None
    error: str | None = None

    @model_validator(mode="after")
    def exactly_one_command(self):
        if (self.lapse is None) == (self.control is None):
            raise ValueError("delivery requires exactly one semantic owner command")
        return self

    @property
    def command(self) -> GameLapse | GameEffectControl:
        return self.control if self.control is not None else self.lapse


class PageCommit(Value):
    job: JobState
    sources: tuple[SourceState, ...]
    captures: tuple[Capture, ...]
    deliveries: tuple[Delivery, ...]
    provider_page_json: str | None = None
    input_continuation: str | None = None


class ConcurrentUpdate(RuntimeError):
    """Another commit won; discard this uncommitted calculation and retry."""


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


class Comparer(Protocol):
    def compare(self, game: CanonicalGame, snapshot: RepertoireSnapshot, *,
                record: GameRecord, spec: JobSpec) -> tuple[Finding, ...]: ...


class GamesStore(Protocol):
    """Each operation owns a short transaction and releases it before returning.

    commit_page checks expected job/source versions, preserves capture revisions,
    inserts commands idempotently, supersedes unsent lapses for controlled keys,
    and publishes all facts/checkpoint atomically. No external code runs inside it.
    """
    def enqueue(self, spec: JobSpec) -> JobState: ...
    def claim_job(self, now: float, lease_until: float) -> JobState | None: ...
    def load_job(self, job_id: str) -> JobState | None: ...
    def save_job(self, job: JobState) -> None: ...
    def source(self, source_key: str) -> SourceState | None: ...
    def commit_page(self, commit: PageCommit) -> None: ...
    def pending_delivery(self, now: float) -> Delivery | None: ...
    def save_delivery(self, delivery: Delivery) -> None: ...
    def delivery(self, operation_id: str) -> Delivery | None: ...

