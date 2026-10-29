"""Only a newly accepted explicit PGN's graph/integrity may run in Study-only.

This is a receipt-bound filter on existing durable slices, not another scheduler.
It creates no grants, connection, task kind, or independent task-state writer.
"""
import json

from tempo_legacy_database.adapters.authored_repertoires.pgn_import import preparation as storage
from tempo_legacy_database.connections import background_read_connection
from .redis_admission_gate import BackgroundAdmissionDeferred

COMMAND = 'imports.pgn.explicit'


def permitted_delivery(task, database=None):
    if task.get('kind') not in {'opening_graph_rebuild','integrity_scan'}:
        return False
    origin=task.get('payload',{}).get('origin_import_operation_id')
    if not isinstance(origin,str) or not 1<=len(origin)<=128:
        return False
    if database is None:
        with background_read_connection(authoritative=True) as reader:
            return permitted_delivery(task, reader)
    row = storage.delivery(database, task['id'], task['generation'], task['lease_token'])
    return bool(row and row['kind'] == task['kind'] and row['phase'] == task['phase']
                and json.loads(row['payload_json']) == task['payload'])


def require_delivery(database, task):
    if not permitted_delivery(task,database):
        raise BackgroundAdmissionDeferred('Explicit import preparation is paused or its receipt/task scope changed')
