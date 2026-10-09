"""Restart-safe 3:1 automated/ordinary engine selection, within claim transactions."""

_ORDINARY_GAME = (
    "SELECT 1 FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
    "LEFT JOIN background_activity control ON control.source='game_analysis' AND control.work_id=j.game_id "
    "WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<?)) "
    "AND g.rated=1 AND g.speed IN ('blitz','rapid','classical') "
    "AND COALESCE(control.paused,0)=0 LIMIT 1"
)


def _execute(database, statement, parameters=()):
    if hasattr(database, 'execute_native'):
        return database.execute_native(statement.replace('?', '%s'), parameters)
    return database.execute(statement, parameters)


def admit_automated_selection(database, now):
    """Reserve an ordinary turn only when such work is currently eligible.

    The caller already selected one automated request. Interactive attempts
    bypass this discretionary turn and never consume its streak.
    """
    lock_suffix = ' FOR UPDATE SKIP LOCKED' if hasattr(database, 'execute_native') else ''
    scheduling_row = _execute(database,
        'SELECT automated_streak FROM engine_scheduling_state WHERE id=1' + lock_suffix).fetchone()
    if scheduling_row is None:
        return False  # Another claim owns the turn; do not wait for capacity.
    if scheduling_row[0] >= 3 and _execute(database, _ORDINARY_GAME, (now,)).fetchone():
        return False
    _execute(database, 'UPDATE engine_scheduling_state SET automated_streak=MIN(3,automated_streak+1) WHERE id=1'
             if not hasattr(database, 'execute_native') else
             'UPDATE engine_scheduling_state SET automated_streak=LEAST(3,automated_streak+1) WHERE id=1')
    return True


def record_ordinary_selection(database):
    _execute(database, 'UPDATE engine_scheduling_state SET automated_streak=0 WHERE id=1 AND automated_streak<>0')
