"""Game Evidence delivery SQL operations on the caller transaction."""
from __future__ import annotations
from psycopg import Connection
from psycopg.types.json import Jsonb


def delivery_batches(database: Connection, game_id, comparison_revision):
    return database.execute("SELECT delivery_id,status,reason,attempts,acknowledgement FROM outbox WHERE game_id=%s AND (comparison_revision=%s OR (attempts>0 AND status IN ('pending','accepted_pending','retryable_failure'))) ORDER BY evidence_revision,bucket",
                               (game_id,comparison_revision)).fetchall()


def claimable_game(database: Connection):
    return database.execute("""SELECT games.* FROM games WHERE heads_initialized
          AND NOT EXISTS(SELECT 1 FROM outbox WHERE outbox.game_id=games.game_id AND attempts>0
            AND (status IN ('conflict','stale_revision','blocked') OR (status='terminal_failure' AND acknowledgement IS NULL)))
          AND NOT EXISTS(SELECT 1 FROM outbox WHERE outbox.game_id=games.game_id AND attempts>0
            AND status IN ('pending','accepted_pending','retryable_failure')
            AND (next_attempt_at>now() OR leased_until>now()))
          AND EXISTS(SELECT 1 FROM outbox
            JOIN comparisons comparison ON comparison.game_id=outbox.game_id AND comparison.revision=outbox.comparison_revision
            JOIN workspace_inputs inputs ON inputs.workspace_id=games.workspace_id
            WHERE outbox.game_id=games.game_id AND status IN ('pending','accepted_pending','retryable_failure')
            AND next_attempt_at<=now() AND (leased_until IS NULL OR leased_until<now())
            AND (attempts>0 OR (comparison_revision=games.report_revision AND
              (payload->>'game_revision')::bigint=games.revision AND
              comparison.report->'snapshot'->>'digest'=inputs.snapshot_digest)))
          ORDER BY game_id LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()


def has_delivery_conflict(database: Connection, game_id):
    return database.execute("""SELECT 1 FROM outbox WHERE game_id=%s AND attempts>0
          AND status IN ('conflict','stale_revision','blocked') LIMIT 1""",(game_id,)).fetchone()


def current_delivery(database: Connection, game_id):
    return database.execute("""SELECT * FROM outbox WHERE game_id=%s
          AND status IN ('pending','accepted_pending','retryable_failure')
          ORDER BY (attempts>0) DESC,evidence_revision,bucket LIMIT 1 FOR UPDATE""",(game_id,)).fetchone()


def comparison_report(database: Connection, game_id, comparison_revision):
    return database.execute('SELECT report FROM comparisons WHERE game_id=%s AND revision=%s',
                                      (game_id,comparison_revision)).fetchone()


def workspace_snapshot(database: Connection, workspace_id):
    return database.execute('SELECT snapshot_digest FROM workspace_inputs WHERE workspace_id=%s',
                                      (workspace_id,)).fetchone()


def begin_attempt(database: Connection, lease_token, delivery_id):
    database.execute("UPDATE outbox SET lease_token=%s,leased_until=now()+interval '90 seconds',attempts=attempts+1 WHERE delivery_id=%s",
                         (lease_token,delivery_id))


def lock_game(database: Connection, game_id):
    database.execute('SELECT game_id FROM games WHERE game_id=%s FOR UPDATE',(game_id,))


def save_receipt(database: Connection, status, reason, acknowledgement, next_attempt_at, delivery_id, lease_token):
    return database.execute("""UPDATE outbox SET status=%s,reason=%s,acknowledgement=%s,
          next_attempt_at=%s,lease_token=NULL,leased_until=NULL WHERE delivery_id=%s AND lease_token=%s
          RETURNING *""",(status,reason,Jsonb(acknowledgement) if acknowledgement else None,
          next_attempt_at,delivery_id,lease_token)).fetchone()


def delivery_game_identity(database: Connection, delivery_id):
    return database.execute('SELECT game_id FROM outbox WHERE delivery_id=%s', (delivery_id,)).fetchone()


def locked_game(database: Connection, game_id):
    return database.execute('SELECT * FROM games WHERE game_id=%s FOR UPDATE', (game_id,)).fetchone()


def locked_delivery(database: Connection, delivery_id, game_id):
    return database.execute('SELECT * FROM outbox WHERE delivery_id=%s AND game_id=%s FOR UPDATE',
                               (delivery_id, game_id)).fetchone()


def requeue_auth_delivery(database: Connection, delivery_id):
    database.execute("""UPDATE outbox SET status='retryable_failure',next_attempt_at=now(),
            lease_token=NULL,leased_until=NULL WHERE delivery_id=%s""", (delivery_id,))
