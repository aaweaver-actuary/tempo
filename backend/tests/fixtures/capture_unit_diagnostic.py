"""Private opt-in markers around the existing controlled capture CLI.

Load with runpy AFTER the fixture's R2 guards and database bootstrap, BEFORE
constructing/running the existing worker. No domain, admission or clock changes.
All markers go to stderr; the public CLI stdout document remains unchanged.
"""

from functools import wraps
from pathlib import Path
import json
import os
import resource
import sys
import time

_LIMIT = 256
_started = time.monotonic()
_counts = dict(
    units_started=0,
    units_finished=0,
    pages_started=0,
    pages_finished=0,
    boundaries_started=0,
    boundaries_finished=0,
    revisions_started=0,
    revisions_finished=0,
    stage_calls=0,
)
_events = 0
_active_unit = None
_active_page = None
_stage_counts = {}
_restores = []


def _number(path):
    try:
        value = Path(path).read_text()[:128].strip()
        return int(value) if value.isdecimal() else None
    except (OSError, ValueError):
        return None


def _memory():
    values = {}
    try:
        for line in Path("/proc/self/status").read_text()[:32768].splitlines():
            name, _, value = line.partition(":")
            if name in {"VmRSS", "RssAnon", "RssFile", "RssShmem"}:
                values[name + "_bytes"] = int(value.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    values["process_peak_rss_bytes"] = peak * (1 if sys.platform == "darwin" else 1024)
    values["cgroup_current_bytes"] = _number("/sys/fs/cgroup/memory.current")
    values["cgroup_peak_bytes"] = _number("/sys/fs/cgroup/memory.peak")
    try:
        values["cgroup_events"] = {
            key: int(value)
            for key, value in (
                line.split()
                for line in Path("/sys/fs/cgroup/memory.events")
                .read_text()[:4096]
                .splitlines()
            )
        }
    except (OSError, ValueError):
        pass
    return values


def _emit(event, **fields):
    global _events
    if _events >= _LIMIT:
        return
    try:
        _events += 1
        if _events == _LIMIT:
            event, fields = "marker_limit_reached", {}
        cpu = os.times()
        marker = dict(
            capture_unit_diagnostic=1,
            event=event,
            marker=_events,
            wall_seconds=round(time.monotonic() - _started, 6),
            cpu_seconds=round(cpu.user + cpu.system, 6),
            unit=_active_unit,
            page=_active_page,
            counts=dict(_counts),
            **_memory(),
            **fields,
        )
        print(json.dumps(marker, sort_keys=True), file=sys.stderr, flush=True)
    except Exception:
        # A diagnostic failure cannot replace a domain result or exception.
        pass


def _timed(label, original):
    """Return the identical result; re-raise the identical BaseException."""

    @wraps(original)
    def measured(*args, **kwargs):
        global _active_unit, _active_page, _stage_counts
        previous_unit, previous_page = _active_unit, _active_page
        extra = {}
        if label == "unit":
            _counts["units_started"] += 1
            _active_unit = _counts["units_started"]
        elif label == "page":
            _counts["pages_started"] += 1
            _active_page = _counts["pages_started"]
            _stage_counts = {}
            try:
                job = kwargs.get("job") if "job" in kwargs else args[1]
                spec = job.spec
                extra = dict(
                    stream=spec.stream, purpose=spec.purpose, attempt=job.attempts
                )
            except Exception:
                pass  # Metadata must not change the wrapped call's errors.
        elif label == "first_boundary":
            _counts["boundaries_started"] += 1
            supplied = kwargs.get("snapshots")
            if type(supplied) in (tuple, list):
                extra["snapshot_count"] = len(supplied)
        elif label == "comparison_revision":
            _counts["revisions_started"] += 1
            supplied = args[0] if args else kwargs.get("snapshots")
            if type(supplied) in (tuple, list):
                extra["snapshot_count"] = len(supplied)
        _emit(label + "_start", **extra)
        try:
            value = original(*args, **kwargs)
        except BaseException as error:
            _emit(label + "_raise", error_class=type(error).__name__)
            raise
        else:
            field = {
                "unit": "units_finished",
                "page": "pages_finished",
                "first_boundary": "boundaries_finished",
                "comparison_revision": "revisions_finished",
            }.get(label)
            if field:
                _counts[field] += 1
            end = (
                dict(result=value)
                if label == "unit"
                and type(value) is str
                and value in ("page", "delivery", "idle", "stopped", "busy", "deferred")
                else {}
            )
            _emit(label + "_end", **end)
            return value
        finally:
            if label == "unit":
                _active_unit = previous_unit
            elif label == "page":
                _active_page = previous_page

    return measured


def install():
    """Patch only exact existing execution seams; importing this file is inert."""
    if _restores:
        return restore
    from app.games.feedback import coordinator
    from app.games.feedback.jobs import FeedbackRunner

    def patch(owner, name, value):
        _restores.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    patch(FeedbackRunner, "run_once", _timed("unit", FeedbackRunner.run_once))
    patch(
        coordinator.Coordinator,
        "capture_page",
        _timed("page", coordinator.Coordinator.capture_page),
    )
    patch(
        coordinator,
        "select_first_boundary",
        _timed("first_boundary", coordinator.select_first_boundary),
    )
    patch(
        coordinator,
        "_comparison_revision",
        _timed("comparison_revision", coordinator._comparison_revision),
    )
    from tempo_legacy_database.adapters.authored_repertoires import product_snapshot

    for name, label in (
        ("read_repertoire", "snapshot_full"),
        ("read_authored_scope", "snapshot_authored"),
        ("read_candidate_bindings", "snapshot_candidate"),
    ):
        patch(product_snapshot, name, _timed(label, getattr(product_snapshot, name)))
    _emit("installed")
    return restore


def restore():
    while _restores:
        owner, name, original = _restores.pop()
        setattr(owner, name, original)
