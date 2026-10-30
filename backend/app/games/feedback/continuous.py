"""Explicit forward-window scheduling over the existing durable Games jobs.

No clock, timer, provider, database or receiving capability is created here.
Existing fixed-window recurrence remains fixed unless this owner is selected.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from typing import Literal, Protocol

from .ports import ConcurrentUpdate, JobSpec, JobState
from .review import ReviewUnavailable, SyncConfiguration
from app.contracts.product_v02.games import GameWindow


def _stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


@dataclass(frozen=True)
class ForwardWindow:
    overlap_seconds: int

    def __post_init__(self):
        if type(self.overlap_seconds) is not int or not 0 <= self.overlap_seconds <= 3600:
            raise ValueError("explicit_bounded_overlap_required")

    def next_job(self, configuration, previous, *, now, build_job):
        """Catch up consecutive bounded windows; never jump an outage gap.

        A noncomplete job owns its original continuation and retry time. No new
        window replaces it. Stable identities make concurrent/restarted triggers
        request the same job. Provider policy/account/repertoire remain explicit.
        """
        interval=configuration.interval_seconds
        if interval is None:
            return None
        start,end=_stamp(configuration.window.start_at),_stamp(configuration.window.end_at)
        width=end-start
        if not 60 <= width <= 86400 or self.overlap_seconds >= width:
            raise ReviewUnavailable("continuous_window_must_be_60_seconds_to_one_day_with_smaller_overlap")
        prefix=f"following:{configuration.provider}:{configuration.revision}:"
        if previous is not None and (previous.status!="complete" or not previous.window_complete):
            return None
        if previous is not None and previous.spec.job_id.startswith(prefix):
            expected=build_job(configuration,previous.spec.job_id)
            fields=("workspace_id","provider","account","repertoire_ids","filters")
            if any(getattr(previous.spec,field)!=getattr(expected,field) for field in fields):
                raise ReviewUnavailable("continuous_previous_scope_changed")
            # The first successfully completed, same-revision window determines
            # the next contiguous scope. Delays never silently skip old time.
            covered=_stamp(previous.spec.window.end_at)
            start=max(start,covered-self.overlap_seconds)
            end=min(covered+width-self.overlap_seconds,(now//interval)*interval)
            if end<=covered:
                return None
        elif end>now:
            return None
        window=GameWindow(start_at=_utc(start),end_at=_utc(end),precision="second")
        identity=hashlib.sha256(json.dumps([configuration.workspace_id,configuration.provider,
            configuration.account.casefold(),configuration.revision,window.model_dump(mode="json"),
            self.overlap_seconds],sort_keys=True,separators=(",", ":")).encode()).hexdigest()
        selected=configuration.model_copy(update={"window":window})
        return build_job(selected,prefix+identity)


Purpose = Literal["forward", "reconciliation"]


def stream_prefix(configuration: SyncConfiguration, purpose: Purpose) -> str:
    if purpose not in ("forward", "reconciliation"):
        raise ValueError("unknown_continuous_purpose")
    name = "following" if purpose == "forward" else "reconciling"
    return f"{name}:{configuration.provider}:{configuration.revision}:"


@dataclass(frozen=True)
class ContinuousProviderState:
    configuration: SyncConfiguration
    forward_due: bool
    forward_job: JobState | None
    reconciliation_job: JobState | None


class ContinuousStore(Protocol):
    def continuous_state(self, workspace: str, now: float) -> tuple[ContinuousProviderState, ...]: ...
    def enqueue_continuous(self, configuration: SyncConfiguration, job: JobSpec, now: float, *,
                           purpose: Purpose, expected_previous: JobState | None) -> JobState: ...


def _validate_scope(configuration, previous, build_job):
    if previous is None:
        return
    expected = build_job(configuration, previous.spec.job_id)
    fields = ("workspace_id", "provider", "account", "repertoire_ids", "filters")
    if any(getattr(previous.spec, field) != getattr(expected, field) for field in fields):
        raise ReviewUnavailable("continuous_previous_scope_changed")


class ContinuousSync:
    """One bounded scheduling pass; existing Games storage owns every mutation."""
    def __init__(self, *, workspace, store: ContinuousStore, policy, build_job, now,
                 reconciliation_interval_seconds: int | None = None):
        if reconciliation_interval_seconds is not None and (
                type(reconciliation_interval_seconds) is not int or reconciliation_interval_seconds < 60):
            raise ValueError("reconciliation_interval_must_be_integer_at_least_60")
        self.workspace, self.store, self.policy = workspace, store, policy
        self.build_job, self.now = build_job, now
        self.reconciliation_interval_seconds = reconciliation_interval_seconds
        self.errors: dict[tuple[str, Purpose], str] = {}

    def _reconciliation_job(self, configuration, previous, now):
        interval = self.reconciliation_interval_seconds
        if interval is None:
            return None
        _validate_scope(configuration, previous, self.build_job)
        if previous is not None:
            if previous.status != "complete" or not previous.window_complete:
                return None
            # Completion, rather than enqueue time, spaces full-history scans.
            if previous.last_checkpoint_at is None:
                raise ReviewUnavailable("reconciliation_completion_time_required")
            if now < _stamp(previous.last_checkpoint_at) + interval:
                return None
        start, end = _stamp(configuration.window.start_at), int(now)
        if end <= start:
            return None
        window = GameWindow(start_at=_utc(start), end_at=_utc(end), precision="second")
        selected = configuration.model_copy(update={"window": window})
        job = self.build_job(selected, "reconciliation-scope")
        identity = hashlib.sha256(job.model_dump_json().encode()).hexdigest()
        return self.build_job(selected, stream_prefix(configuration, "reconciliation") + identity)

    def enqueue_due(self):
        now = self.now()
        enqueued = False
        self.errors = {}
        for state in self.store.continuous_state(self.workspace, now):
            configuration = state.configuration
            for purpose, previous in (("forward", state.forward_job),
                                      ("reconciliation", state.reconciliation_job)):
                try:
                    if purpose == "forward":
                        if not state.forward_due:
                            continue
                        _validate_scope(configuration, previous, self.build_job)
                        job = self.policy.next_job(configuration, previous, now=now, build_job=self.build_job)
                    else:
                        job = self._reconciliation_job(configuration, previous, now)
                    if job is not None:
                        self.store.enqueue_continuous(configuration, job, now, purpose=purpose,
                                                      expected_previous=previous)
                        enqueued = True
                except ConcurrentUpdate:
                    continue  # A competing enqueue/page won; re-read next scheduling pass.
                except ReviewUnavailable as error:
                    self.errors[(configuration.provider, purpose)] = error.reason
        return enqueued
