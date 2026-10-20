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


def test_equivalent_ep_capture_to_preview_preserves_later_introduction_unknown():
    """Current repertoire conformance must never manufacture historical recall."""
    from copy import deepcopy
    from datetime import datetime, timezone
    import chess
    from app.legacy_game_observation import capture_game
    from tempo_a_bridge.legacy_observation import observe_capture
    from tempo_game_evidence.legacy_payloads import preview_legacy_observation
    from test_legacy_scoped_capture import source_fixture
    from test_legacy_game_observation import reader_for

    value, source = source_fixture()
    route_moves = ['e2e4','d7d5','e4d5','d8d5','b1c3','d5a5','d2d4','c8f5','d1f3']
    game_moves = route_moves[:-1]+['g1f3']
    route = source.rows['repertoire_lines'][0]
    route.update(trained_color='white', moves_json=json.dumps(route_moves))
    source.rows['repertoire_lines'] = [route]
    original_card, original_graph = source.rows['cards'][0], source.rows['graphs'][0]
    source.rows['cards'], source.rows['graphs'], source.rows['repertoire_cards'] = [], [], []
    for root, introduced, card_id in [(5,'2026-10-15','synthetic-d4'), (7,'2026-10-18','synthetic-qf3')]:
        board = chess.Board()
        for move in route_moves[:root]: board.push_uci(move)
        start_fen = board.fen(en_passant='legal')
        executable = json.dumps(route_moves[root:root+2])
        source.rows['cards'].append({**original_card, 'id':card_id, 'trained_color':'white',
            'start_fen':start_fen, 'moves_json':executable, 'introduced_at':introduced})
        source.rows['graphs'].append({**original_graph, 'card_id':card_id, 'trained_color':'white',
            'starting_fen':start_fen, 'moves_json':executable, 'decision_index':root})
        source.rows['repertoire_cards'].append(dict(repertoire_id='ruy',card_id=card_id))
    source.captured.game.update(color='white', moves_json=json.dumps(game_moves), played_at='2026-10-09T12:00:00Z')
    before = deepcopy(source.rows)
    capture = capture_game('ruy', value.game_id, [6,8], reader=reader_for(source), metadata=source.metadata,
        now=datetime(2026,10,20,tzinfo=timezone.utc))
    observation = observe_capture(capture)
    preview = preview_legacy_observation(observation)
    assert source.rows == before and len(capture['graphs']) == len(capture['tables']['cards']) == 2
    assert observation['mapping']['mapped_identities'] == ['synthetic-d4','synthetic-qf3']
    assert all(not card['introduced_before_game'] for card in observation['snapshot']['cards'])
    assert {binding['card_id'] for route in observation['snapshot']['routes']
            for binding in route['bindings']} == {'synthetic-d4','synthetic-qf3'}
    assert preview['result'] == 'unknown' and preview['delivery'] is None
    scoped = [finding for finding in preview['findings'] if finding['ply'] in (6,8)]
    assert [finding['authored_expected_moves'] for finding in scoped] == [['d2d4'],['d1f3']]
    assert all(finding['physical_binding_complete'] for finding in scoped)
    assert all(finding['status']=='unknown' and finding['explanation']=='legacy_introduction_chronology_unknown'
               for finding in scoped)
