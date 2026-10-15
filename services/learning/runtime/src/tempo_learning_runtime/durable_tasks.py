"""Narrow durable Learning queue work, with database-selected identity."""
import json,uuid

def enqueue_task_in_transaction(database,kind,deduplication_key,payload,*,priority=10,max_attempts=5,delay_seconds=0,minimum_generation=0):
    if kind!='daily_queue': raise ValueError('Only essential daily_queue work is registered')
    queued=database.execute_native("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,payload_json,priority,max_attempts,next_attempt_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,NOW()+(%s * INTERVAL '1 second'))
        ON CONFLICT(kind,deduplication_key) DO UPDATE SET
          generation=GREATEST(background_tasks.generation+1,EXCLUDED.generation),payload_json=EXCLUDED.payload_json,
          state='queued',phase='queued',priority=LEAST(background_tasks.priority,EXCLUDED.priority),
          attempt_count=0,max_attempts=EXCLUDED.max_attempts,lease_token=NULL,lease_expires_at=NULL,
          next_attempt_at=EXCLUDED.next_attempt_at,last_error=NULL,updated_at=NOW()
        RETURNING *""",(str(uuid.uuid4()),kind,deduplication_key,minimum_generation+1,json.dumps(payload),priority,max_attempts,delay_seconds)).fetchone()
    database.execute_native("INSERT INTO background_task_events(task_id,generation,event) VALUES(%s,%s,'enqueued')",(queued['id'],queued['generation']))
    return dict(queued)

def claim(database):
    token=uuid.uuid4().hex
    row=database.execute_native("""SELECT id FROM background_tasks WHERE kind='daily_queue'
      AND ((state IN ('queued','retrying') AND next_attempt_at<=NOW()) OR (state='leased' AND lease_expires_at<=NOW()))
      ORDER BY priority,updated_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
    if row is None:return None
    return dict(database.execute_native("UPDATE background_tasks SET state='leased',lease_token=%s,lease_expires_at=NOW()+INTERVAL '2 seconds',attempt_count=attempt_count+1 WHERE id=%s RETURNING *",(token,row[0])).fetchone())
