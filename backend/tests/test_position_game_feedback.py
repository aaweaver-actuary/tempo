"""Position settlement requires the actual saved target response and reset version."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest

from app.contracts.product_v02.learning import GameLapse
from app.study.game_feedback import Completion
from tempo_legacy_database.adapters.study import position_grade_effects as grades
from test_product_game_lapse import CARD, NOW, Decisions, MemoryStore, fixture, issued_attempt
from app.study.game_feedback import GameFeedback


def test_position_settlement_requires_owner_proof_and_post_reset_issuance():
    store = MemoryStore()
    service = GameFeedback(store, Decisions(), clock=lambda: NOW)
    lapse = fixture('lapse', GameLapse)
    service.accept_lapse(lapse)
    completion = Completion(100, CARD, 1, NOW + timedelta(hours=1), False, 'position_study',
                            issued_attempt(CARD, 1, 8, NOW + timedelta(minutes=1)))
    # Aggregate correctness and a source-kind string alone provide no authority.
    service.settle(completion)
    assert store.get(lapse.key).status == 'applied'
    store.position_completion_matches = lambda effect, completion: True
    service.settle(replace(completion, issuance=issued_attempt(CARD, 1, 7, NOW)))
    assert store.get(lapse.key).status == 'applied'
    service.settle(completion)
    assert store.get(lapse.key).status == 'settled'


class Result:
    def __init__(self, data): self.data = data
    def fetchone(self): return self.data
    def fetchall(self): return self.data


class Database:
    def __init__(self, reset): self.reset = reset
    def execute_native(self, sql, args):
        if 'review_attempt_receipts' in sql:
            return Result(dict(attempt_id='a', queue_entry_id=1, source_digest='digest'))
        if 'study_position_first_responses' in sql:
            return Result([dict(event_id='event')])
        if "source_kind='study'" in sql:
            return Result(dict(grade='observe'))
        if "source_kind='game_lapse'" in sql:
            return Result(self.reset)
        raise AssertionError(sql)


@pytest.mark.parametrize('assisted,correct,disposition,version,expected', [
    (False, True, 'expected', 4, True),
    (True, True, 'expected', 4, False),
    (False, False, 'wrong', 4, False),
    (False, True, 'expected', 3, False),
])
def test_position_response_must_be_unassisted_exact_and_after_reset(monkeypatch, assisted, correct,
                                                                  disposition, version, expected):
    key = NS(repertoire_id='rep', variant='standard', position='fen')
    definition = NS(key=key, expected_uci='e2e4')
    plan = NS(attempt_id='a', due_positions=(NS(definition=definition, position_revision=version),))
    response = NS(definition=definition, assistance_before=assisted, manual_failure=False,
                  correct=correct, event_id='event')
    monkeypatch.setattr(grades, '_load_plan', lambda *a: plan)
    monkeypatch.setattr(grades, '_completion_saved', lambda *a: True)
    monkeypatch.setattr(grades, '_load_events', lambda *a: ((response, disposition, 'digest', NOW),))
    effect = NS(qualification=None, lapse=NS(finding=NS(repertoire_id='rep', position_key='fen',
                  expected_uci=('e2e4',))), key=NS(logical_id=lambda: 'game-key'))
    completion = NS(review_id=1, card_id='card', revision=1)
    db = Database(dict(version_after=4, repertoire_id='rep', variant='standard', position='fen'))
    assert grades.position_completion_matches(db, effect, completion) is expected
