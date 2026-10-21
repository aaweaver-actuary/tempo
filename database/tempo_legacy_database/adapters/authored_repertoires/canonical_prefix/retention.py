"""Existing bounded-retention SQL, borrowing the application transaction."""

from ... import postgres_store


_RETENTION_VICTIMS_SQL = (
    'SELECT id,state FROM canonical_prefix_previews WHERE repertoire_id=? AND id<>? '
    'AND id NOT IN (SELECT id FROM canonical_prefix_previews WHERE repertoire_id=? '
    'ORDER BY created_at DESC,id DESC LIMIT ?) '
    "AND id<>COALESCE((SELECT canonical_prefix_preview_id FROM repertoires WHERE id=?),'') "
    'ORDER BY created_at,id LIMIT ?'
)
_RETENTION_TASK_SQL = "SELECT id,state FROM background_tasks WHERE kind='canonical_prefix_preview' AND deduplication_key=?"
_RETIRE_TASK_SQL = (
    "UPDATE background_tasks SET state='superseded',generation=generation+1,"
    "lease_token=NULL,lease_expires_at=NULL WHERE id=? AND state<>'superseded'"
)
_INVALIDATE_PREVIEW_SQL = "UPDATE canonical_prefix_previews SET state='stale',last_error=? WHERE id=? AND state<>'stale'"
_DELETE_RESULTS_SQL = (
    'DELETE FROM canonical_prefix_results WHERE preview_id=? AND item_id IN '
    '(SELECT item_id FROM canonical_prefix_results WHERE preview_id=? ORDER BY item_id LIMIT ?)'
)
_DELETE_POSITIONS_SQL = (
    'DELETE FROM canonical_prefix_positions WHERE preview_id=? AND (fen_key,in_scope) IN '
    '(SELECT fen_key,in_scope FROM canonical_prefix_positions WHERE preview_id=? ORDER BY fen_key,in_scope LIMIT ?)'
)
_DELETE_EVENTS_SQL = (
    'DELETE FROM background_task_events WHERE task_id=? AND id IN '
    '(SELECT id FROM background_task_events WHERE task_id=? ORDER BY id LIMIT ?)'
)
_DELETE_RETIRED_TASK_SQL = (
    "DELETE FROM background_tasks WHERE id=? AND state='superseded' "
    'AND NOT EXISTS(SELECT 1 FROM background_task_events WHERE task_id=background_tasks.id)'
)
_DELETE_EMPTY_PREVIEW_SQL = (
    "DELETE FROM canonical_prefix_previews WHERE id=? AND state='stale' "
    'AND NOT EXISTS(SELECT 1 FROM canonical_prefix_results WHERE preview_id=canonical_prefix_previews.id) '
    'AND NOT EXISTS(SELECT 1 FROM canonical_prefix_positions WHERE preview_id=canonical_prefix_previews.id) '
    "AND NOT EXISTS(SELECT 1 FROM background_tasks WHERE kind='canonical_prefix_preview' "
    'AND deduplication_key=canonical_prefix_previews.id)'
)


def warm_queries(warm_completion_sql):
    if postgres_store.configured():
        for statement in (_RETENTION_VICTIMS_SQL, _RETENTION_TASK_SQL + ' FOR UPDATE',
                          _RETIRE_TASK_SQL, _INVALIDATE_PREVIEW_SQL, _DELETE_RESULTS_SQL,
                          _DELETE_POSITIONS_SQL, _DELETE_EVENTS_SQL,
                          _DELETE_RETIRED_TASK_SQL, _DELETE_EMPTY_PREVIEW_SQL):
            postgres_store.postgres_sql(statement)
        warm_completion_sql()


def retention_task(database, preview_id):
    task_lock = ' FOR UPDATE' if hasattr(database, 'execute_native') else ''
    return database.execute(_RETENTION_TASK_SQL + task_lock, (preview_id,)).fetchone()


def retire_task(database, task_id):
    return database.execute(_RETIRE_TASK_SQL, (task_id,)).rowcount


def invalidate_preview(database, error, preview_id):
    return database.execute(_INVALIDATE_PREVIEW_SQL, (error, preview_id)).rowcount


def delete_results(database, parent_id, remaining_rows):
    return database.execute(_DELETE_RESULTS_SQL, (parent_id, parent_id, remaining_rows)).rowcount


def delete_positions(database, parent_id, remaining_rows):
    return database.execute(_DELETE_POSITIONS_SQL, (parent_id, parent_id, remaining_rows)).rowcount


def delete_events(database, parent_id, remaining_rows):
    return database.execute(_DELETE_EVENTS_SQL, (parent_id, parent_id, remaining_rows)).rowcount


def delete_retired_task(database, task_id):
    return database.execute(_DELETE_RETIRED_TASK_SQL, (task_id,)).rowcount


def delete_empty_preview(database, preview_id):
    return database.execute(_DELETE_EMPTY_PREVIEW_SQL, (preview_id,)).rowcount


def retention_victims(database, parameters):
    return database.execute(_RETENTION_VICTIMS_SQL, parameters).fetchall()


def retention_victim(database, parameters):
    return database.execute(_RETENTION_VICTIMS_SQL, parameters).fetchone()
