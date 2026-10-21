"""Learning maintenance SQL and driver error identities; caller owns transactions."""
import json
import psycopg
from psycopg_pool import PoolTimeout


def leased_task_payload_for_update(database, task):
    current = database.execute_native(
        """SELECT payload_json FROM background_tasks
               WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'
                 AND lease_expires_at>clock_timestamp() FOR UPDATE""",
        (task["id"], task["generation"], task["lease_token"]),
    ).fetchone()
    return current


def finish_task_slice(database, task, payload, state, phase):
    changed = database.execute_native(
        """UPDATE background_tasks SET payload_json=%s,state=%s,phase=%s,
               lease_token=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=NOW()
           WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'
             AND lease_expires_at>clock_timestamp()
           RETURNING id""",
        (
            json.dumps(payload),
            state,
            phase,
            task["id"],
            task["generation"],
            task["lease_token"],
        ),
    ).fetchone()
    return changed


def next_queued_operation(database):
    operation = database.execute_native(
        "SELECT operation_id FROM operation_receipts WHERE state='queued' AND command_name!='publication.activate.v2' AND (next_retry_at IS NULL OR next_retry_at<=NOW()) ORDER BY created_at,operation_id LIMIT 1"
    ).fetchone()
    return operation


def defer_queued_operation(database, operation_id):
    database.execute_native(
        "UPDATE operation_receipts SET next_retry_at=NOW()+INTERVAL '1 second' WHERE operation_id=%s AND state='queued'",
        (operation_id,),
    )


def release_yielded_task(database, task):
    database.execute_native(
        """UPDATE background_tasks SET state='queued',lease_token=NULL,
                    lease_expires_at=NULL,last_error='Learning slice yielded',updated_at=NOW()
                    WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'""",
        (task["id"], task["generation"], task["lease_token"]),
    )


def queue_day_order(database, day):
    saved = database.execute("SELECT seed,membership_hash FROM daily_queue_days WHERE queue_date=?", (day,)).fetchone()
    return saved


def set_queue_position(database, position, entry_id):
    database.execute("UPDATE daily_queue SET position=? WHERE id=?", (position, entry_id))


def save_queue_day_order(database, day, seed, membership_hash, generated_at):
    database.execute(
        """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
                       VALUES(?,?,?,?) ON CONFLICT(queue_date) DO UPDATE SET
                       membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
        (day, seed, membership_hash, generated_at),
    )


def mark_queue_projection_ready(database, day, updated_at):
    database.execute_native(
        "INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,updated_at) VALUES(%s,'ready',1,0,%s) ON CONFLICT(queue_date) DO UPDATE SET state='ready',generation=queue_projections.generation+1,refresh_pending=0,last_error=NULL,updated_at=excluded.updated_at",
        (day, updated_at),
    )
