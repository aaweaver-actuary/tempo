"""Concrete, bounded provider wiring for the optional Games worker only.

No process, credential, connection, timer or Study work starts on import.
Games' coordinator owns durable revisions/checkpoints and retries.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from threading import Event, Lock
from typing import Literal

import httpx
from pydantic import Field, model_validator

from app.contracts.product_v02 import Revision, Value
from app.contracts.product_v02.games import CanonicalGame, Finding, GamePage, GameRecord, GameWindow
from app.contracts.product_v02.learning import EncounterFeedbackPort, LearningFeedbackPort
from app.contracts.product_v02.repertoire import RepertoireSnapshot, RepertoireSnapshotPort
from app.games.feedback.comparison import compare_game, normalize_record
from app.games.feedback.coordinator import Coordinator
from app.games.feedback.jobs import FeedbackRunner
from app.games.feedback.ports import AcquisitionFailure, Clock, GamesStore, JobSpec
from app.games.providers.chesscom_acquisition import ChesscomAcquisitionError, fetch_chesscom_page
from app.games.providers.lichess_acquisition import LichessAcquisitionError, fetch_lichess_page
from app.repertoire import build_index

class AcquisitionPolicy(Value):
    """Explicit user filters plus finite integration resource bounds.

    No population, account, window or game filter is inferred here.
    source_revision is an acquisition label; the Games store reconciles evidence.
    """
    provider: Literal["lichess", "chesscom"]
    speeds: tuple[Literal["bullet", "blitz", "rapid", "classical"], ...] = Field(min_length=1)
    rated: bool | None
    source_revision: Revision
    page_size: int = Field(default=100, ge=1, le=1000)
    timeout_seconds: float = Field(default=10, gt=0, le=15, allow_inf_nan=False)
    total_timeout_seconds: float = Field(default=30, gt=0, le=30, allow_inf_nan=False)
    max_response_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=32 * 1024 * 1024)

    @model_validator(mode="after")
    def supported_provider_bounds(self):
        if self.provider == "chesscom" and self.rated is False:
            raise ValueError("Chess.com casual-only filter is unavailable; choose rated-only or both explicitly")
        if self.provider == "lichess" and self.max_response_bytes > 8 * 1024 * 1024:
            raise ValueError("Lichess response bound cannot exceed 8 MiB")
        return self


class AcquisitionDeadline(TimeoutError):
    """A total I/O budget expired; the input checkpoint remains authoritative."""
    kind = "timeout"
    retry_after = None

    def __init__(self, continuation: str | None):
        super().__init__("provider page exceeded its total I/O budget")
        self.continuation = continuation


class ProviderAcquisition:
    """Implements synchronous GameAcquisitionPort at the worker boundary.

    Async callers use fetch_page_async; never call fetch_page on the API event
    loop. Each invocation owns and closes one client, with one HTTP connection.
    Cancellation and provider errors escape to the coordinator without retry or
    checkpoint changes. The returned provider subtype is preserved verbatim.
    """
    def __init__(self, policy: AcquisitionPolicy, *,
                 cancelled: Callable[[], bool] | None = None,
                 transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None):
        self.policy = policy
        self.cancelled = cancelled
        self.transport_factory = transport_factory
        self._unit = Lock()
        self._active = None

    def cancel(self) -> None:
        active = self._active
        if active is not None:
            loop, task = active
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # The owned loop has already closed its response/client.

    def fetch_page(self, *, account: str, window: GameWindow,
                   continuation: str | None = None) -> GamePage:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("use fetch_page_async on an event loop; synchronous acquisition belongs to the optional worker")
        return asyncio.run(self.fetch_page_async(account=account, window=window,
                                                continuation=continuation))

    async def fetch_page_async(self, *, account: str, window: GameWindow,
                              continuation: str | None = None) -> GamePage:
        policy = self.policy
        if not self._unit.acquire(blocking=False):
            raise RuntimeError("provider page is already running")
        self._active = (asyncio.get_running_loop(), asyncio.current_task())
        try:
            transport = self.transport_factory() if self.transport_factory is not None else None
            async with asyncio.timeout(policy.total_timeout_seconds):
                async with httpx.AsyncClient(transport=transport, trust_env=False,
                        follow_redirects=False, timeout=policy.timeout_seconds,
                        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
                    common = dict(account=account, window=window, client=client,
                        source_revision=policy.source_revision, continuation=continuation,
                        speeds=policy.speeds, timeout_seconds=policy.timeout_seconds,
                        max_response_bytes=policy.max_response_bytes, cancelled=self.cancelled)
                    if policy.provider == "lichess":
                        return await fetch_lichess_page(**common, rated=policy.rated,
                                                        page_size=policy.page_size)
                    return await fetch_chesscom_page(**common, rated_only=policy.rated is True)
        except TimeoutError:
            raise AcquisitionDeadline(continuation) from None
        finally:
            self._active = None
            self._unit.release()


class WorkflowAcquisition:
    """Translate provider errors to #489's injected AcquisitionFailure class.

    Retry-After is an HTTP header in both providers; the workflow expects seconds.
    No state mutation/retry policy is placed in this transport translation.
    """
    def __init__(self, acquisition: ProviderAcquisition, *,
                 now: Callable[[], float], failure: Callable[..., Exception] = AcquisitionFailure):
        self.acquisition, self.failure, self.now = acquisition, failure, now

    def fetch_page(self, *, account: str, window: GameWindow,
                   continuation: str | None = None) -> GamePage:
        try:
            return self.acquisition.fetch_page(account=account, window=window,
                                               continuation=continuation)
        except (LichessAcquisitionError, ChesscomAcquisitionError, AcquisitionDeadline, asyncio.CancelledError) as error:
            kind = "cancelled" if isinstance(error, asyncio.CancelledError) else getattr(error, "kind", getattr(error, "code", "transport_error"))
            retryable = getattr(error, "retryable", kind in {
                "rate_limited", "http_error", "transport_error", "timeout", "cancelled"})
            raw_delay = getattr(error, "retry_after", None)
            delay = 0.0
            if raw_delay is not None:
                if str(raw_delay).isdigit():
                    delay = float(raw_delay)
                else:
                    # Provider validation has already restricted this to HTTP-date.
                    delay = max(0.0, parsedate_to_datetime(raw_delay).timestamp() - self.now())
            raise self.failure(f"{self.acquisition.policy.provider}_{kind}",
                               retry_after=delay, retryable=retryable) from None


class WorkflowNormalizer:
    """Concrete #486 normalizer for #489's documented Normalizer capability."""
    def __init__(self, resolve_game_id=None):
        self.resolve_game_id = resolve_game_id

    def normalize(self, record: GameRecord, spec: JobSpec) -> CanonicalGame:
        game_id = self.resolve_game_id(spec.provider, record.source_game_id) if self.resolve_game_id else None
        return normalize_record(record, provider=spec.provider, account=spec.account,
                                workspace_id=spec.workspace_id, game_id=game_id)


