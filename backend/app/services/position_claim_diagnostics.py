"""Opt-in, bounded claim traces; emit only after the owning scope releases SQL."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
import re
import time

import psycopg


_LOGGER = logging.getLogger("tempo.position_claim")
# Uvicorn does not install a root handler. This dedicated sink stays silent
# unless tracing is explicitly enabled, and also works in Celery processes.
_LOGGER.addHandler(logging.StreamHandler())
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False
MAX_TRACE_EVENTS = 96
_current_trace = ContextVar("position_claim_trace", default=None)


def enabled():
    return os.getenv("TEMPO_POSITION_CLAIM_DIAGNOSTICS") == "1"


@dataclass
class ClaimTrace:
    operation_digest: str
    role: str
    events: list[dict] = field(default_factory=list)
    omitted_events: int = 0

    def record(self, stage, seconds, error=None, metadata=None):
        if len(self.events) >= MAX_TRACE_EVENTS:
            self.omitted_events += 1
            return
        event = {"stage": stage, "duration_ms": round(max(0, seconds) * 1000, 3),
                 "outcome": "error" if error is not None else "complete"}
        if error is not None:
            event["exception_class"] = type(error).__name__
            sqlstate = getattr(error, "sqlstate", None)
            if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
                event["sqlstate"] = sqlstate
        for name in ("backend_pid", "transaction_timeout_ms", "lock_timeout_ms", "attempt_number"):
            value = (metadata or {}).get(name)
            if type(value) is int:
                event[name] = value
        receipt_state = (metadata or {}).get("receipt_state")
        if receipt_state in {"unknown", "pending", "queued", "executing", "retrying", "blocked", "complete", "failed"}:
            event["receipt_state"] = receipt_state
        attempt_digest = (metadata or {}).get("attempt_owner_digest")
        if isinstance(attempt_digest, str) and re.fullmatch(r"[a-f0-9]{24}", attempt_digest):
            event["attempt_owner_digest"] = attempt_digest
        self.events.append(event)


@contextmanager
def trace_position_claim(operation_id, role):
    if not enabled() or not operation_id:
        yield
        return
    try:
        trace = ClaimTrace(hashlib.sha256(operation_id.encode()).hexdigest()[:24], role)
    except Exception:
        yield
        return
    token = _current_trace.set(trace)
    try:
        yield
    finally:
        _current_trace.reset(token)
        # Never let diagnostics replace a result or the original exception.
        try:
            _LOGGER.info(json.dumps({"event": "position_claim", "operation_digest": trace.operation_digest,
                                     "role": trace.role, "events": trace.events,
                                     "omitted_events": trace.omitted_events}, separators=(",", ":")))
        except Exception:
            pass


def record_stage(stage, seconds, error=None, **metadata):
    trace = _current_trace.get()
    if trace is not None:
        try:
            trace.record(stage, seconds, error, metadata)
        except Exception:
            pass


def record_attempt_owner(attempt_token, attempt_number):
    if _current_trace.get() is not None:
        try:
            digest = hashlib.sha256(attempt_token.encode()).hexdigest()[:24] if attempt_token else None
            record_stage("attempt_ownership", 0, attempt_number=attempt_number, attempt_owner_digest=digest)
        except Exception:
            pass


@contextmanager
def claim_stage(stage):
    if _current_trace.get() is None:
        yield
        return
    started = time.perf_counter()
    error = None
    try:
        yield
    except BaseException as failure:
        error = failure
        raise
    finally:
        record_stage(stage, time.perf_counter() - started, error)


def statement_stage(statement):
    if not isinstance(statement, str):
        return "other_statement"
    statement = statement.lstrip()
    if "set_config(" in statement:
        return "transaction_settings"
    if "pg_advisory_xact_lock" in statement:
        return "receipt_lock"
    if "operation_receipts" in statement:
        return "receipt_read" if statement.startswith("SELECT") else "receipt_write"
    if "FROM game_analysis_jobs" in statement and "moves_json" in statement:
        return "candidate_read"
    if "game_analysis_position_reports" in statement:
        return "position_read" if statement.startswith("SELECT") else "position_write"
    if "engine_scheduling_state" in statement:
        return "engine_turn"
    if "game_analysis_jobs" in statement:
        return "parent_read" if statement.startswith("SELECT") else "parent_write"
    if "imported_games" in statement:
        return "game_state"
    return "other_statement"


class ClaimDiagnosticCursor(psycopg.Cursor):
    def execute(self, query, params=None, *, prepare=None, binary=None):
        with claim_stage(statement_stage(query)):
            return super().execute(query, params, prepare=prepare, binary=binary)


def cursor_options():
    return {"cursor_factory": ClaimDiagnosticCursor} if enabled() else {}
