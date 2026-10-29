"""Bounded, payload-free diagnostics for the independent Study feedback sweep."""
from contextvars import ContextVar
from functools import wraps
import json
import logging
from pathlib import Path
import time
import traceback

from app.contracts.runtime_timing import _training_timing

_current = ContextVar('feedback_diagnostic', default=None)
_logger = logging.getLogger('tempo.study.feedback')


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
        record.update(failure_stage=record['stage'], error_class=type(error).__name__,
            sqlstate=getattr(error, 'sqlstate', None),
            frames=[{'file':Path(frame.filename).name, 'line':frame.lineno, 'function':frame.name}
                    for frame in traceback.extract_tb(error.__traceback__)[-8:]])


def diagnose_feedback(function):
    @wraps(function)
    def measured(*args, **kwargs):
        record = {'stage':'claim', 'stage_started':time.perf_counter(), 'stages_ms':{}}
        sql = {'sql_count':0, 'sql_ms':0, 'connections':[]}
        token = _current.set(record)
        timing_token = _training_timing.set(sql)
        try:
            result = function(*args, **kwargs)
            record['result'] = result
            return result
        except Exception as error:
            feedback_failure(error)
            raise
        finally:
            feedback_stage('finished')
            record.pop('stage_started')
            record.update(sql_count=sql['sql_count'], sql_ms=round(sql['sql_ms'],3),
                          slow_sql=sql.get('slow_sql', []), connections=sql['connections'][:16])
            _training_timing.reset(timing_token)
            _current.reset(token)
            _logger.info('study_feedback_diagnostic %s', json.dumps(record, sort_keys=True))
    return measured
