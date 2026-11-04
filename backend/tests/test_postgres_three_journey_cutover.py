"""Synthetic cutover classification; native migration proof is separately required."""

import copy

import pytest

from database.tooling.verify_postgres_cli_state import (
    propose_three_journey_card_deletes,
    three_journey_expected_rows,
    verify_three_journey_rows,
)


def mixed_fixture():
    return {
        'cards': [
            {'id': 'opening', 'content_type': 'opening'},
            {'id': 'puzzle-rush-defense-theme', 'content_type': 'tactic'},
            {'id': 'defense', 'content_type': 'defense'},
            {'id': 'shared', 'content_type': 'defense'},
            {'id': 'unknown', 'content_type': 'future-type'},
        ],
        'daily_queue': [
            {'id': 1, 'card_id': 'opening', 'status': 'issued'},
            {'id': 2, 'card_id': 'defense', 'status': 'issued'},
            {'id': 3, 'card_id': 'shared', 'status': 'pending'},
        ],
        'queue_attempt_origins': [
            {'queue_entry_id': 1, 'card_id': 'opening', 'content_type': 'opening'},
            {'queue_entry_id': 2, 'card_id': 'defense', 'content_type': 'defense'},
            {'queue_entry_id': 3, 'card_id': 'shared', 'content_type': 'tactic'},
        ],
        'tactic_captures': [{'id': 'capture', 'card_id': 'puzzle-rush-defense-theme',
                             'source_kind': 'puzzle_rush', 'request_json': '{"theme":"defense"}'}],
        'tactic_discovery_attempts': [{'id': 'pack-receipt', 'result_json': '{"clean":true}'}],
        'reviews': [{'id': 1, 'card_id': 'opening', 'rating': 'again'}],
        'operation_receipts': [
            {'operation_id': 'phone', 'command_name': 'review.submit', 'state': 'pending'},
            {'operation_id': 'retired', 'command_name': 'defense.attempt.submit', 'state': 'pending'},
            {'operation_id': 'mixed', 'command_name': 'unknown', 'response_json': '{"card_id":"defense"}'},
        ],
        'study_game_feedback': [{'effect_id': 'effect', 'state': 'held', 'card_id': 'opening'}],
        'imported_games': [{'id': 'game', 'pgn': '[FEN "synthetic"]'}],
    }


def test_three_journey_mixed_ownership_preserves_uncertainty():
    data = mixed_fixture()
    result = propose_three_journey_card_deletes(
        data['cards'], {'defense', 'shared'}, {'shared', 'puzzle-rush-defense-theme'},
        data['queue_attempt_origins'])
    assert result['proposed_card_ids'] == ['defense']
    assert 'shared' in result['ambiguous']
    assert 'unknown' not in result['proposed_card_ids']
    assert 'puzzle-rush-defense-theme' not in result['proposed_card_ids']


def test_three_journey_exact_witness_rejects_survivor_tamper_and_resurrection():
    source = mixed_fixture()
    keys = {name: ('id',) for name in source}
    keys.update(queue_attempt_origins=('queue_entry_id',),
                operation_receipts=('operation_id',), study_game_feedback=('effect_id',))
    deletes = {'cards': [('defense',)], 'daily_queue': [(2,)],
               'queue_attempt_origins': [(2,)]}
    witness = three_journey_expected_rows(source, keys, deletes)
    target = copy.deepcopy(source)
    for table, proposed in deletes.items():
        target[table] = [row for row in target[table]
                         if tuple(row[column] for column in keys[table]) not in proposed]
    verify_three_journey_rows(witness, target)
    target['study_game_feedback'][0]['state'] = 'applied'
    with pytest.raises(ValueError, match='study_game_feedback'):
        verify_three_journey_rows(witness, target)
    target = copy.deepcopy(source)
    with pytest.raises(ValueError):
        verify_three_journey_rows(witness, target)


def test_three_journey_unknown_delete_key_is_rejected():
    with pytest.raises(ValueError, match='unknown proposed key'):
        three_journey_expected_rows({'cards': [{'id': 'kept'}]}, {'cards': ('id',)},
                                   {'cards': [('missing',)]})
