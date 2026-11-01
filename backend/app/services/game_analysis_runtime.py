"""Explicit execution-time boundary for optional game engine analysis.

HTTP and command registration retain their names while core Study bootstrap
never imports the analysis worker or constructs its optional engine machinery.
"""
from importlib import import_module

from fastapi import HTTPException


def _worker():
    try:
        return import_module("app.services.game_analysis_worker")
    except ImportError as error:
        raise HTTPException(503, "Optional game analysis is unavailable; ordinary Study remains available") from error


def evidence_version():
    return _worker().EVIDENCE_VERSION


def build_game_evaluations(*args, **kwargs):
    return _worker().build_game_evaluations(*args, **kwargs)


def claim_position(*args, **kwargs):
    return _worker().claim_position(*args, **kwargs)


def release_position(*args, **kwargs):
    return _worker().release_position(*args, **kwargs)


def save_position_report(*args, **kwargs):
    return _worker().save_position_report(*args, **kwargs)


def _confirmed_indices(*args, **kwargs):
    return _worker()._confirmed_indices(*args, **kwargs)


def _positions(*args, **kwargs):
    return _worker()._positions(*args, **kwargs)
