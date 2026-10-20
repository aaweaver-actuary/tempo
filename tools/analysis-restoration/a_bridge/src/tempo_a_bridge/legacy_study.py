"""Explicit one-game local API operator. No domain runtime, sync or retry loop."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener

from tempo_game_evidence_contracts import v3
from tempo_game_evidence_contracts.legacy import fingerprint
from .cli import NoRedirect

MAX_BYTES = 8 * 1024 * 1024


def read_preview(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES: raise ValueError('legacy_preview_exceeds_bound')
    value = json.loads(path.read_bytes())
    return validate_preview(value)


def validate_preview(value):
    if (value.get('kind') != 'legacy_game_preview' or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or value.get('preview_digest') != fingerprint(
                {key: item for key, item in value.items() if key != 'preview_digest'})):
        raise ValueError('legacy_preview_file_changed_or_unsupported')
    if value.get('delivery') is not None:
        delivery = v3.EvidenceDelivery.model_validate(value['delivery'])
        if (delivery.authority.target_id, delivery.workspace_id, delivery.authority.repertoire_id, delivery.game_id) != (
                value['target_id'], value['workspace_id'], value['repertoire_id'], value['game_id']):
            raise ValueError('legacy_preview_target_changed')
        if len({item.card_id for item in delivery.obligations}) != 1 or delivery.withdrawals:
            raise ValueError('legacy_one_card_initial_evidence_required')
        if value.get('result') != 'miss_obligation': raise ValueError('legacy_preview_result_inconsistent')
    elif value.get('result') not in ('unknown', 'no_miss_obligation', 'unsupported'):
        raise ValueError('legacy_preview_result_inconsistent')
    return value


class LegacyStudyOperator:
    def __init__(self, endpoint, requester=None):
        origin = urlsplit(endpoint)
        if (origin.scheme != 'http' or origin.hostname not in ('127.0.0.1', 'localhost', '::1')
                or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ('', '/')):
            raise ValueError('explicit_loopback_legacy_api_origin_required')
        self.endpoint = endpoint.rstrip('/')
        self.requester = requester or self.http

    def http(self, method, path, document=None, headers=None):
        body = json.dumps(document, separators=(',', ':')).encode() if document is not None else None
        request = Request(self.endpoint + path, data=body, method=method,
            headers={'Accept': 'application/json', **({'Content-Type': 'application/json'} if body else {}), **(headers or {})})
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES: raise ValueError('legacy_response_exceeds_bound')
            return response.status, json.loads(data)

    @staticmethod
    def delivery(preview):
        validate_preview(preview)
        return v3.EvidenceDelivery.model_validate(preview['delivery']) if preview.get('delivery') else None

    @staticmethod
    def accept_operation(delivery):
        return 'legacy-accept:' + fingerprint([delivery.authority.target_id, delivery.workspace_id, delivery.delivery_id])

    def no_obligation(self, preview):
        return dict(result='no_obligation', preview_result=preview['result'], admission_requested=False)

    def target(self, delivery, *, check_revision=False):
        card = delivery.obligations[0]
        code, status = self.requester('GET', '/api/game-evidence/legacy/cards/' + quote(card.card_id, safe=''))
        if code != 200 or (status.get('target_id'), status.get('workspace_id'), status.get('card_id')) != (
                delivery.authority.target_id, delivery.workspace_id, card.card_id):
            raise ValueError('legacy_api_target_identity_mismatch')
        if check_revision and (type(status.get('actual_card_revision')) is not int
                or status['actual_card_revision'] != card.card_revision):
            raise ValueError('legacy_physical_revision_changed')
        return status

    def acknowledgement(self, delivery, response):
        ack = v3.EvidenceAcknowledgement.model_validate(response)
        if (ack.workspace_id, ack.delivery_id, ack.evidence_id, ack.evidence_revision) != (
                delivery.workspace_id, delivery.delivery_id, delivery.evidence_id, delivery.evidence_revision):
            raise ValueError('legacy_acceptance_receipt_mismatch')
        accepted = ack.status == 'completed'
        if accepted and (len(ack.accepted_obligation_ids) != len(delivery.obligations)
                or set(ack.accepted_obligation_ids) != {item.obligation_id for item in delivery.obligations}):
            raise ValueError('legacy_acceptance_obligations_mismatch')
        return dict(operation_id=self.accept_operation(delivery), transport_state='complete',
                    accepted=accepted, acknowledgement=ack.model_dump(mode='json'), admission_requested=False)

    def status(self, preview):
        delivery = self.delivery(preview)
        return self.target(delivery) if delivery else self.no_obligation(preview)

    def accept(self, preview):
        delivery = self.delivery(preview)
        if delivery is None: return self.no_obligation(preview)
        self.target(delivery, check_revision=True)
        operation = self.accept_operation(delivery)
        code, response = self.requester('POST', '/api/game-evidence/legacy/deliveries',
            delivery.model_dump(mode='json'), {'Idempotency-Key': operation, 'X-Tempo-Work-Class': 'background'})
        if code == 200 and response.get('contract_version') == 3:
            return self.acknowledgement(delivery, response)
        if code != 202 or response.get('operation_id') != operation:
            raise ValueError('legacy_intake_transport_response_invalid')
        return dict(operation_id=operation, transport_state=response['state'], accepted=False, admission_requested=False)

    def receipt(self, preview):
        delivery = self.delivery(preview)
        if delivery is None: return self.no_obligation(preview)
        operation = self.accept_operation(delivery)
        code, response = self.requester('GET', '/api/operations/' + quote(operation, safe=''))
        if code != 200 or response.get('operation_id') != operation:
            raise ValueError('legacy_operation_receipt_mismatch')
        if response['state'] == 'complete': return self.acknowledgement(delivery, response['response'])
        return dict(operation_id=operation, transport_state=response['state'], accepted=False,
                    error=response.get('error'), admission_requested=False)

    def queue(self, preview, operation_id):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', operation_id): raise ValueError('explicit_queue_operation_identity_required')
        delivery = self.delivery(preview)
        if delivery is None: return self.no_obligation(preview)
        accepted = self.receipt(preview)
        if not accepted['accepted']: return {**accepted, 'result': 'acceptance_not_completed'}
        status = self.target(delivery, check_revision=True)
        if status.get('accepted') is not True or status.get('current') is not True:
            return dict(result='not_current', card_status=status, admission_requested=False)
        card_id = delivery.obligations[0].card_id
        code, response = self.requester('POST', '/api/game-evidence/legacy/cards/' + quote(card_id, safe='') + '/queue',
            None, {'Idempotency-Key': operation_id})
        if code not in (200, 202): raise ValueError('legacy_queue_transport_response_invalid')
        if code == 202 and response.get('operation_id') != operation_id:
            raise ValueError('legacy_queue_operation_mismatch')
        if code == 200 and response.get('status') not in ('applied', 'deferred', 'rejected'):
            raise ValueError('legacy_queue_receipt_invalid')
        return dict(operation_id=operation_id, admission_requested=True, response=response)


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('command', choices=('status', 'accept', 'receipt', 'queue'))
    parser.add_argument('--preview', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--operation-id')
    options = parser.parse_args(arguments)
    preview = read_preview(options.preview)
    operator = LegacyStudyOperator(options.endpoint)
    if options.output.exists(): raise ValueError('new_operator_output_required')
    if options.command == 'queue':
        if not options.operation_id: raise ValueError('explicit_queue_operation_identity_required')
        result = operator.queue(preview, options.operation_id)
    else: result = getattr(operator, options.command)(preview)
    with options.output.open('x') as output: json.dump(result, output, indent=2)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
