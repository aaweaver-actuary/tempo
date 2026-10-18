"""Prepared three-case real HTTP/Celery/Redis/PostgreSQL boundary proof.

Never runs in regular source tests. Native fixture setup fails closed without
the separately granted owned helper manifest. No mocks or eager Celery mode.
"""
import json

import pytest
from psycopg import sql
from tempo_a_bridge.legacy_study import LegacyStudyOperator
from tempo_game_evidence_contracts import v3


def accepted(native, operator, preview):
    first = operator.accept(preview)
    if not first['accepted']: native.completed(first['operation_id'])
    receipt = operator.receipt(preview)
    assert receipt['accepted'] and not receipt['admission_requested']
    return receipt


def review(native, card, attempt):
    body = dict(outcome='correct', guided=False, queue_entry_id=card['queue_entry_id'],
                expected_revision=card['revision'], attempt_id=attempt)
    return native.post('/api/cards/' + card['id'] + '/review', body, attempt + '-transport'), body


def status(native, operator, preview):
    value = operator.status(preview)
    assert value['workspace_id'] == 'legacy' and value['card_id'] == native.identities['physical']
    return value


def write_trace(native, **value):
    document = dict(native_boundaries=['real loopback HTTP', 'separate foreground/background Celery processes',
        'owned Redis broker/result/admission', 'real module PostgreSQL SQL and backend transactions'],
        synthetic_graph_integrity_owner_facts=True, case_id=native.case_id, counts=native.counts(),
        calls=native.calls, **value)
    (native.directory / 'native-trace.json').write_text(json.dumps(document, indent=2, default=str))


def test_native_generated_accept_replay_restart_and_normal_study(native):
    preview = native.preview()
    operator = LegacyStudyOperator(native.endpoint, native.request)
    assert not status(native, operator, preview)['accepted']
    assert native.counts()['frontier'] == 0
    with native.connection() as raw:
        ordinary = raw.execute('SELECT row_to_json(card) FROM cards card WHERE id=%s', (native.identities['ordinary'],)).fetchone()[0]
    receipt = accepted(native, operator, preview)
    # Discard the caller's first response, restart a real command process, and
    # recover through the durable operation receipt and the identical POST.
    native.stop('background'); native.worker('background')
    assert operator.receipt(preview) == receipt
    assert accepted(native, operator, preview) == receipt
    delivery = v3.EvidenceDelivery.model_validate(preview['delivery'])
    transport = delivery.model_copy(update={'delivery_id': delivery.delivery_id + ':fresh-transport'})
    second = native.post('/api/game-evidence/legacy/deliveries', transport.model_dump(mode='json'), native.case_id + '-new-transport')
    assert second['status'] == 'completed' and second['learning_revision'] == receipt['acknowledgement']['learning_revision']
    counts = native.counts()
    assert counts['frontier'] == counts['remediation_contributions'] == counts['remediation_incident_versions'] == counts['remediation_evidence'] == 1
    assert counts['remediation_deliveries'] == 2 and counts['remediation_attempts'] == counts['target_reviews'] == 0
    admitted = operator.queue(preview, native.case_id + '-queue')
    result = admitted['response']
    if result.get('state') != 'complete' and 'operation_id' in result: result = native.completed(result['operation_id'])
    assert result['status'] == 'applied'
    current = status(native, operator, preview)
    assert current['accepted'] and current['current'] and current['settlement'] == 'unsettled'
    assert current['played_move'] == 'g8f6' and current['expected_moves'] == ['a7a6']
    assert current['admission']['waiting_behind_protected_head']
    window = native.queue(2)['cards']
    assert [card['id'] for card in window] == [native.identities['protected'], native.identities['physical']]
    assert native.counts()['remediation_attempts'] == 0  # Actual normal prefetch did not issue the target.
    review(native, window[0], native.case_id + '-protected-review')
    target = native.queue(1)['cards'][0]
    assert target['id'] == native.identities['physical'] and target['queue_entry_id'] == result['queue_entry_id']
    with native.connection() as raw:
        bound = raw.execute('SELECT card_id,card_revision,frontier_sequence FROM remediation_attempts').fetchall()
        assert bound == [(target['id'], 1, 1)]
        assert raw.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s AND revision=1', (target['queue_entry_id'],)).fetchone() == (1,)
        assert json.loads(raw.execute('SELECT moves_json FROM cards WHERE id=%s', (target['id'],)).fetchone()[0]) == ['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6']
    attempt = native.case_id + '-target-review'
    saved, body = review(native, target, attempt)
    assert saved['persisted'] and saved['review_id'] is not None
    assert native.post('/api/cards/' + target['id'] + '/review', body, attempt + '-transport') == saved
    assert native.post('/api/cards/' + target['id'] + '/review', body, attempt + '-new-transport')['review_id'] == saved['review_id']
    settled = status(native, operator, preview)
    assert settled['settlement'] == 'settled'
    counts = native.counts()
    assert counts['target_reviews'] == counts['remediation_attempts'] == counts['remediation_review_receipts'] == counts['remediation_settlements'] == counts['frontier'] == 1
    with native.connection() as raw:
        assert raw.execute('SELECT row_to_json(card) FROM cards card WHERE id=%s', (native.identities['ordinary'],)).fetchone()[0] == ordinary
        assert raw.execute('SELECT command_name,background,state FROM operation_receipts WHERE operation_id=%s', (receipt['operation_id'],)).fetchone() == ('game_evidence.receive_legacy', True, 'complete')
        assert raw.execute('SELECT command_name,background,state FROM operation_receipts WHERE operation_id=%s', (native.case_id + '-queue',)).fetchone() == ('game_evidence.admit_legacy', False, 'complete')
        assert raw.execute('SELECT COUNT(*) FROM review_attempt_receipts WHERE card_id=%s', (target['id'],)).fetchone()[0] == 1
    write_trace(native, acknowledgement=receipt, admission=result, status=settled, review_id=saved['review_id'], worker_restarted=True)


