"""Persist one scheduling turn with the lease it selects."""

from .defensive_analysis import DEFENSIVE_TASK_KINDS
from tempo_legacy_database.adapters.scheduling import durable as storage
from tempo_legacy_database.adapters.scheduling.durable import persist_scheduling_turn


# Interleave the two larger pipelines instead of giving them consecutive bursts.
SCHEDULING_TURNS = ('graph', 'game', 'graph', 'game', 'priority', 'coverage', 'sync')
CLASS_KINDS = {
    'graph': ('opening_graph_rebuild', 'integrity_scan'),
    'game': ('game_derivation_positions', 'game_derivation_compare',
             'game_derivation_findings', 'game_derivation_misses',
             'game_derivation_events', 'game_derivation_features',
             'game_derivation_priorities', 'repertoire_game_refresh',
             'game_analysis_followup'),
    'priority': ('repertoire_priority',
                 'priority_retention', 'discovery_recommendation'),
    'coverage': ('coverage_seed', 'coverage_explorer', 'opening_segmentation'),
    'sync': ('game_sync_record', 'game_sync_window', *DEFENSIVE_TASK_KINDS),
}
# User-dependent publications and controls get prompt, but finite, preference.
CONTROL_KINDS = ('daily_queue', 'prefix_transition_application',
                 'canonical_prefix_preview', 'discovery_admission', 'game_analysis_publish')


def _candidate_sql(kinds, *, promoted=False, control=False):
    return storage.candidate_sql(kinds, DEFENSIVE_TASK_KINDS, promoted=promoted, control=control)


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
    storage.warm_scheduling_sql(allowed_kinds, _selected_classes, DEFENSIVE_TASK_KINDS)


def select_scheduled_task(database, allowed_kinds, now):
    """Return a candidate plus its next turn; the caller commits both with a lease."""
    scheduler = storage.scheduling_turn(database)
    if not scheduler:
        return None, None
    classes, controls, ordinary = _selected_classes(allowed_kinds)
    next_turn = scheduler['next_turn']
    promoted_since_turn = scheduler['promoted_since_turn']
    control_streak = scheduler['control_streak']

    def candidate(kinds, **kwargs):
        if not kinds:
            return None
        return storage.candidate(database, kinds, now, DEFENSIVE_TASK_KINDS, **kwargs)

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
