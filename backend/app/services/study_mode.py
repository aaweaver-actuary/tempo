"""Durable installation-wide admission policy for emergency study isolation."""

from ..database import read_connection, background_read_connection
from .activity_gate import activity_gate
from contextlib import nullcontext
from fastapi import HTTPException


STUDY_TASK_KINDS = ('daily_queue',)
PAUSED_IMPORT_COMMANDS = frozenset({'imports.pgn.admit', 'studies.import.commit', 'studies.bundle.import'})
STUDY_BACKGROUND_COMMANDS = frozenset({
    'opening_evidence.checkpoint',
    'coverage.maia.heartbeat', 'coverage.maia.release', 'coverage.maia.failure',
    'threat.analysis.failure', 'threat.analysis.release',
    'games.analysis.position.report',
    'games.analysis.position.release', 'games.analysis.failure',
    'games.analysis.heartbeat', 'games.analysis.release', 'games.analysis.finalize.admit',
    'activity.progress',
})


def study_only_enabled(database=None) -> bool:
    if database is None:
        reader = background_read_connection if activity_gate.in_background else read_connection
        with (activity_gate.background_control() if activity_gate.in_background else nullcontext()), reader() as settings_database:
            return study_only_enabled(settings_database)
    setting = database.execute('SELECT study_only_mode FROM settings WHERE id=1').fetchone()
    if setting is None:
        raise HTTPException(503, 'Study mode settings are unavailable; restore the database and retry')
    return bool(setting[0])


def optional_work_paused(database=None) -> bool:
    """Emergency pause overrides the independent optional server-work control."""
    if study_only_enabled(database):
        return True
    if database is None:
        reader = background_read_connection if activity_gate.in_background else read_connection
        with (activity_gate.background_control() if activity_gate.in_background else nullcontext()), reader() as settings_database:
            return optional_work_paused(settings_database)
    setting = database.execute('SELECT optional_analysis_and_imports_enabled FROM settings WHERE id=1').fetchone()
    if setting is None:
        raise HTTPException(503, 'Optional work settings are unavailable; restore the database and retry')
    return not bool(setting[0])
