"""Position settlement requires the actual saved target response and reset version."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest

from app.contracts.product_v02.learning import GameLapse
from app.study.game_feedback import Completion
from tempo_legacy_database.adapters.study import position_grade_effects as grades
from test_product_game_lapse import CARD, NOW, Decisions, MemoryStore, fixture, issued_attempt, encounter_owner
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


def test_qualified_intake_applies_position_once_before_receipt(encounter_owner):
    from test_product_game_lapse import qualification_for
    store, _, _, _, service, command = encounter_owner
    store.facts = replace(store.facts, introduced_at=NOW - timedelta(days=1))

    class PositionIntake:
        calls = 0
        def fence_position_intake(self, command, qualification):
            assert not store.effects
            return 'position'
        def apply_position_lapse(self, key, *, day, now):
            self.calls += 1
            assert store.get(key).status == 'pending'
            return 'position-receipt', now, 2

    port = PositionIntake()
    service.position_intake = port
    receipt = service.accept_qualified(command, qualification=qualification_for(command))
    assert receipt.progress.position_grade_effect_id == 'position-receipt'
    assert receipt.progress.scheduling_review_id is None
    assert receipt.progress.remediation_fence is not None
    assert not store.reviews and port.calls == 1
    assert service.refresh_encounter(command.key).reason == 'position_priority_pending'
    assert not store.queue
    # Retry traverses the same pre-lock fence and returns the accepted receipt.
    port.fence_position_intake = lambda command, qualification: 'position'
    replay = service.accept_qualified(command, qualification=qualification_for(command))
    assert replay.outcome == 'duplicate' and port.calls == 1


def test_unseen_position_holds_for_canonical_position_introduction(encounter_owner):
    from test_product_game_lapse import qualification_for
    store, _, _, _, service, command = encounter_owner
    service.position_intake = NS(fence_position_intake=lambda *args: 'position')
    receipt = service.accept_qualified(command, qualification=qualification_for(command))
    assert receipt.reason == 'position_introduction_pending'
    assert service.refresh_encounter(command.key).reason == 'position_introduction_pending'
    assert not store.reviews


def test_game_grade_writes_position_again_with_original_game_identity(monkeypatch):
    from fsrs import Card
    from test_position_game_target_mapping import departure, LONDON_ORDER_A
    from tempo_evidence_math.position_learning import PositionDefinition, PositionKey
    from tempo_legacy_database.adapters.authored_repertoires import position_study_source
    from tempo_legacy_database.adapters.study import game_feedback as feedback_storage, position_owner_epoch

    command, qualification, position = departure(LONDON_ORDER_A)
    definition = PositionDefinition(PositionKey('london', position), 'g1f3', 'london-policy')
    source = NS(authored_token=NS(repertoire_id='london', authored_digest='source'),
                routes=(NS(decisions=(NS(definition=definition),)),))
    effect = NS(status='pending', control=None, qualification=qualification,
                disposition='introduced_again', encounter=command, lapse=None)
    monkeypatch.setattr(position_study_source, 'fence_position_source', lambda *args: None)
    monkeypatch.setattr(position_owner_epoch, 'owner_mode', lambda *args: NS(disposition='position', epoch='epoch'))
    monkeypatch.setattr(feedback_storage, 'PostgresGameFeedbackStore', lambda db: NS(get=lambda key: effect))
    card = Card(card_id=123, due=NOW, last_review=NOW - timedelta(days=1), stability=10, difficulty=5)
    state = dict(expected_uci='g1f3', policy_version='london-policy', version=3,
                 fsrs_internal_card_id=123, fsrs_json=card.to_json())
    monkeypatch.setattr(grades, '_lock_states', lambda *args, **kwargs: {definition.key: state})

    class Writer:
        read_only = False
        raw = NS(info=NS(transaction_status=NS(name='INTRANS')))
        def __init__(self): self.writes, self.prior = [], None
        def execute_native(self, sql, args=()):
            if 'SELECT effect_id,request_digest' in sql: return Result(self.prior)
            if 'SELECT introduced_at' in sql: return Result(dict(introduced_at=NOW - timedelta(days=5)))
            self.writes.append((sql, args))
            if 'INSERT INTO study_position_grades' in sql:
                self.prior = dict(effect_id=args[0], request_digest=args[11], accepted_at=args[12], version_after=args[-1])
            return Result(None)

    db = Writer()
    receipt, accepted, version = grades.apply_game_lapse(db, command.key, prepared_source=source,
        accepted_study_day=NOW.date(), accepted_at=NOW)
    assert accepted == NOW and version == 4 and len(receipt) == 64
    assert len(db.writes) == 2
    sql, args = db.writes[1]
    assert "'game_lapse'" in sql and args[1] == command.key.logical_id()
    assert 'reviews' not in sql and args[-2:] == (3, 4)
    replay = grades.apply_game_lapse(db, command.key, prepared_source=source,
        accepted_study_day=NOW.date(), accepted_at=NOW + timedelta(hours=1))
    assert replay == (receipt, NOW, 4) and len(db.writes) == 2


def test_position_urgency_reads_exact_durable_reset_and_holds_changed_state(monkeypatch):
    from test_position_game_target_mapping import departure, LONDON_ORDER_A
    from tempo_evidence_math.position_learning import Hold
    from tempo_legacy_database.adapters.study import game_feedback as storage
    command, qualification, position = departure(LONDON_ORDER_A)
    progress = NS(position_grade_effect_id='grade', remediation_fence=object(),
                  target=NS(card_id='card', card_revision=1), stream='forward', determined_at=NOW)
    effect = NS(control=None, qualification=qualification, progress=progress,
                encounter=command, lapse=None, key=command.key)
    monkeypatch.setattr(storage, 'installed', lambda db: True)
    monkeypatch.setattr(storage.PostgresGameFeedbackStore, '_effect', lambda row: effect)
    row = dict(effect_id='grade', repertoire_id='london', variant='standard', position=position,
               owner_epoch='epoch', source_digest='digest', expected_uci='g1f3', policy_version='london-policy',
               version_after=4, state_epoch='epoch', state_digest='digest', state_uci='g1f3',
               state_policy='london-policy', state_version=4, introduced_at=NOW)
    class Reader:
        def execute_native(self, sql, args=()):
            if 'SELECT * FROM study_game_feedback' in sql: return Result([{}])
            if 'pg_try_advisory' in sql: return Result((True,))
            if 'FROM study_position_grades' in sql: return Result(row)
            raise AssertionError(sql)
    db = Reader()
    fact = storage.read_position_urgency(db, card_id='card', revision=1, repertoire_id='london')
    assert fact.definition.key.position == position and fact.position_version == 4
    assert fact.grade_effect_id == 'grade'
    assert storage.read_position_urgency(db, card_id='card', revision=1, repertoire_id='london', lock=True) == fact
    row['state_digest'] = 'changed'
    assert isinstance(storage.read_position_urgency(db, card_id='card', revision=1, repertoire_id='london'), Hold)

    class TooMany:
        def execute_native(self, sql, args=()):
            assert 'LIMIT 129' in sql
            return Result([{}] * 129)
    assert storage.read_position_urgency(TooMany(), card_id='card', revision=1,
                                         repertoire_id='london').reason == 'position_urgency_candidate_limit'
