"""Immutable v2 delivery with durable, acknowledgement-aware reconciliation."""
from datetime import timedelta
import uuid
from pathlib import Path

import httpx
from psycopg.types.json import Jsonb
from tempo_game_evidence_contracts.v2 import EvidenceAcknowledgement, EvidenceDelivery

from .reconciliation import apply_completed, request_reconciliation, validate_receipt
from .source import utc_now
from .transport import RemoteFailure, request_bytes


def inspect_delivery(database, game_id, comparison_revision, reconciliation_status, reconciliation_reason, *, has_report=True):
    """The report/export read port; existing outbox and receipts remain authority."""
    batches = database.execute("SELECT delivery_id,status,reason,attempts,acknowledgement FROM outbox WHERE game_id=%s AND (comparison_revision=%s OR (attempts>0 AND status IN ('pending','accepted_pending','retryable_failure'))) ORDER BY evidence_revision,bucket",
                               (game_id,comparison_revision)).fetchall()
    priority = ['blocked','terminal_failure','conflict','stale_revision','retryable_failure','waiting_predecessor','accepted_pending','pending','completed']
    status = next((status for status in priority if any(batch['status']==status for batch in batches)), 'not_required' if has_report else 'not_ready')
    if reconciliation_status in {'pending','waiting_predecessor','blocked'}:
        status = reconciliation_status
    return {'status':status,'reason':reconciliation_reason or next((batch['reason'] for batch in batches if batch['reason']),None),
            'attempts':sum(batch['attempts'] for batch in batches),'batches':batches}


def learning_token(path):
    credential = Path(path)
    if credential.stat().st_mode & 0o077:
        raise ValueError("learning_credential_file_must_be_private")
    token = credential.read_text().strip()
    return validate_learning_token(token)


def validate_learning_token(token):
    if not isinstance(token, str) or len(token) < 32 or "\n" in token or "\r" in token:
        raise ValueError("invalid_learning_credential")
    return token


def deliver_one(store, endpoint, *, client=None, token=None):
    if not endpoint:
        return False
    token = validate_learning_token(token)
    with store.connect() as database:
        # The game lock serializes claims, comparisons, receipt projection and
        # reconciliation. An in-flight attempt fences every later game delivery.
        game = database.execute("""SELECT games.* FROM games WHERE heads_initialized
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
        if not game:
            return False
        if database.execute("""SELECT 1 FROM outbox WHERE game_id=%s AND attempts>0
          AND status IN ('conflict','stale_revision','blocked') LIMIT 1""",(game['game_id'],)).fetchone():
            return False
        row = database.execute("""SELECT * FROM outbox WHERE game_id=%s
          AND status IN ('pending','accepted_pending','retryable_failure')
          ORDER BY (attempts>0) DESC,evidence_revision,bucket LIMIT 1 FOR UPDATE""",(game['game_id'],)).fetchone()
        if row['next_attempt_at']>utc_now() or row['leased_until'] and row['leased_until']>utc_now():
            return False
        if not row['attempts']:
            report = database.execute('SELECT report FROM comparisons WHERE game_id=%s AND revision=%s',
                                      (game['game_id'],row['comparison_revision'])).fetchone()['report']
            inputs = database.execute('SELECT snapshot_digest FROM workspace_inputs WHERE workspace_id=%s',
                                      (game['workspace_id'],)).fetchone()
            if (row['comparison_revision'] != game['report_revision'] or
                    row['payload']['game_revision'] != game['revision'] or
                    (report.get('snapshot') or {}).get('digest') != inputs['snapshot_digest']):
                return False
        lease_token = uuid.uuid4().hex
        database.execute("UPDATE outbox SET lease_token=%s,leased_until=now()+interval '90 seconds',attempts=attempts+1 WHERE delivery_id=%s",
                         (lease_token,row['delivery_id']))
    payload = EvidenceDelivery.model_validate(row['payload'])
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(15,read=10),follow_redirects=False)
    acknowledgement = None
    delay = min(900,2**min(row['attempts'],10))
    try:
        raw = request_bytes(client,'POST',endpoint,max_bytes=65536,json=payload.model_dump(mode='json'),
                            headers={"Authorization":"Bearer "+token} if token else {})
        acknowledgement = EvidenceAcknowledgement.model_validate_json(raw)
        validate_receipt(row['payload'],acknowledgement.model_dump(mode='json'))
        status,reason = acknowledgement.status,acknowledgement.reason
    except RemoteFailure as error:
        # HTTP rejection/bounds are not a validated receipt of non-acceptance.
        authentication_failure = error.reason == 'remote_rejected_401'
        status = 'blocked' if error.terminal and not authentication_failure else 'retryable_failure'
        reason,delay = error.reason,max(delay,error.retry_after,60 if authentication_failure else 0)
        acknowledgement = None
    except httpx.HTTPError:
        status,reason = 'retryable_failure','learning_unreachable_or_response_lost'
    except ValueError:
        status,reason = 'retryable_failure','invalid_learning_acknowledgement'
        acknowledgement = None
    finally:
        if owned:
            client.close()
    with store.connect() as database:
        database.execute('SELECT game_id FROM games WHERE game_id=%s FOR UPDATE',(row['game_id'],))
        changed = database.execute("""UPDATE outbox SET status=%s,reason=%s,acknowledgement=%s,
          next_attempt_at=%s,lease_token=NULL,leased_until=NULL WHERE delivery_id=%s AND lease_token=%s
          RETURNING *""",(status,reason,Jsonb(acknowledgement.model_dump(mode='json')) if acknowledgement else None,
          utc_now()+timedelta(seconds=delay),row['delivery_id'],lease_token)).fetchone()
        if changed:
            if status=='completed':
                apply_completed(database,changed)
            request_reconciliation(database,game['workspace_id'],row['game_id'])
    return True


class AuthRetryRefused(ValueError):
    """Only fixed, sanitized refusal reasons may be exposed by the recovery CLI."""


def retry_auth_delivery(store, workspace, delivery_id):
    """Requeue one original legacy 401 delivery, never infer non-acceptance."""
    with store.connect() as database:
        identity = database.execute('SELECT game_id FROM outbox WHERE delivery_id=%s', (delivery_id,)).fetchone()
        if identity is None:
            raise AuthRetryRefused('auth_delivery_not_found')
        game = database.execute('SELECT * FROM games WHERE game_id=%s FOR UPDATE', (identity['game_id'],)).fetchone()
        if game['workspace_id'] != workspace:
            raise AuthRetryRefused('auth_delivery_workspace_mismatch')
        row = database.execute('SELECT * FROM outbox WHERE delivery_id=%s AND game_id=%s FOR UPDATE',
                               (delivery_id, game['game_id'])).fetchone()
        if row['leased_until'] and row['leased_until'] > utc_now():
            raise AuthRetryRefused('auth_delivery_active_lease')
        if row['status'] == 'completed':
            raise AuthRetryRefused('auth_delivery_already_completed')
        if (row['reason'] != 'remote_rejected_401' or row['acknowledgement'] is not None
                or row['attempts'] < 1 or row['status'] not in {'blocked', 'retryable_failure'}):
            raise AuthRetryRefused('auth_delivery_not_legacy_401_block')
        if row['status'] == 'retryable_failure':
            return {'delivery_id': delivery_id, 'status': row['status'], 'requeued': False}
        database.execute("""UPDATE outbox SET status='retryable_failure',next_attempt_at=now(),
            lease_token=NULL,leased_until=NULL WHERE delivery_id=%s""", (delivery_id,))
    return {'delivery_id': delivery_id, 'status': 'retryable_failure', 'requeued': True}
