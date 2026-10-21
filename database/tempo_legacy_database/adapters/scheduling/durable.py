"""Existing durable turn/candidate queries and warming, without turn-selection policy."""
from ..controls.claim_sql import claimable, control_order
from ..defensive_analysis import task_admission_sql


_SCHEDULER_SQL = 'SELECT * FROM background_scheduling_turns WHERE lane=\'durable\''
_TURN_UPDATE_SQL = ('UPDATE background_scheduling_turns SET next_turn=?, '
                    'promoted_since_turn=?,control_streak=? WHERE lane=\'durable\'')


def candidate_sql(kinds, defensive_task_kinds, *, promoted=False, control=False):
    eligibility = f"{claimable('durable', 'background_tasks.id')} AND {task_admission_sql('background_tasks.kind', defensive_task_kinds)}"
    if promoted:
        eligibility += (" AND EXISTS(SELECT 1 FROM background_activity a "
                        "WHERE a.source='durable' AND a.work_id=background_tasks.id AND a.promoted=1)")
    order = (f"priority,{control_order('durable', 'background_tasks.id')}" if control else '')
    return ("SELECT * FROM background_tasks WHERE state IN ('queued','retrying') "
            "AND next_attempt_at<=? AND kind IN (" + ','.join('?' for _ in kinds) + ") AND "
            + eligibility + ' ORDER BY ' + order + 'COALESCE(pending_since,created_at),id LIMIT 1')


def warm_scheduling_sql(allowed_kinds, selected_classes, defensive_task_kinds):
    """Translate before entering the fail-fast background database budget."""
    from tempo_legacy_database.adapters.postgres_store import postgres_sql
    classes, controls, ordinary = selected_classes(allowed_kinds)
    statements = [_SCHEDULER_SQL, _TURN_UPDATE_SQL]
    for kinds in classes.values():
        if kinds:
            statements.append(candidate_sql(kinds, defensive_task_kinds))
    if controls:
        statements.append(candidate_sql(controls, defensive_task_kinds, control=True))
    if ordinary:
        statements.append(candidate_sql(ordinary, defensive_task_kinds, promoted=True))
    for statement in statements:
        postgres_sql(statement)


def scheduling_turn(database):
    if hasattr(database, 'execute_native'):
        return database.execute_native(_SCHEDULER_SQL + ' FOR UPDATE SKIP LOCKED').fetchone()
    return database.execute(_SCHEDULER_SQL).fetchone()


def candidate(database, kinds, now, defensive_task_kinds, **kwargs):
    statement = candidate_sql(kinds, defensive_task_kinds, **kwargs)
    if hasattr(database, 'execute_native'):
        from tempo_legacy_database.adapters.postgres_store import postgres_sql
        return database.execute_native(
            postgres_sql(statement) + ' FOR UPDATE OF background_tasks SKIP LOCKED', (now, *kinds)).fetchone()
    return database.execute(statement, (now, *kinds)).fetchone()


def persist_scheduling_turn(database, turn):
    database.execute(_TURN_UPDATE_SQL, turn)