@pytest.mark.parametrize('native', ['prior'], indirect=True)
def test_native_prior_issued_attempt_cannot_settle_later_acceptance(native):
    target = native.queue(1)['cards'][0]  # Genuine issuance precedes acceptance.
    assert target['id'] == native.identities['physical']
    preview = native.preview(); operator = LegacyStudyOperator(native.endpoint, native.request)
    receipt = accepted(native, operator, preview)
    queued = operator.queue(preview, native.case_id + '-queue')['response']
    if 'operation_id' in queued: queued = native.completed(queued['operation_id'])
    assert queued['status'] == 'deferred' and queued['reason'] == 'issued_head_preserved'
    saved, _body = review(native, target, native.case_id + '-prior-review')
    assert saved['persisted'] and native.counts()['target_reviews'] == 1
    counts = native.counts()
    assert counts['frontier'] == counts['remediation_contributions'] == 1
    assert counts['remediation_attempts'] == counts['remediation_review_receipts'] == counts['remediation_settlements'] == 0
    assert status(native, operator, preview)['settlement'] == 'unsettled'
    write_trace(native, acknowledgement=receipt, admission=queued, prior_issued=True, review_id=saved['review_id'])


def test_native_module_effect_and_complete_operation_receipt_roll_back_together(native):
    preview = native.preview(); operator = LegacyStudyOperator(native.endpoint, native.request)
    delivery = v3.EvidenceDelivery.model_validate(preview['delivery'])
    operation = operator.accept_operation(delivery)
    with native.connection() as raw:
        raw.execute("""CREATE FUNCTION bridge_native_fail_complete() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.operation_id=TG_ARGV[0] AND NEW.state='complete' THEN
              RAISE EXCEPTION 'synthetic outer receipt publication failure' USING ERRCODE='40001';
            END IF; RETURN NEW; END $$""")
        raw.execute(sql.SQL("CREATE TRIGGER bridge_native_fail_complete BEFORE UPDATE ON operation_receipts FOR EACH ROW EXECUTE FUNCTION bridge_native_fail_complete({})").format(sql.Literal(operation)))
    first = operator.accept(preview)
    assert not first['accepted']
    retry = native.wait(lambda: (receipt if (receipt := native.operation(operation))['state'] == 'retrying' else None))
    assert retry.get('response') is None
    assert all(count == 0 for count in native.counts().values())
    with native.connection() as raw:
        saved = raw.execute('SELECT command_name,payload_json,response_json,attempt_count FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()
        assert saved[0] == 'game_evidence.receive_legacy' and json.loads(saved[1]) == {'delivery': preview['delivery']}
        assert saved[2] is None and saved[3] >= 1
        raw.execute('DROP TRIGGER bridge_native_fail_complete ON operation_receipts')
        raw.execute('DROP FUNCTION bridge_native_fail_complete()')
    def retry_due():
        with native.connection() as raw:
            return raw.execute('SELECT next_retry_at<=NOW() FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()[0]
    native.wait(retry_due, seconds=10)
    receipt = accepted(native, operator, preview)
    counts = native.counts()
    assert counts['frontier'] == counts['remediation_contributions'] == counts['remediation_deliveries'] == counts['remediation_evidence'] == 1
    with native.connection() as raw:
        assert raw.execute('SELECT state,attempt_count FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()[0] == 'complete'
    write_trace(native, acknowledgement=receipt, retained_retry=retry, rollback_before_retry=True)
