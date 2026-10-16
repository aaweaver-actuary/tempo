"""Durable desired-state reconciliation. Completed receipts are its only authority."""
import json

from psycopg.types.json import Jsonb
from tempo_game_evidence_contracts.v2 import EvidenceAcknowledgement, EvidenceDelivery

from .payloads import make_deliveries
from .snapshot import parse_snapshot



def validate_receipt(payload, acknowledgement):
    payload = EvidenceDelivery.model_validate(payload)
    receipt = EvidenceAcknowledgement.model_validate(acknowledgement)
    if ((receipt.workspace_id, receipt.delivery_id, receipt.evidence_id, receipt.evidence_revision) !=
            (payload.workspace_id, payload.delivery_id, payload.evidence_id, payload.evidence_revision)):
        raise ValueError('acknowledgement_identity_mismatch')
    expected = {item.obligation_id for item in payload.obligations}
    accepted = set(receipt.accepted_obligation_ids)
    if (len(receipt.accepted_obligation_ids) != len(accepted) or not accepted <= expected
            or receipt.status == 'completed' and accepted != expected):
        raise ValueError('acknowledgement_obligation_mismatch')
    return receipt


def request_reconciliation(database, workspace, game_id):
    database.execute("""INSERT INTO jobs(job_key,workspace_id,game_id,kind) VALUES(%s,%s,%s,'reconcile')
      ON CONFLICT(job_key) DO UPDATE SET requested=jobs.requested+1,next_attempt_at=now(),error=NULL""",
      ('reconcile:'+game_id,workspace,game_id))


def apply_completed(database, row):
    receipt = validate_receipt(row['payload'],row['acknowledgement'])
    if receipt.status != 'completed':
        raise ValueError('completed_receipt_required')
    payload = row['payload']
    encounters = {item['encounter_id']:item for item in payload['encounters']}
    for withdrawal in payload['withdrawals']:
        changed = database.execute("""UPDATE accepted_heads SET active=false WHERE game_id=%s AND bucket=%s
          AND obligation_id=%s AND obligation_revision=%s RETURNING obligation_id""",
          (row['game_id'],row['bucket'],withdrawal['obligation_id'],withdrawal['expected_obligation_revision'])).fetchone()
        if not changed:
            raise ValueError('accepted_withdrawal_history_mismatch')
    for obligation in payload['obligations']:
        changed = database.execute("""INSERT INTO accepted_heads VALUES(%s,%s,%s,%s,true,%s,%s)
          ON CONFLICT(game_id,obligation_id) DO UPDATE SET obligation_revision=excluded.obligation_revision,
          active=true,obligation=excluded.obligation,encounter=excluded.encounter
          WHERE accepted_heads.bucket=excluded.bucket AND accepted_heads.obligation_revision<excluded.obligation_revision
          RETURNING obligation_id""",
          (row['game_id'],row['bucket'],obligation['obligation_id'],obligation['obligation_revision'],
           Jsonb(obligation),Jsonb(encounters[obligation['encounter_id']]))).fetchone()
        if not changed:
            raise ValueError('accepted_obligation_history_mismatch')


def _state(database, game_id, status, reason=None):
    database.execute('UPDATE games SET reconciliation_status=%s,reconciliation_reason=%s WHERE game_id=%s',
                     (status,reason,game_id))


