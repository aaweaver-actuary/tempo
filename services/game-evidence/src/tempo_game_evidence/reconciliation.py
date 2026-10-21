"""Durable desired-state reconciliation. Completed receipts are its only authority."""
from tempo_game_evidence_database.adapters import reconciliation as storage
import json

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
    storage.request_reconciliation(database, workspace, game_id)


def reconcile_comparison(database, job, game, store):
    request_reconciliation(database, job["workspace_id"], game["game_id"])
    finalize(database, game["game_id"], snapshot_loader=store.comparison_snapshot)


def apply_completed(database, row):
    receipt = validate_receipt(row['payload'],row['acknowledgement'])
    if receipt.status != 'completed':
        raise ValueError('completed_receipt_required')
    payload = row['payload']
    encounters = {item['encounter_id']:item for item in payload['encounters']}
    for withdrawal in payload['withdrawals']:
        changed = storage.withdraw_accepted_head(database, row['game_id'], row['bucket'], withdrawal)
        if not changed:
            raise ValueError('accepted_withdrawal_history_mismatch')
    for obligation in payload['obligations']:
        changed = storage.accept_obligation(database, row['game_id'], row['bucket'], obligation, encounters[obligation['encounter_id']])
        if not changed:
            raise ValueError('accepted_obligation_history_mismatch')


def _state(database, game_id, status, reason=None):
    storage.set_reconciliation_status(database, status, reason, game_id)


def finalize(database, game_id, *, snapshot_loader=None):
    # Caller holds the game lock; no network operation occurs in this transaction.
    game = storage.locked_game(database, game_id)
    if not game['heads_initialized']:
        _state(database,game_id,'pending','accepted_history_recovery_required')
        return
    if storage.unresolved_receiver_authority(database, game_id):
        _state(database,game_id,'blocked','receiver_authority_unresolved')
        return
    if storage.outstanding_receipt(database, game_id):
        _state(database,game_id,'finalized' if game['reconciled_revision']==game['report_revision'] else 'waiting_predecessor',
               None if game['reconciled_revision']==game['report_revision'] else 'prior_delivery_acknowledgement_required')
        return
    if game['reconciled_revision'] == game['report_revision']:
        _state(database,game_id,'finalized')
        return
    inputs = storage.workspace_inputs(database, game['workspace_id'])
    comparison = storage.comparison_report(database, game_id, game['report_revision'])
    if not comparison:
        _state(database,game_id,'pending','comparison_required')
        return
    report = comparison['report']
    if (report['game']['revision'] != game['revision'] or
            (report.get('snapshot') or {}).get('digest') != inputs['snapshot_digest']):
        _state(database,game_id,'pending','current_comparison_required')
        return
    snapshot_row = storage.snapshot_document(database, inputs['snapshot_digest'])
    snapshot = ((snapshot_loader(inputs['snapshot_digest'], snapshot_row['document']) if snapshot_loader else parse_snapshot(snapshot_row['document'])) if snapshot_row else None)
    heads = storage.accepted_heads(database, game_id)
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
    storage.supersede_unattempted_deliveries(database, game_id)
    if incomplete:
        _state(database,game_id,'blocked','comparison_scope_incomplete')
        return
    for bucket,payload in payloads:
        storage.enqueue_delivery(database, payload, game_id, revision, game['report_revision'], bucket)
    storage.mark_reconciled(database, revision, game_id)
    _state(database,game_id,'finalized')


def resume_reconciliation(store, job):
    with store.connect() as database:
        storage.lock_workspace(database, job['workspace_id'])
        game = storage.reconciliation_game(database, job['game_id'])
        current = storage.locked_job(database, job['job_key'])
        if not store._current(current,job):
            store._release(database,job)
            return
        if not game['heads_initialized']:
            cursor = json.loads(job['cursor']) if job['cursor'] else [0,-1]
            history = storage.accepted_history_page(database, game['game_id'], cursor)
            try:
                with storage.accepted_history_savepoint(database):
                    for row in history:
                        if row['status']=='completed':
                            apply_completed(database,row)
            except ValueError:
                _state(database,game['game_id'],'blocked','invalid_accepted_history')
                storage.reject_invalid_history(database, job['job_key'])
                return
            if len(history)==64:
                storage.advance_history_page(database, json.dumps([history[-1]['evidence_revision'],history[-1]['bucket']]), job['job_key'])
                return
            storage.mark_heads_initialized(database, game['game_id'])
        finalize(database,game['game_id'],snapshot_loader=store.comparison_snapshot)
        storage.complete_job(database, job['job_key'])
