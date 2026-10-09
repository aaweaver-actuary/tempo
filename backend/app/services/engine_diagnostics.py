"""Engine counters are accepted once with the existing fenced callback."""
from ..models import EngineAttemptDiagnostics
from .background_metrics import increment


def record_engine_outcome(database, kind, identity, payload, *, completed=False):
    diagnostics = payload.get("diagnostics")
    counts = {}
    diagnostic=None
    if completed:
        counts.update(engine_completed_positions=1, useful_completions=1)
    if diagnostics is not None:
        diagnostic = EngineAttemptDiagnostics.model_validate(diagnostics)
        expected = "success" if completed else diagnostic.outcome
        if not completed and diagnostic.outcome == "success":
            raise ValueError("Engine release/failure cannot report a successful search")
        if completed and diagnostic.outcome != "success":
            raise ValueError("Engine report requires a successful outcome")
        if expected == 'success':
            counts.update(engine_successful_seconds=diagnostic.elapsed_seconds,engine_successful_max_seconds=diagnostic.elapsed_seconds,engine_successful_samples=1)
        else:
            counts.update(engine_abandoned_seconds=diagnostic.elapsed_seconds,engine_abandoned_max_seconds=diagnostic.elapsed_seconds,engine_abandoned_samples=1)
        if expected == "preempted":
            counts['engine_preempted_seconds'] = diagnostic.elapsed_seconds
            counts['engine_preempted_max_seconds'] = diagnostic.elapsed_seconds
            counts['engine_preemptions'] = 1
            if payload.get('error'):
                counts['engine_failures'] = 1
        elif expected == "timeout":
            counts['engine_timeouts'] = 1
        elif expected == "failure":
            counts['engine_failures'] = 1
    if not completed and diagnostics is None and payload.get("error"):
        counts["engine_failures"] = 1
    if diagnostics is None:
        counts["engine_unknown_timing_attempts"] = 1
    from .activity_health import record_engine_execution_in_transaction
    record_engine_execution_in_transaction(database,kind,identity,completed=completed,diagnostics=diagnostic)
    if counts:
        increment(database, kind, identity, **counts)
