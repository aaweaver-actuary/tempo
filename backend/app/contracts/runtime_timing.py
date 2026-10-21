"""Neutral timing context shared by runtime instrumentation and storage observers.

The application owns measurement scopes, labels, stages, clocks and publication.
The existing None default means no active measurement; it is not admission policy.
"""
from contextvars import ContextVar


# Bounded spans extend existing diagnostics; disable explicitly for paired probes.
_training_timing: ContextVar[dict | None] = ContextVar("training_timing", default=None)


def current_training_timing():
    return _training_timing.get()