class WorkflowComparer:
    """The same index/comparer seam used by #488; no learning writer."""
    def compare(self, game: CanonicalGame, snapshot: RepertoireSnapshot, *,
                record: GameRecord, spec: JobSpec) -> tuple[Finding, ...]:
        return compare_game(game, provider=spec.provider, time_precision=record.time_precision,
                            snapshot=snapshot, reentry_lookup=build_index(snapshot))


def sync_job(*, job_id: str, workspace_id: str, account: str, window: GameWindow,
             repertoire_ids: tuple[str, ...], policy: AcquisitionPolicy) -> JobSpec:
    """Bind an explicit request to the provider's canonical account/filter facts.

    This creates an owner JobSpec, without enqueueing or changing saved Settings.
    Both providers return casefolded account identities; a job must bind the same
    identity or the workflow correctly rejects its page as out of scope.
    """
    if not account or account != account.strip():
        raise ValueError("account must be explicit without surrounding whitespace")
    speeds = ",".join(sorted(set(policy.speeds)))
    if policy.provider == "lichess":
        filters = (("perfType", speeds),) + (() if policy.rated is None else (
            ("rated", str(policy.rated).lower()),))
    else:
        filters = (("speeds", speeds), ("rated_only", str(policy.rated is True).lower()),
                   ("variant", "standard"))
    return JobSpec(job_id=job_id, workspace_id=workspace_id, provider=policy.provider,
        account=account.casefold(), window=window, repertoire_ids=repertoire_ids,
        filters=filters)


class GameSyncRuntime:
    """Private optional-worker assembly, with no process or timer of its own.

    stop owns only acquisition cancellation and the Games runner. Supplied store,
    snapshot and Learning capabilities are borrowed and are never closed here.
    """
    def __init__(self, runner: FeedbackRunner, stop: Event, cancel_acquisitions=(), admission=None):
        self.runner, self._stop = runner, stop
        self._cancel_acquisitions = tuple(cancel_acquisitions)
        self._admission=admission

    def run_once(self) -> str:
        if self._admission is None or self._stop.is_set():
            return self.runner.run_once()
        from app.services.redis_admission_gate import BackgroundAdmissionDeferred
        try:
            with self._admission():
                return self.runner.run_once()
        except BackgroundAdmissionDeferred:
            return "deferred"

    def request_stop(self) -> None:
        self._stop.set()
        self.runner.request_stop()
        for cancel in self._cancel_acquisitions:
            cancel()

    def close(self) -> None:
        self.request_stop()
        self.runner.close()


def create_game_sync(*, policies: tuple[AcquisitionPolicy, ...], store: GamesStore,
                     snapshots: RepertoireSnapshotPort, learning: LearningFeedbackPort | EncounterFeedbackPort,
                     clock: Clock, max_page_records: int = 100,
                     max_page_positions: int = 10000,
                     normalizer=None,
                     encounter_feedback=False,
                     admission=None,
                     transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None) -> GameSyncRuntime:
    """Wire published concrete seams; the caller selects when bounded units run.

    A caller supplies explicit policies for both providers. The existing #487
    owner is the sole Learning writer. Nothing starts at construction; no
    startup/readiness hook is registered with the ordinary Study application.
    """
    if not 1<=len(policies)<=2 or len({policy.provider for policy in policies})!=len(policies):
        raise ValueError("one_explicit_policy_per_configured_provider_required")
    stop = Event()
    providers = {policy.provider: WorkflowAcquisition(ProviderAcquisition(policy,
        cancelled=stop.is_set, transport_factory=transport_factory), now=clock.now)
        for policy in policies}
    coordinator = Coordinator(providers=providers, normalizer=normalizer or WorkflowNormalizer(),
        comparer=WorkflowComparer(), snapshots=snapshots, store=store, learning=learning,
        clock=clock, max_page_records=max_page_records, max_page_positions=max_page_positions,
        encounter_feedback=encounter_feedback)
    return GameSyncRuntime(FeedbackRunner(coordinator), stop,
                           (provider.acquisition.cancel for provider in providers.values()),admission=admission)
