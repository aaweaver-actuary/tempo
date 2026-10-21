"""Game Evidence reconciliation SQL operations on the caller transaction."""
from __future__ import annotations
from psycopg import Connection
from psycopg.types.json import Jsonb


def accepted_history_savepoint(database: Connection):
    return database.transaction()


def request_reconciliation(database: Connection, workspace, game_id):
    database.execute("""INSERT INTO jobs(job_key,workspace_id,game_id,kind) VALUES(%s,%s,%s,'reconcile')
      ON CONFLICT(job_key) DO UPDATE SET requested=jobs.requested+1,next_attempt_at=now(),error=NULL""",
      ('reconcile:'+game_id,workspace,game_id))


def withdraw_accepted_head(database: Connection, game_id, bucket, withdrawal):
    return database.execute("""UPDATE accepted_heads SET active=false WHERE game_id=%s AND bucket=%s
          AND obligation_id=%s AND obligation_revision=%s RETURNING obligation_id""",
          (game_id,bucket,withdrawal['obligation_id'],withdrawal['expected_obligation_revision'])).fetchone()


def accept_obligation(database: Connection, game_id, bucket, obligation, encounter):
    return database.execute("""INSERT INTO accepted_heads VALUES(%s,%s,%s,%s,true,%s,%s)
          ON CONFLICT(game_id,obligation_id) DO UPDATE SET obligation_revision=excluded.obligation_revision,
          active=true,obligation=excluded.obligation,encounter=excluded.encounter
          WHERE accepted_heads.bucket=excluded.bucket AND accepted_heads.obligation_revision<excluded.obligation_revision
          RETURNING obligation_id""",
          (game_id,bucket,obligation['obligation_id'],obligation['obligation_revision'],
           Jsonb(obligation),Jsonb(encounter))).fetchone()


def set_reconciliation_status(database: Connection, status, reason, game_id):
    database.execute('UPDATE games SET reconciliation_status=%s,reconciliation_reason=%s WHERE game_id=%s',
                     (status,reason,game_id))


def locked_game(database: Connection, game_id):
    return database.execute('SELECT * FROM games WHERE game_id=%s FOR UPDATE',(game_id,)).fetchone()


def unresolved_receiver_authority(database: Connection, game_id):
    return database.execute("""SELECT 1 FROM outbox WHERE game_id=%s AND attempts>0
      AND (status IN ('conflict','stale_revision','blocked') OR (status='terminal_failure' AND acknowledgement IS NULL)) LIMIT 1""",(game_id,)).fetchone()


def outstanding_receipt(database: Connection, game_id):
    return database.execute("""SELECT 1 FROM outbox WHERE game_id=%s AND attempts>0
      AND status IN ('pending','accepted_pending','retryable_failure') LIMIT 1""",(game_id,)).fetchone()


def workspace_inputs(database: Connection, workspace_id):
    return database.execute('SELECT * FROM workspace_inputs WHERE workspace_id=%s',(workspace_id,)).fetchone()


def comparison_report(database: Connection, game_id, report_revision):
    return database.execute('SELECT report FROM comparisons WHERE game_id=%s AND revision=%s',
                                  (game_id,report_revision)).fetchone()


def snapshot_document(database: Connection, snapshot_digest):
    return database.execute('SELECT document FROM snapshots WHERE digest=%s',(snapshot_digest,)).fetchone()


def accepted_heads(database: Connection, game_id):
    return database.execute('SELECT * FROM accepted_heads WHERE game_id=%s AND active ORDER BY bucket,obligation_id',(game_id,)).fetchall()


def supersede_unattempted_deliveries(database: Connection, game_id):
    database.execute("""UPDATE outbox SET status='superseded' WHERE game_id=%s AND attempts=0
      AND status IN ('pending','waiting_predecessor','blocked')""",(game_id,))


def enqueue_delivery(database: Connection, payload, game_id, revision, report_revision, bucket):
    database.execute("""INSERT INTO outbox(delivery_id,game_id,evidence_revision,comparison_revision,bucket,payload,status)
          VALUES(%s,%s,%s,%s,%s,%s,'pending')""",
          (payload['delivery_id'],game_id,revision,report_revision,bucket,Jsonb(payload)))


def mark_reconciled(database: Connection, revision, game_id):
    database.execute('UPDATE games SET evidence_revision=%s,reconciled_revision=report_revision WHERE game_id=%s',
                     (revision,game_id))


def lock_workspace(database: Connection, workspace_id):
    database.execute('SELECT workspace_id FROM workspace_inputs WHERE workspace_id=%s FOR SHARE',(workspace_id,))


def reconciliation_game(database: Connection, game_id):
    return database.execute('SELECT * FROM games WHERE game_id=%s FOR UPDATE',(game_id,)).fetchone()


def locked_job(database: Connection, job_key):
    return database.execute('SELECT * FROM jobs WHERE job_key=%s FOR UPDATE',(job_key,)).fetchone()


def accepted_history_page(database: Connection, game_id, cursor):
    return database.execute('SELECT * FROM outbox WHERE game_id=%s AND (evidence_revision,bucket)>(%s,%s) ORDER BY evidence_revision,bucket LIMIT 64',
                                        (game_id,*cursor)).fetchall()


def reject_invalid_history(database: Connection, job_key):
    database.execute("UPDATE jobs SET published=requested,lease_token=NULL,leased_until=NULL,error='invalid_accepted_history' WHERE job_key=%s",(job_key,))


def advance_history_page(database: Connection, cursor, job_key):
    database.execute('UPDATE jobs SET cursor=%s,lease_token=NULL,leased_until=NULL WHERE job_key=%s',
                                  (cursor,job_key))


def mark_heads_initialized(database: Connection, game_id):
    database.execute('UPDATE games SET heads_initialized=true WHERE game_id=%s',(game_id,))


def complete_job(database: Connection, job_key):
    database.execute('UPDATE jobs SET published=requested,lease_token=NULL,leased_until=NULL,error=NULL WHERE job_key=%s',(job_key,))
