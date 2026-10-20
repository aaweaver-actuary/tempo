"""File-only projection and explicit HTTP intents; imports no domain runtime."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from tempo_game_evidence_contracts import v3
from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_a_bridge.legacy_observation import observe_capture, main as observe_main
from tempo_a_bridge.legacy_study import LegacyStudyOperator, read_preview

ROOT = Path(__file__).resolve().parents[4]


def capture():
    raw = json.loads((ROOT / 'services/game-evidence/fixtures/legacy-scoped-input.json').read_text())
    raw.pop('provenance')
    scope = raw['catalogue_scope']['scope']
    game = dict(id=scope['game_id'], provider='lichess', provider_game_id='synthetic-game',
        content_hash=scope['content_hash'], played_at=scope['occurred_at'], color=scope['learner_color'],
        result='draw', start_fen=raw['tables']['cards'][0]['start_fen'],
        moves_json=json.dumps(scope['moves']), adaptive_excluded=0)
    document = dict(kind='legacy_game_capture', schema_version=1, game=game, **raw)
    return {**document, 'export_digest': fingerprint(document)}


def preview():
    delivery = json.loads((ROOT / 'contracts/game-evidence/fixtures/v3/delivery.json').read_text())['payload']
    document = dict(kind='legacy_game_preview', schema_version=1, result='miss_obligation', reason=None,
        target_id=delivery['authority']['target_id'], workspace_id=delivery['workspace_id'],
        repertoire_id=delivery['authority']['repertoire_id'], game_id=delivery['game_id'], delivery=delivery,
        findings=[], source_content_hash=delivery['game_source']['content_hash'], captured_at=delivery['captured_at'])
    return seal(document)


def seal(document):
    return {**document, 'preview_digest': fingerprint({key: item for key, item in document.items() if key != 'preview_digest'})}


class Server:
    def __init__(self, value):
        self.delivery = v3.EvidenceDelivery.model_validate(value['delivery'])
        self.calls, self.accepted, self.pending = [], False, False
        self.status = dict(target_id=value['target_id'], workspace_id=value['workspace_id'],
            card_id=self.delivery.obligations[0].card_id, actual_card_revision=1, accepted=False, current=False)
        self.ack = dict(contract_version=3, workspace_id=self.delivery.workspace_id,
            delivery_id=self.delivery.delivery_id, evidence_id=self.delivery.evidence_id,
            evidence_revision=1, status='completed', reason=None, learning_revision=1,
            accepted_obligation_ids=[item.obligation_id for item in self.delivery.obligations])
    def __call__(self, method, path, document=None, headers=None):
        self.calls.append((method, path, document, headers))
        operation = LegacyStudyOperator.accept_operation(self.delivery)
        if method == 'GET' and '/cards/' in path: return 200, deepcopy(self.status)
        if method == 'GET' and '/operations/' in path:
            return 200, dict(operation_id=operation, state='complete' if self.accepted else 'queued', response=self.ack)
        if path.endswith('/deliveries'):
            assert document == self.delivery.model_dump(mode='json')
            assert headers['Idempotency-Key'] == operation
            if self.pending: return 202, dict(operation_id=operation, state='queued')
            self.accepted = self.status['accepted'] = self.status['current'] = True
            return 200, deepcopy(self.ack)
        if path.endswith('/queue'):
            return 200, dict(status='deferred', reason='issued_head_preserved', queue_entry_id=None)
        pytest.fail('Unexpected HTTP intent: ' + path)


def test_file_projection_reuses_exact_physical_translation_without_services(tmp_path):
    source = capture()
    result = observe_capture(source)
    assert result['snapshot']['schema_version'] == 2
    assert result['snapshot']['cards'][0]['card_id'] == 'physical-black'
    assert len(result['snapshot']['cards'][0]['moves']) == 6
    assert result['game_source']['raw_source_digest'] == fingerprint(source['game'])
    input_path, output_path = tmp_path / 'capture.json', tmp_path / 'observation.json'
    input_path.write_text(json.dumps(source))
    assert observe_main(['--capture', str(input_path), '--output', str(output_path)]) == 0
    assert json.loads(output_path.read_text()) == result


@pytest.mark.parametrize('fault', ['digest', 'version'])
def test_mixed_capture_file_is_not_projected(fault):
    source = capture()
    if fault == 'digest': source['game']['color'] = 'white'
    else: source['schema_version'] = True
    with pytest.raises(ValueError): observe_capture(source)


def test_accept_replays_original_identity_and_queue_remains_an_explicit_separate_command(tmp_path):
    value = preview(); server = Server(value); operator = LegacyStudyOperator('http://127.0.0.1:8000', server)
    path = tmp_path / 'preview.json'; path.write_text(json.dumps(value))
    assert read_preview(path) == value
    first = operator.accept(value)
    assert first['accepted'] and not first['admission_requested']
    assert operator.accept(value) == first
    posts = [call for call in server.calls if call[0] == 'POST']
    assert len(posts) == 2 and posts[0][2:] == posts[1][2:] and all(call[1].endswith('/deliveries') for call in posts)
    queued = operator.queue(value, 'explicit-queue-1')
    assert queued['response']['reason'] == 'issued_head_preserved' and queued['admission_requested']
    assert server.calls[-1][3] == {'Idempotency-Key': 'explicit-queue-1'}


def test_pending_acceptance_is_not_queue_permission_or_automatic_poll():
    value = preview(); server = Server(value); server.pending = True
    operator = LegacyStudyOperator('http://localhost:8000', server)
    assert operator.accept(value)['transport_state'] == 'queued'
    assert len(server.calls) == 2
    assert operator.queue(value, 'explicit-queue-1')['result'] == 'acceptance_not_completed'
    assert not any(call[1].endswith('/queue') for call in server.calls)


def test_lost_accept_response_is_recovered_only_by_explicit_original_receipt_read():
    value = preview(); server = Server(value)
    def lost(method, path, document=None, headers=None):
        response = server(method, path, document, headers)
        if method == 'POST': raise OSError('synthetic lost response after acceptance')
        return response
    operator = LegacyStudyOperator('http://127.0.0.1:8000', lost)
    with pytest.raises(OSError, match='lost response'): operator.accept(value)
    assert len(server.calls) == 2 and server.accepted
    recovered = operator.receipt(value)
    assert recovered['accepted'] and recovered['operation_id'] == operator.accept_operation(server.delivery)
    assert len(server.calls) == 3 and server.calls[-1][0] == 'GET'
    assert not any(call[1].endswith('/queue') for call in server.calls)


def test_foreign_queue_operation_does_not_count_as_a_delivery_result():
    value = preview(); server = Server(value)
    operator = LegacyStudyOperator('http://127.0.0.1:8000', server)
    operator.accept(value)
    def foreign(method, path, document=None, headers=None):
        if path.endswith('/queue'): return 202, dict(operation_id='foreign-operation', state='queued')
        return server(method, path, document, headers)
    operator.requester = foreign
    with pytest.raises(ValueError, match='queue_operation_mismatch'): operator.queue(value, 'explicit-queue-1')


@pytest.mark.parametrize('result', ['unknown', 'no_miss_obligation', 'unsupported'])
def test_unknown_or_no_miss_never_sends_an_http_write(result):
    value = seal({**preview(), 'result': result, 'delivery': None})
    operator = LegacyStudyOperator('http://127.0.0.1:8000', lambda *a, **k: pytest.fail('No-obligation result called HTTP'))
    assert operator.accept(value)['result'] == operator.queue(value, 'queue-1')['result'] == 'no_obligation'


@pytest.mark.parametrize('fault', ['target_id', 'workspace_id', 'card_id', 'actual_card_revision'])
def test_wrong_target_or_revision_stops_before_acceptance_write(fault):
    value = preview(); server = Server(value); server.status[fault] = 'wrong'
    if fault == 'actual_card_revision': server.status[fault] = True  # JSON boolean is not physical revision1.
    with pytest.raises(ValueError): LegacyStudyOperator('http://127.0.0.1:8000', server).accept(value)
    assert [call[0] for call in server.calls] == ['GET']


def test_changed_saved_preview_and_foreign_receipt_are_rejected(tmp_path):
    value = preview(); path = tmp_path / 'preview.json'
    value['delivery']['encounters'][0]['observed_move'] = 'a7a6'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='file_changed'): read_preview(path)
    value = preview(); server = Server(value); server.ack['evidence_id'] = 'foreign'
    with pytest.raises(ValueError, match='receipt_mismatch'):
        LegacyStudyOperator('http://127.0.0.1:8000', server).accept(value)


@pytest.mark.parametrize('endpoint', ['https://example.org', 'http://example.org', 'http://user:secret@127.0.0.1', 'http://127.0.0.1/api'])
def test_operator_requires_an_explicit_loopback_api_origin(endpoint):
    with pytest.raises(ValueError, match='loopback'): LegacyStudyOperator(endpoint)
