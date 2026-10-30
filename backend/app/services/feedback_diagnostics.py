"""Bounded, payload-free diagnostics for the independent Study feedback sweep."""
from collections.abc import Mapping
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from hashlib import sha256
import json
import logging
import re
from pathlib import Path
import time
import traceback

from app.contracts.runtime_timing import _training_timing

_current = ContextVar('feedback_diagnostic', default=None)
_logger = logging.getLogger('tempo.study.feedback')

# These are fixed public owner classifications, never exception text. Unknown
# reasons deliberately stay redacted even when they happen to look like codes.
_REMEDIATION_REASONS = frozenset({
    'source_authority_mismatch', 'source_revision_mismatch', 'batch_limit_exceeded',
    'legacy_capture_out_of_range', 'legacy_capture_in_future', 'occurrence_out_of_range',
    'occurrence_in_future', 'ambiguous_obligation_linkage', 'legacy_authority_variant_required',
    'legacy_card_not_introduced', 'legacy_introduction_chronology_unknown',
    'legacy_physical_identity_required', 'legacy_binding_authority_changed',
    'unknown_is_not_permission', 'legacy_decision_scope_changed', 'legacy_route_policy_ambiguous',
    'legacy_authority_not_current', 'publication_not_active', 'accepted_workspace_mismatch',
    'unsupported_card_linkage', 'accepted_definition_mismatch', 'unsupported_decision_linkage',
    'incoherent_comparison', 'unsupported_miss_provenance',
})


def feedback_work(identity, version=None):
    record = _current.get()
    if record is not None:
        record['work_token'] = sha256(identity.encode()).hexdigest()[:16]
        if type(version) is int:
            record['work_version'] = version


def feedback_preparation_work(work):
    """Record fixed numeric work facts only; never serialize the owner value."""
    record = _current.get()
    if record is None or not isinstance(work, Mapping):
        return
    fields = ('input_plies', 'prefix_nodes', 'replayed_nodes',
              'projected_frame_records', 'accounting_batches')
    limits = ('work_limit_plies', 'accounting_interval_nodes')
    if (any(type(work.get(field)) is not int or work[field] < 0 for field in fields)
            or any(type(work.get(field)) is not int or work[field] <= 0 for field in limits)):
        return
    values = record.setdefault('preparation_work', dict(repertoires=0,
        max_repertoire_input_plies=0, **{field: 0 for field in fields}))
    values['repertoires'] += 1
    values['max_repertoire_input_plies'] = max(values['max_repertoire_input_plies'], work['input_plies'])
    for field in fields:
        values[field] += work[field]
    for field in limits:
        values[field] = work[field]


@contextmanager
def feedback_span(name):
    """Accumulate repeated inner stages without replacing the outer sweep stage."""
    record = _current.get()
    if record is None:
        yield
        return
    previous = record.get('inner_stage')
    record['inner_stage'] = name
    started = time.perf_counter()
    try:
        yield
    except Exception as error:
        feedback_failure(error)
        raise
    finally:
        spans = record.setdefault('spans', {})
        if name in spans or len(spans) < 32:
            span = spans.setdefault(name, {'count': 0, 'elapsed_ms': 0})
            span['count'] += 1
            span['elapsed_ms'] = round(span['elapsed_ms'] + (time.perf_counter()-started)*1000, 3)
        if previous is None:
            record.pop('inner_stage', None)
        else:
            record['inner_stage'] = previous


def feedback_stage(name, task=None):
    record = _current.get()
    if record is None:
        return
    now = time.perf_counter()
    record['stages_ms'][record['stage']] = round((now - record['stage_started']) * 1000, 3)
    record.update(stage=name, stage_started=now)
    if task is not None:
        record.update(task_id=task['id'], generation=task['generation'], phase=task.get('phase'))


def feedback_failure(error):
    record = _current.get()
    if record is not None:
        if 'error_class' in record:
            record['recovery_error_class'] = type(error).__name__
            return
        sqlstate = getattr(error, 'sqlstate', None)
        record.update(failure_stage=record.get('inner_stage', record['stage']), error_class=type(error).__name__,
            sqlstate=sqlstate if isinstance(sqlstate,str) and re.fullmatch(r'[0-9A-Z]{5}',sqlstate) else None,
            frames=[{'file':Path(frame.filename).name, 'line':frame.lineno, 'function':frame.name}
                    for frame in traceback.extract_tb(error.__traceback__)[-8:]])
        from tempo_learning_remediation import RemediationError
        if isinstance(error, RemediationError):
            if error.reason in _REMEDIATION_REASONS:
                record['error_reason'] = error.reason
            if error.status in {'terminal_failure', 'stale_revision'}:
                record['error_status'] = error.status


def diagnose_feedback(function):
    @wraps(function)
    def measured(*args, **kwargs):
        if _current.get() is not None:
            with feedback_span(function.__name__):
                return function(*args, **kwargs)
        record = {'stage':'claim', 'stage_started':time.perf_counter(), 'stages_ms':{}}
        record['operation'] = function.__name__
        # A foreground command already owns this context. Reuse its counters so
        # the command's full SQL cost still includes preparation, while this
        # diagnostic reports only its own count/time/connection deltas.
        sql = _training_timing.get()
        if sql is None:
            sql = {'sql_count':0, 'sql_ms':0, 'connections':[]}
        before_count, before_ms = sql.get('sql_count', 0), sql.get('sql_ms', 0)
        before_connections = len(sql.get('connections', ()))
        before_slow_sql = len(sql.get('slow_sql', ()))
        token = _current.set(record)
        timing_token = _training_timing.set(sql)
        try:
            result = function(*args, **kwargs)
            # Preparation returns opaque owner objects. Never serialize their
            # commands/captures, and never let diagnostics change that result.
            if result is None or type(result) in {bool, int, float}:
                record['result'] = result
            return result
        except Exception as error:
            feedback_failure(error)
            raise
        finally:
            feedback_stage('finished')
            record.pop('stage_started')
            record.update(sql_count=sql.get('sql_count', 0)-before_count,
                          sql_ms=round(sql.get('sql_ms', 0)-before_ms,3),
                          slow_sql=sql.get('slow_sql', [])[before_slow_sql:],
                          connections=sql.get('connections', [])[before_connections:before_connections+16])
            _training_timing.reset(timing_token)
            _current.reset(token)
            _logger.info('study_feedback_diagnostic %s', json.dumps(record, sort_keys=True))
    return measured
