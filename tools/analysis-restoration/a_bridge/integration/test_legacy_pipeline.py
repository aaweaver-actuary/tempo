"""Explicit cross-component SOURCE integration; not an installed/native claim.

Real capture, projection, GE producer, semantic validation, shared remediation
application and SQLite Study writers. HTTP/Celery/operation transport and module
SQL rows are scripted; PostgreSQL atomicity/locking requires its separate gate.
"""
import json
from urllib.parse import unquote

import pytest
from tempo_learning_remediation import PostgresRepository
from tempo_a_bridge.legacy_study import LegacyStudyOperator
from app import game_evidence_commands as commands
from app.services import legacy_study_remediation as bridge
from test_legacy_study_remediation import (
    vertical, issue, complete,
    test_synthetic_accepted_physical_card_uses_normal_study_issuance_and_exactly_once_review as normal_study_flow,
)


@pytest.mark.parametrize('vertical', ['producer'], indirect=True)
@pytest.mark.parametrize('prior_attempt', [False, True])
def test_generated_one_game_preview_explicit_accept_queue_and_normal_study(vertical, prior_attempt, tmp_path):
    db, delivery, _accepted, module = vertical
    preview = db.generated_preview
    repository = PostgresRepository(module, 'legacy')
    calls, operation_receipts = [], {}
    def transport(method, path, document=None, headers=None):
        calls.append((method, path))
        if method == 'GET' and '/cards/' in path:
            return 200, bridge.card_status(db, unquote(path.rsplit('/', 1)[-1]))
        if method == 'GET' and '/operations/' in path:
            operation = unquote(path.rsplit('/', 1)[-1])
            return 200, dict(operation_id=operation, state='complete', response=operation_receipts[operation])
        if path.endswith('/deliveries'):
            assert document == delivery.model_dump(mode='json')
            result = repository.receive(delivery, db.receive_context).model_dump(mode='json')
            operation_receipts[headers['Idempotency-Key']] = result
            return 200, result
        if path.endswith('/queue'):
            result = commands.admit_legacy_card(db, {'card_id': 'physical-black'})
            operation_receipts[headers['Idempotency-Key']] = result
            return 200, result
        pytest.fail('Unexpected transport: ' + path)
    operator = LegacyStudyOperator('http://127.0.0.1:8000', transport)
    before = operator.status(preview)
    assert not before['accepted'] and module.sequence == 0 and not module.attempts
    assert db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 0
    accepted = operator.accept(preview)
    assert accepted['accepted'] and not accepted['admission_requested']
    assert operator.accept(preview) == accepted  # Immutable response-loss replay.
    assert module.sequence == module.workspace_revision == len(module.contributions) == len(module.deliveries) == 1
    assert not module.attempts and db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='physical-black'").fetchone()[0] == 0
    assert operator.receipt(preview) == accepted
    queued = operator.queue(preview, 'explicit-source-queue')
    assert queued['response']['status'] == 'applied'
    status = operator.status(preview)
    assert status['accepted'] and status['current'] and status['settlement'] == 'unsettled'
    assert status['played_move'] == 'g8f6' and status['expected_moves'] == ['a7a6']
    assert status['admission']['waiting_behind_protected_head'] and not module.attempts
    entry = queued['response']['queue_entry_id']
    assert issue(db, 'physical-black', entry) == {'issued': False} and not module.attempts
    if prior_attempt:
        db.execute("UPDATE daily_queue SET status='complete' WHERE card_id='protected'")
        db.execute("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=?", (entry,))
        assert issue(db, 'physical-black', entry)['issued'] and not module.attempts
        assert complete(db, entry)['persisted']
        assert not module.receipts and not module.settlements and module.outstanding
    else:
        normal_study_flow(vertical)  # Fresh head -> one review/settlement -> replay -> invalidation.
        assert len(module.attempts) == len(module.receipts) == len(module.settlements) == 1
    trace = dict(source_only=True, scripted=['HTTP/Celery/operation transport', 'module PostgreSQL rows'],
        result=preview['result'], game_id=delivery.game_id, physical_card_id=delivery.obligations[0].card_id,
        exercise_ply=delivery.comparisons[0].exercise_ply,
        played=delivery.encounters[0].observed_move, expected=delivery.comparisons[0].expected_moves,
        accepted=accepted, queued=queued, waiting_status=status, prior_attempt=prior_attempt,
        frontier=module.sequence, bindings=len(module.attempts), reviews=db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0],
        review_receipts=len(module.receipts), settlements=len(module.settlements),
        settlement_receipts=[dict(review_id=key, attempt_id=item[0], invalidated=item[1])
                             for key, item in module.settlements.items()],
        outstanding_after_control=module.outstanding, calls=calls)
    (tmp_path / 'one-game-pipeline-trace.json').write_text(json.dumps(trace, indent=2))
