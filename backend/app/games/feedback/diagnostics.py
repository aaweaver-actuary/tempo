"""Bounded payload-free optional page diagnostics; no runtime owner imports."""
from contextlib import nullcontext
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import time
import traceback

_current = ContextVar('games_page_diagnostics', default=None)
_logger = logging.getLogger('tempo.games.page')


def page_stage(stage, **counts):
    record = _current.get()
    if record is not None:
        record['stage'] = stage
        record.update({key:value for key,value in counts.items() if type(value) is int and value >= 0})


def page_failure(error):
    record = _current.get()
    if record is None:
        return
    state = getattr(error, 'sqlstate', None)
    failure = dict(stage=record['stage'], error_class=type(error).__name__,
        sqlstate=state if isinstance(state,str) and re.fullmatch('[0-9A-Z]{5}',state) else None,
        frames=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                for frame in traceback.extract_tb(error.__traceback__)[-6:]])
    # A swallowed source/compare failure remains visible without an arbitrary
    # exception message, PGN, account, SQL parameters or provider cursor.
    if len(record['failures']) < 4:
        record['failures'].append(failure)


def diagnose_page(function):
    @wraps(function)
    def measured(self, job, *args, **kwargs):
        record = dict(work_token=sha256(job.spec.job_id.encode()).hexdigest()[:16],
            configuration_revision=job.spec.configuration_revision,
            attempt=job.attempts, stage='page_start', failures=[])
        token = _current.set(record)
        started = time.monotonic()
        scope = getattr(self.comparer, 'page_scope', None)
        try:
            with scope() if callable(scope) else nullcontext():
                return function(self, job, *args, **kwargs)
        except BaseException as error:
            page_failure(error)
            raise
        finally:
            record['elapsed_ms'] = round((time.monotonic()-started)*1000,3)
            _current.reset(token)
            try:
                _logger.log(logging.WARNING if record['failures'] else logging.INFO,
                    'games_page %s', json.dumps(record,sort_keys=True))
            except Exception:
                pass  # Diagnostics must never replace the original result/error.
    return measured
