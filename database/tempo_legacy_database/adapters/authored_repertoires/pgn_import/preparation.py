"""Fixed receipt/task provenance predicate; durable_tasks remains sole lease writer."""
from ...controls.claim_sql import claimable

COMMAND = 'imports.pgn.explicit'


def receipt_scope(task='background_tasks'):
    # Only fixed internal aliases are accepted, never user SQL.
    if task not in {'background_tasks','task'}: raise ValueError('Unknown import task alias')
    return f"""{task}.kind IN ('opening_graph_rebuild','integrity_scan') AND EXISTS (
        SELECT 1 FROM operation_receipts receipt
        JOIN background_tasks graph ON receipt.operation_id=(graph.payload_json::jsonb->>'origin_import_operation_id')
        WHERE receipt.command_name='{COMMAND}' AND receipt.state='complete'
          AND graph.kind='opening_graph_rebuild' AND graph.deduplication_key={task}.deduplication_key
          AND graph.id=(receipt.response_json::jsonb->'preparation'->>'graph_task_id')
          AND graph.generation::text=(receipt.response_json::jsonb->'preparation'->>'graph_generation')
          AND graph.deduplication_key=(receipt.response_json::jsonb->>'repertoire_id')
          AND (graph.payload_json::jsonb->>'repertoire_id')=graph.deduplication_key
          AND ({task}.payload_json::jsonb->>'repertoire_id')=graph.deduplication_key
          AND ({task}.id=graph.id AND {task}.kind='opening_graph_rebuild' AND {task}.generation=graph.generation
            OR {task}.kind='integrity_scan' AND {task}.deduplication_key=graph.deduplication_key
              AND graph.state='complete'
              AND ({task}.payload_json::jsonb->>'graph_generation')=graph.generation::text
              AND ({task}.payload_json::jsonb->>'origin_graph_task_id')=graph.id
              AND ({task}.payload_json::jsonb->>'origin_import_operation_id')=receipt.operation_id)
    )"""


def predicate(task='background_tasks'):
    return f"({receipt_scope(task)}) AND {claimable('durable',task+'.id')}"


def delivery(database, task_id, generation, lease_token):
    return database.execute_native(
        "SELECT task.kind,task.phase,task.payload_json FROM background_tasks task "
        "WHERE task.id=%s AND task.generation=%s AND task.lease_token=%s AND task.state='leased' AND " + predicate('task'),
        (task_id,generation,lease_token)).fetchone()
