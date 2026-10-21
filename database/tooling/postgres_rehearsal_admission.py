"""Bounded admission for direct native proof drivers, never product retry policy."""

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
import time

from app.services.redis_admission_gate import BackgroundAdmissionDeferred


def admitted_call(operation, *arguments):
    """Keep the original claim across expected denial; expose all other errors."""
    deadline = time.monotonic() + 10
    while True:
        try:
            return operation(*arguments)
        except BackgroundAdmissionDeferred as error:
            # A control outage uses the same public exception type. Preserve
            # that failure and its cause instead of disguising it as contention.
            if error.__cause__ is not None or str(error) == 'Foreground admission is unavailable':
                raise
            # The denied context has unwound before yielding. Do not reclaim,
            # change durable state, or extend any database/operation deadline.
            if time.monotonic() >= deadline:
                raise AssertionError('Native rehearsal never obtained admission within 10 seconds') from error
            time.sleep(0.01)
