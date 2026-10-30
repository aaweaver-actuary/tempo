"""Games-owned stored review projections and explicit configured commands."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field, model_validator

from app.contracts.product_v02 import Identifier, Value
from app.contracts.product_v02.games import Finding, GameSummary, GameWindow, ProviderSyncStatus, RecentGames
from app.study.interfaces.game_priority import EncounterFeedbackPort, GameEncounter, LearningReceipt
from app.contracts.product_v02.presentation import GameDetail
from .ports import Delivery, JobSpec, JobState, SourceState

Provider = Literal["lichess", "chesscom"]


class ReviewUnavailable(RuntimeError):
    def __init__(self, reason: str, status: int = 409):
        self.reason, self.status = reason, status
        super().__init__(reason)


class SyncConfiguration(Value):
    workspace_id: Identifier
    provider: Provider
    account: Identifier
    window: GameWindow
    repertoire_ids: tuple[Identifier, ...] = Field(min_length=1)
    speeds: tuple[Literal["bullet", "blitz", "rapid", "classical"], ...] = Field(min_length=1, max_length=4)
    rated: bool | None
    interval_seconds: int | None = Field(ge=60, le=86400)
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def explicit_supported_request(self):
        if self.account != self.account.strip():
            raise ValueError("account cannot contain surrounding whitespace")
        if self.provider == "chesscom" and self.rated is False:
            raise ValueError("Chess.com supports rated-only or both; casual-only is unavailable")
        return self


class ReviewStore(Protocol):
    def configuration(self, workspace: str, provider: Provider) -> SyncConfiguration | None: ...
    def configure(self, configuration: SyncConfiguration, expected_revision: int) -> SyncConfiguration: ...
    def recent(self, workspace: str, limit: int) -> tuple[GameSummary, ...]: ...
    def source_detail(self, workspace: str, provider: Provider, game_id: str) -> SourceState | None: ...
    def deliveries(self, source_key: str) -> tuple[Delivery, ...]: ...
    def last_job(self, workspace: str, provider: Provider) -> JobState | None: ...
    def enqueue(self, configuration: SyncConfiguration, job: JobSpec, now: float) -> JobState: ...
    def due_configuration(self, workspace: str, now: float) -> SyncConfiguration | None: ...
    def due_configurations(self, workspace: str, now: float) -> tuple[SyncConfiguration, ...]: ...
    def request_retry(self, workspace: str, source: SourceState, finding: Finding, operation_id: str) -> Delivery: ...
    def delivery(self, operation_id: str) -> Delivery | None: ...


def sync_status(provider: Provider, job: JobState | None) -> ProviderSyncStatus:
    if job is None:
        return ProviderSyncStatus(provider=provider, status="idle", operation_id=None, error=None)
    failed = job.status in {"retryable", "failed", "quarantined"}
    return ProviderSyncStatus(provider=provider, operation_id=job.spec.job_id,
        status="failed" if failed else "complete" if job.status == "complete" else "pending",
        error=(job.error or job.status) if failed else None)


class GameReview:
    def __init__(self, *, workspace: str, store: ReviewStore, learning: EncounterFeedbackPort,
                 build_job: Callable[[SyncConfiguration, str], JobSpec],
                 deliver: Callable[[Delivery], None], now: Callable[[], float]):
        self.workspace, self.store, self.learning = workspace, store, learning
        self.build_job, self.deliver, self.now = build_job, deliver, now

    def configuration(self, provider: Provider) -> SyncConfiguration | None:
        return self.store.configuration(self.workspace, provider)

    def configure(self, configuration: SyncConfiguration, expected_revision: int) -> SyncConfiguration:
        if configuration.workspace_id != self.workspace:
            raise ReviewUnavailable("configuration_workspace_mismatch")
        # Validate new writes here, not in the stored value's deserializer.
        # Historical/manual windows remain arbitrary-width and old seeds readable.
        if configuration.interval_seconds is not None:
            start = datetime.fromisoformat(configuration.window.start_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(configuration.window.end_at.replace("Z", "+00:00"))
            if not 60 <= (end - start).total_seconds() <= 86400:
                raise ReviewUnavailable(
                    "Ongoing collection needs a UTC seed of 60–86400 seconds (1 minute–24 hours). "
                    "Adjust the dates or choose Historical/manual import.", 422)
        return self.store.configure(configuration, expected_revision)

    def recent_games(self) -> RecentGames:
        return RecentGames(games=self.store.recent(self.workspace, 50), sync=tuple(
            sync_status(provider, self.store.last_job(self.workspace, provider))
            for provider in ("lichess", "chesscom")))

    def game_detail(self, *, provider: Provider, game_id: str, repertoire_id: str | None) -> GameDetail:
        source = self.store.source_detail(self.workspace, provider, game_id)
        if source is None or source.normalized is None:
            raise ReviewUnavailable("game_not_available", 404)
        if source.quarantined:
            # Preserve original captures; do not draw changed findings on old moves.
            raise ReviewUnavailable(source.diagnostic or "source_quarantined")
        game = source.normalized
        findings = tuple(f for f in source.findings if repertoire_id is None or f.repertoire_id == repertoire_id)
        encounter_keys = {effect.key.logical_id() for effect in source.effects
                          if isinstance(effect, GameEncounter)}
        receipts = []
        for delivery in self.store.deliveries(source.source_key):
            key = delivery.command.key
            # Controls retain the original effect kind. The legacy read operation
            # cannot represent encounter disposition/admission/deferral fields.
            receipt = (self.learning.encounter_status(key)
                if delivery.encounter is not None or key.logical_id() in encounter_keys
                else self.learning.effect_status(key))
            if receipt is not None and receipt not in receipts:
                receipts.append(receipt)
            elif receipt is None and delivery.receipt is not None and delivery.receipt not in receipts:
                receipts.append(delivery.receipt)
        return GameDetail(provider=provider, game_id=game.game_id,
            source_revision=source.original.source_revision, played_at=source.original.played_at,
            time_precision=source.original.time_precision, player_color=game.learner_color,
            starting_fen=game.starting_fen, moves=tuple(game.moves), findings=findings, receipts=tuple(receipts))

    def sync(self, *, provider: Provider, operation_id: str) -> ProviderSyncStatus:
        configuration = self.configuration(provider)
        if configuration is None:
            raise ReviewUnavailable("explicit_sync_configuration_required", 422)
        job = self.build_job(configuration, operation_id)
        return sync_status(provider, self.store.enqueue(configuration, job, self.now()))

    def enqueue_due(self) -> bool:
        configuration = self.store.due_configuration(self.workspace, self.now())
        if configuration is None:
            return False
        interval = configuration.interval_seconds
        operation = f"recurring:{configuration.provider}:{configuration.revision}:{int(self.now() // interval)}"
        self.store.enqueue(configuration, self.build_job(configuration, operation), self.now())
        return True

    def retry_feedback(self, *, finding: Finding, operation_id: str) -> LearningReceipt:
        source = self.store.source_detail(self.workspace, finding.provider, finding.game_id)
        if source is None or source.quarantined or finding not in source.findings or not finding.lapse_eligible:
            raise ReviewUnavailable("current_confirmed_finding_required")
        delivery = self.store.request_retry(self.workspace, source, finding, operation_id)
        current = self.learning.effect_status(delivery.command.key)
        if current is not None and delivery.state == "acknowledged":
            return current
        self.deliver(delivery)
        persisted = self.store.delivery(delivery.operation_id)
        if persisted is None or persisted.receipt is None:
            raise ReviewUnavailable("owner_acknowledgment_pending", 503)
        return persisted.receipt
