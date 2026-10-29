"""Explicit forward-window scheduling over the existing durable Games jobs.

No clock, timer, provider, database or receiving capability is created here.
Existing fixed-window recurrence remains fixed unless this owner is selected.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from .review import ReviewUnavailable
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


class ContinuousSync:
    """One bounded scheduling pass; existing Games storage owns every mutation."""
    def __init__(self, *, workspace, store, policy, build_job, now):
        self.workspace,self.store,self.policy=workspace,store,policy
        self.build_job,self.now=build_job,now

    def enqueue_due(self):
        now=self.now()
        enqueued=0
        for configuration in self.store.due_configurations(self.workspace,now):
            previous=self.store.last_job(self.workspace,configuration.provider)
            job=self.policy.next_job(configuration,previous,now=now,build_job=self.build_job)
            if job is not None:
                self.store.enqueue(configuration,job,now)
                enqueued+=1
        return bool(enqueued)