def finalize(database, game_id):
    # Caller holds the game lock; no network operation occurs in this transaction.
    game = database.execute('SELECT * FROM games WHERE game_id=%s FOR UPDATE',(game_id,)).fetchone()
    if not game['heads_initialized']:
        _state(database,game_id,'pending','accepted_history_recovery_required')
        return
    if database.execute("""SELECT 1 FROM outbox WHERE game_id=%s AND attempts>0
      AND (status IN ('conflict','stale_revision','blocked') OR (status='terminal_failure' AND acknowledgement IS NULL)) LIMIT 1""",(game_id,)).fetchone():
        _state(database,game_id,'blocked','receiver_authority_unresolved')
        return
    if database.execute("""SELECT 1 FROM outbox WHERE game_id=%s AND attempts>0
      AND status IN ('pending','accepted_pending','retryable_failure') LIMIT 1""",(game_id,)).fetchone():
        _state(database,game_id,'finalized' if game['reconciled_revision']==game['report_revision'] else 'waiting_predecessor',
               None if game['reconciled_revision']==game['report_revision'] else 'prior_delivery_acknowledgement_required')
        return
    if game['reconciled_revision'] == game['report_revision']:
        _state(database,game_id,'finalized')
        return
    inputs = database.execute('SELECT * FROM workspace_inputs WHERE workspace_id=%s',(game['workspace_id'],)).fetchone()
    comparison = database.execute('SELECT report FROM comparisons WHERE game_id=%s AND revision=%s',
                                  (game_id,game['report_revision'])).fetchone()
    if not comparison:
        _state(database,game_id,'pending','comparison_required')
        return
    report = comparison['report']
    if (report['game']['revision'] != game['revision'] or
            (report.get('snapshot') or {}).get('digest') != inputs['snapshot_digest']):
        _state(database,game_id,'pending','current_comparison_required')
        return
    snapshot_row = database.execute('SELECT document FROM snapshots WHERE digest=%s',(inputs['snapshot_digest'],)).fetchone()
    snapshot = parse_snapshot(snapshot_row['document']) if snapshot_row else None
    heads = database.execute('SELECT * FROM accepted_heads WHERE game_id=%s AND active ORDER BY bucket,obligation_id',(game_id,)).fetchall()
    prior = {}
    for head in heads:
        previous = prior.setdefault(head['bucket'],dict(bucket=head['bucket'],evidence_revision=0,status='completed',
            payload=dict(contract_version=2,obligations=[],withdrawals=[],encounters=[])))
        previous['payload']['obligations'].append(head['obligation'])
        previous['payload']['encounters'].append(head['encounter'])
    revision = game['evidence_revision']+1
    findings = report['findings']
    payloads = make_deliveries(game['document'],findings,revision,snapshot.publication if snapshot else None,prior.values())
    incomplete = bool(heads) and (not snapshot or not snapshot.serving_current() or not snapshot.complete or 'source_error' in game['document'])
    known_plies = {item['ply'] for item in findings if item['status'] != 'unknown'}
    current_cards = {item.card_id for item in snapshot.publication.definitions} if snapshot else set()
    current_ids = {item['obligation_id'] for _,payload in payloads for item in payload['obligations']}
    positions = game['positions'] or []
    for head in heads:
        encounter = head['encounter']
        ply = encounter['ply']
        if (head['obligation_id'] not in current_ids and ply<len(positions)
                and positions[ply]['move']==encounter['observed_move'] and ply not in known_plies
                and head['obligation']['card_id'] in current_cards):
            incomplete = True
    database.execute("""UPDATE outbox SET status='superseded' WHERE game_id=%s AND attempts=0
      AND status IN ('pending','waiting_predecessor','blocked')""",(game_id,))
    if incomplete:
        _state(database,game_id,'blocked','comparison_scope_incomplete')
        return
    for bucket,payload in payloads:
        database.execute("""INSERT INTO outbox(delivery_id,game_id,evidence_revision,comparison_revision,bucket,payload,status)
          VALUES(%s,%s,%s,%s,%s,%s,'pending')""",
          (payload['delivery_id'],game_id,revision,game['report_revision'],bucket,Jsonb(payload)))
    database.execute('UPDATE games SET evidence_revision=%s,reconciled_revision=report_revision WHERE game_id=%s',
                     (revision,game_id))
    _state(database,game_id,'finalized')


def resume_reconciliation(store, job):
    with store.connect() as database:
        database.execute('SELECT workspace_id FROM workspace_inputs WHERE workspace_id=%s FOR SHARE',(job['workspace_id'],))
        game = database.execute('SELECT * FROM games WHERE game_id=%s FOR UPDATE',(job['game_id'],)).fetchone()
        current = database.execute('SELECT * FROM jobs WHERE job_key=%s FOR UPDATE',(job['job_key'],)).fetchone()
        if not store._current(current,job):
            store._release(database,job)
            return
        if not game['heads_initialized']:
            cursor = json.loads(job['cursor']) if job['cursor'] else [0,-1]
            history = database.execute('SELECT * FROM outbox WHERE game_id=%s AND (evidence_revision,bucket)>(%s,%s) ORDER BY evidence_revision,bucket LIMIT 64',
                                        (game['game_id'],*cursor)).fetchall()
            try:
                with database.transaction():
                    for row in history:
                        if row['status']=='completed':
                            apply_completed(database,row)
            except ValueError:
                _state(database,game['game_id'],'blocked','invalid_accepted_history')
                database.execute("UPDATE jobs SET published=requested,lease_token=NULL,leased_until=NULL,error='invalid_accepted_history' WHERE job_key=%s",(job['job_key'],))
                return
            if len(history)==64:
                database.execute('UPDATE jobs SET cursor=%s,lease_token=NULL,leased_until=NULL WHERE job_key=%s',
                                  (json.dumps([history[-1]['evidence_revision'],history[-1]['bucket']]),job['job_key']))
                return
            database.execute('UPDATE games SET heads_initialized=true WHERE game_id=%s',(game['game_id'],))
        finalize(database,game['game_id'])
        database.execute('UPDATE jobs SET published=requested,lease_token=NULL,leased_until=NULL,error=NULL WHERE job_key=%s',(job['job_key'],))
