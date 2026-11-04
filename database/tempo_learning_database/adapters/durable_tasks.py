"""Essential Learning task persistence and leasing on the caller's transaction."""
import json, uuid


def enqueue_daily_task_in_transaction(
    database,
    kind,
    deduplication_key,
    payload,
    *,
    priority=10,
    max_attempts=5,
    delay_seconds=0,
    minimum_generation=0,
):
    initial = json.dumps(payload)
    queued = database.execute_native(
        """INSERT INTO background_tasks(id,kind,deduplication_key,generation,payload_json,priority,max_attempts,next_attempt_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,NOW()+(%s * INTERVAL '1 second'))
        ON CONFLICT(kind,deduplication_key) DO NOTHING RETURNING *""",
        (str(uuid.uuid4()), kind, deduplication_key, minimum_generation + 1,
         initial, priority, max_attempts, delay_seconds),
    ).fetchone()
    event = "enqueued"
    if queued is None:
        queued = database.execute_native(
            "SELECT * FROM background_tasks WHERE kind=%s AND deduplication_key=%s FOR UPDATE",
            (kind, deduplication_key),
        ).fetchone()
        current = json.loads(queued["payload_json"])
        if (queued["state"] in {"queued", "leased", "retrying"}
                and current.get("queue_date") == payload.get("queue_date")):
            # Keep productive work and its lease; publication observes the new
            # request epoch and schedules a full follow-up scan.
            preserve = max(current.get("preserve_through_entry_id", 0),
                           payload.get("preserve_through_entry_id", 0))
            if preserve:
                current["preserve_through_entry_id"] = preserve
            queued = database.execute_native(
                "UPDATE background_tasks SET payload_json=%s WHERE id=%s RETURNING *",
                (json.dumps(current), queued["id"]),
            ).fetchone()
            event = "refresh_requested"
        else:
            queued = database.execute_native(
                """UPDATE background_tasks SET generation=GREATEST(generation+1,%s),
                  payload_json=%s,state='queued',phase='queued',priority=LEAST(priority,%s),
                  attempt_count=0,max_attempts=%s,lease_token=NULL,lease_expires_at=NULL,
                  next_attempt_at=NOW()+(%s * INTERVAL '1 second'),last_error=NULL,updated_at=NOW()
                  WHERE id=%s RETURNING *""",
                (minimum_generation + 1, initial, priority, max_attempts, delay_seconds, queued["id"]),
            ).fetchone()
    database.execute_native(
        "INSERT INTO background_task_events(task_id,generation,event) VALUES(%s,%s,%s)",
        (queued["id"], queued["generation"], event),
    )
    return dict(queued)


def create_or_lock_remediation_task(database, deduplication_key, initial, *, priority,
                                    max_attempts, delay_seconds, minimum_generation):
    task = database.execute_native(
        """INSERT INTO background_tasks(id,kind,deduplication_key,generation,payload_json,priority,max_attempts,next_attempt_at)
           VALUES(%s,'remediation',%s,%s,%s,%s,%s,NOW()+(%s * INTERVAL '1 second'))
           ON CONFLICT(kind,deduplication_key) DO NOTHING RETURNING *""",
        (str(uuid.uuid4()), deduplication_key, minimum_generation + 1,
         json.dumps(initial), priority, max_attempts, delay_seconds),
    ).fetchone()
    created = task is not None
    if task is None:
        task = database.execute_native(
            "SELECT * FROM background_tasks WHERE kind='remediation' AND deduplication_key=%s FOR UPDATE",
            (deduplication_key,),
        ).fetchone()
    return task, created


def set_remediation_payload(database, task, current):
    task = database.execute_native(
        "UPDATE background_tasks SET payload_json=%s WHERE id=%s RETURNING *",
        (json.dumps(current), task["id"]),
    ).fetchone()
    return task


def reset_remediation_task(database, task, initial, *, priority, max_attempts,
                           delay_seconds, minimum_generation):
    task = database.execute_native(
        """UPDATE background_tasks SET generation=GREATEST(generation+1,%s),payload_json=%s,
                   state='queued',phase='queued',priority=LEAST(priority,%s),attempt_count=0,
                   max_attempts=%s,lease_token=NULL,lease_expires_at=NULL,
                   next_attempt_at=NOW()+(%s * INTERVAL '1 second'),last_error=NULL,updated_at=NOW()
                   WHERE id=%s RETURNING *""",
        (minimum_generation + 1, json.dumps(initial), priority, max_attempts,
         delay_seconds, task["id"]),
    ).fetchone()
    return task


def record_remediation_event(database, task, event):
    database.execute_native(
        "INSERT INTO background_task_events(task_id,generation,event) VALUES(%s,%s,%s)",
        (task["id"], task["generation"], event),
    )


def claim(database):
    token = uuid.uuid4().hex
    row = database.execute_native("""SELECT id FROM background_tasks WHERE kind IN ('daily_queue','remediation')
      AND ((state IN ('queued','retrying') AND next_attempt_at<=NOW()) OR (state='leased' AND lease_expires_at<=NOW()))
      ORDER BY priority,updated_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
    if row is None:
        return None
    return dict(
        database.execute_native(
            "UPDATE background_tasks SET state='leased',lease_token=%s,lease_expires_at=NOW()+INTERVAL '2 seconds',attempt_count=attempt_count+1 WHERE id=%s RETURNING *",
            (token, row[0]),
        ).fetchone()
    )
