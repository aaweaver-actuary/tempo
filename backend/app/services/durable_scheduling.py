"""Persist one scheduling turn with the lease it selects."""

from .background_activity import claimable, control_order
from .defensive_analysis import DEFENSIVE_TASK_KINDS, task_admission_sql


# Interleave the two larger pipelines instead of giving them consecutive bursts.
SCHEDULING_TURNS = ('graph', 'game', 'graph', 'game', 'priority', 'coverage', 'sync')
CLASS_KINDS = {
    'graph': ('opening_graph_rebuild', 'integrity_scan'),
    'game': ('game_derivation_positions', 'game_derivation_compare',
             'game_derivation_findings', 'game_derivation_misses',
             'game_derivation_events', 'game_derivation_features',
             'game_derivation_priorities', 'repertoire_game_refresh',
             'game_analysis_followup'),
    'priority': ('repertoire_priority', 'repertoire_opportunity',
                 'priority_retention', 'daily_statistics', 'discovery_recommendation'),
    'coverage': ('coverage_seed', 'coverage_explorer', 'opening_segmentation'),
    'sync': ('game_sync_record', 'game_sync_window', *DEFENSIVE_TASK_KINDS),
}
# User-dependent publications and controls get prompt, but finite, preference.
CONTROL_KINDS = ('daily_queue', 'prefix_transition_application',
                 'canonical_prefix_preview', 'discovery_admission', 'game_analysis_publish')
_SCHEDULER_SQL = 'SELECT * FROM background_scheduling_turns WHERE lane=\'durable\''
_TURN_UPDATE_SQL = ('UPDATE background_scheduling_turns SET next_turn=?, '
                    'promoted_since_turn=?,control_streak=? WHERE lane=\'durable\'')


def _candidate_sql(kinds, *, promoted=False, control=False):
    eligibility = f"{claimable('durable', 'background_tasks.id')} AND {task_admission_sql('background_tasks.kind')}"
    if promoted:
        eligibility += (" AND EXISTS(SELECT 1 FROM background_activity a "
                        "WHERE a.source='durable' AND a.work_id=background_tasks.id AND a.promoted=1)")
    order = (f"priority,{control_order('durable', 'background_tasks.id')}" if control else '')
    return ("SELECT * FROM background_tasks WHERE state IN ('queued','retrying') "
            "AND next_attempt_at<=? AND kind IN (" + ','.join('?' for _ in kinds) + ") AND "
            + eligibility + ' ORDER BY ' + order + 'COALESCE(pending_since,created_at),id LIMIT 1')


def _selected_classes(allowed_kinds):
    allowed = set(allowed_kinds)
    classes = {name: tuple(kind for kind in kinds if kind in allowed)
               for name, kinds in CLASS_KINDS.items()}
    known = set(CONTROL_KINDS).union(*(set(kinds) for kinds in CLASS_KINDS.values()))
    # A newly supported handler still gets bounded turns until assigned explicitly.
    classes['sync'] += tuple(kind for kind in allowed_kinds if kind not in known)
    controls = tuple(kind for kind in CONTROL_KINDS if kind in allowed)
    ordinary = tuple(kind for kinds in classes.values() for kind in kinds)
    return classes, controls, ordinary


def warm_scheduling_sql(allowed_kinds):
    """Translate before entering the fail-fast background database budget."""
    from ..postgres_store import postgres_sql
    classes, controls, ordinary = _selected_classes(allowed_kinds)
    statements = [_SCHEDULER_SQL, _TURN_UPDATE_SQL]
    for kinds in classes.values():
        if kinds:
            statements.append(_candidate_sql(kinds))
    if controls:
        statements.append(_candidate_sql(controls, control=True))
    if ordinary:
        statements.append(_candidate_sql(ordinary, promoted=True))
    for statement in statements:
        postgres_sql(statement)


def select_scheduled_task(database, allowed_kinds, now):
    """Return a candidate plus its next turn; the caller commits both with a lease."""
    if hasattr(database, 'execute_native'):
        scheduler = database.execute_native(_SCHEDULER_SQL + ' FOR UPDATE SKIP LOCKED').fetchone()
    else:
        scheduler = database.execute(_SCHEDULER_SQL).fetchone()
    if not scheduler:
        return None, None
    classes, controls, ordinary = _selected_classes(allowed_kinds)
    next_turn = scheduler['next_turn']
    promoted_since_turn = scheduler['promoted_since_turn']
    control_streak = scheduler['control_streak']

    def candidate(kinds, **kwargs):
        if not kinds:
            return None
        statement = _candidate_sql(kinds, **kwargs)
        if hasattr(database, 'execute_native'):
            from ..postgres_store import postgres_sql
            return database.execute_native(
                postgres_sql(statement) + ' FOR UPDATE OF background_tasks SKIP LOCKED', (now, *kinds)).fetchone()
        return database.execute(statement, (now, *kinds)).fetchone()

    if control_streak < 2:
        row = candidate(controls, control=True)
        if row:
            return row, (next_turn, promoted_since_turn, control_streak + 1)
    if not promoted_since_turn:
        row = candidate(ordinary, promoted=True)
        if row:
            return row, (next_turn, 1, 0)
    for offset in range(len(SCHEDULING_TURNS)):
        selected_turn = (next_turn + offset) % len(SCHEDULING_TURNS)
        row = candidate(classes[SCHEDULING_TURNS[selected_turn]])
        if row:
            return row, ((selected_turn + 1) % len(SCHEDULING_TURNS), 0, 0)
    row = candidate(controls, control=True)
    return (row, (next_turn, promoted_since_turn, 2)) if row else (None, None)


def persist_scheduling_turn(database, turn):
    database.execute(_TURN_UPDATE_SQL, turn)
