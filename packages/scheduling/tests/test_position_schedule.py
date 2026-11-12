import importlib
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fsrs import Card, Rating, Scheduler, State
from tempo_scheduling import initial_position_state, InitialPositionState, clone_state, schedule_position_review, schedule_review


ACCEPTED_AT = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
STUDY_DAY = date(2026, 11, 3)
DONOR = dict(card_id=81, state=2, step=None, stability=12.5, difficulty=5.2,
             due='2026-11-01T12:00:00+00:00', last_review='2026-10-20T12:00:00+00:00')
INITIAL = dict(card_id=82, state=1, step=0, stability=None, difficulty=None,
               due='2026-11-01T12:00:00+00:00', last_review=None)


@pytest.mark.parametrize('offset', [0, -7, 5.5])
def test_fresh_position_state_preserves_allocated_identity_and_due_without_review(offset):
    due = ACCEPTED_AT.astimezone(timezone(timedelta(hours=offset)))
    state = initial_position_state(4503599627370496, due)
    assert isinstance(state, InitialPositionState)
    expected = Card(card_id=4503599627370496, due=due)
    assert state.fsrs_card_json == expected.to_json()
    card = Card.from_json(state.fsrs_card_json)
    assert card.card_id == 4503599627370496 and card.due == due == state.due_at
    assert card.state == State.Learning and card.step == 0
    assert card.last_review is card.stability is card.difficulty is None
    scheduled = schedule_position_review('good', fsrs_card_json=state.fsrs_card_json,
        accepted_at=due, review_day=STUDY_DAY)
    assert Card.from_json(scheduled.fsrs_card_json).card_id == card.card_id
    assert Card.from_json(scheduled.fsrs_card_json).last_review == due


@pytest.mark.parametrize('identity', [None, True, False, 0, -1, '1', 1.0])
def test_fresh_position_requires_allocated_positive_integer(identity):
    with pytest.raises(ValueError, match='allocated_card_id'):
        initial_position_state(identity, ACCEPTED_AT)


@pytest.mark.parametrize('due', [None, ACCEPTED_AT.replace(tzinfo=None), '2026-11-02'])
def test_fresh_position_requires_explicit_aware_instant(due):
    with pytest.raises(ValueError, match='due_at'):
        initial_position_state(901, due)


def blob(**changes):
    return json.dumps({**DONOR, **changes})


def transition(grade='good', **changes):
    inputs = dict(fsrs_card_json=blob(), accepted_at=ACCEPTED_AT, review_day=STUDY_DAY)
    return schedule_position_review(grade, **{**inputs, **changes})


@pytest.mark.parametrize('memory', [DONOR, INITIAL,
    {**DONOR, 'state': 1, 'step': 1}, {**DONOR, 'state': 3, 'step': 0}])
def test_two_position_clones_preserve_complete_donor_memory_with_new_ids(memory):
    original = json.dumps(memory)
    clones = [Card.from_json(clone_state(original, allocated_new_card_id=i)) for i in (901, 902)]
    assert [card.card_id for card in clones] == [901, 902]
    for card in clones:
        assert {**card.to_dict(), 'card_id': memory['card_id']} == memory
    assert json.loads(original) == memory


def test_clone_resets_physical_metadata_without_mutating_donor():
    original = blob(first_correct_at='2020-01-01T00:00:00+00:00', reinforcement_pending=True,
        scheduling_mode='hard', hard_correct_streak=2, recent_attempts=['again'],
        requeue_today=True, requeue_after_cards=4, suggest_shorter_prefix=True,
        light_first_interval_days=7)
    result = json.loads(clone_state(original, allocated_new_card_id=901))
    assert result == {**DONOR, 'card_id': 901}
    assert json.loads(original)['reinforcement_pending'] is True


@pytest.mark.parametrize('field', list(DONOR))
def test_incomplete_position_card_is_held(field):
    incomplete = dict(DONOR)
    del incomplete[field]
    with pytest.raises(ValueError, match='complete card state'):
        clone_state(json.dumps(incomplete), allocated_new_card_id=901)


@pytest.mark.parametrize('changes', [
    {'card_id': None}, {'card_id': True}, {'card_id': '81'}, {'card_id': 0},
    {'state': 0}, {'state': 4}, {'state': True}, {'state': '2'},
    {'step': 0}, {'state': 1, 'step': None}, {'state': 3, 'step': -1},
    {'state': 1, 'step': True}, {'stability': 0}, {'stability': -1},
    {'stability': float('nan')}, {'stability': float('inf')},
    {'stability': '12.5'}, {'stability': True}, {'difficulty': 0},
    {'difficulty': 11}, {'difficulty': None}, {'last_review': None},
    {'stability': None, 'difficulty': None, 'last_review': None},
    {'due': None}, {'due': ''}, {'due': '2026-11-01T12:00:00'},
    {'last_review': '2026-10-20T12:00:00'}, {'last_review': ''},
    {'due': '2026-10-19T12:00:00+00:00'}, {'version': '7.0.0'},
    {'fsrs_version': '6.3.1'}, {'unknown': 'ignored by the permissive decoder'},
])
def test_invalid_position_card_is_held(changes):
    with pytest.raises(ValueError, match='invalid position fsrs_json'):
        clone_state(blob(**changes), allocated_new_card_id=901)
    with pytest.raises(ValueError, match='invalid position fsrs_json'):
        transition(fsrs_card_json=blob(**changes))


@pytest.mark.parametrize('bad_blob', [None, '', 'null', '[]', '{}', 'not json',
    blob().replace('"card_id": 81', '"card_id": 81, "card_id": 82')])
def test_invalid_serialized_position_card_is_held(bad_blob):
    with pytest.raises(ValueError, match='invalid position fsrs_json'):
        clone_state(bad_blob, allocated_new_card_id=901)


@pytest.mark.parametrize('new_id', [None, True, False, 0, -1, 81, '901', 901.0])
def test_new_identity_must_be_positive_and_different(new_id):
    with pytest.raises(ValueError, match='allocated_new_card_id'):
        clone_state(blob(), allocated_new_card_id=new_id)


def test_unsupported_fsrs_version_is_held(monkeypatch):
    module = importlib.import_module('tempo_scheduling._position_schedule')
    monkeypatch.setattr(module, '_FSRS_VERSION', '7.0.0')
    with pytest.raises(ValueError, match='unsupported FSRS version'):
        clone_state(blob(), allocated_new_card_id=901)
    with pytest.raises(ValueError, match='unsupported FSRS version'):
        transition()
    with pytest.raises(ValueError, match='unsupported FSRS version'):
        initial_position_state(901, ACCEPTED_AT)


@pytest.mark.parametrize('grade,rating', [('good', Rating.Good), ('again', Rating.Again)])
@pytest.mark.parametrize('memory', [DONOR, INITIAL])
def test_position_grade_matches_one_native_fsrs_transition_at_acceptance(monkeypatch, grade, rating, memory):
    scheduler = Scheduler(desired_retention=.92, learning_steps=(), relearning_steps=(),
        maximum_interval=365, enable_fuzzing=False)
    expected, _ = scheduler.review_card(Card.from_dict(memory), rating, ACCEPTED_AT)
    original_review = Scheduler.review_card
    calls = []
    def record_call(self, card, actual_rating, review_datetime):
        calls.append((actual_rating, review_datetime))
        return original_review(self, card, actual_rating, review_datetime)
    monkeypatch.setattr(Scheduler, 'review_card', record_call)
    actual = transition(grade, fsrs_card_json=json.dumps(memory))
    assert calls == [(rating, ACCEPTED_AT)]
    assert actual.fsrs_card_json == expected.to_json()
    assert actual.due_at == expected.due
    assert actual.interval_days == (expected.due - ACCEPTED_AT).days
    assert actual.due_date == STUDY_DAY + timedelta(days=actual.interval_days)
    assert Card.from_json(actual.fsrs_card_json).last_review == ACCEPTED_AT
    assert actual.internal_rating == grade


def test_first_clean_position_has_no_positive_reinforcement_requeue():
    result = transition(fsrs_card_json=json.dumps(INITIAL))
    assert result.requeue_today is False
    assert result.reinforcement_pending is False
    assert result.first_correct_at is None
    assert result.scheduling_mode == 'normal'
    assert result.hard_correct_streak == 0
    assert result.interval_days >= 1
    assert result.due_date > STUDY_DAY
    # No daily ledger claim here: the writer must reject a second unauthorized Good.


def test_position_operations_do_not_call_legacy_scheduler_or_environment(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('ambient API or legacy scheduling invoked')
    class NoClock(datetime):
        now = today = utcnow = classmethod(forbidden)
    for name in ('fsrs.card', 'fsrs.scheduler'):
        module = importlib.import_module(name)
        monkeypatch.setattr(module, 'datetime', NoClock)
    monkeypatch.setattr('time.sleep', forbidden)
    monkeypatch.setattr('time.time', forbidden)
    monkeypatch.setattr('tempo_scheduling._scheduler.schedule_review', forbidden)
    monkeypatch.setattr('tempo_scheduling._scheduler._scheduler', forbidden)
    cloned = clone_state(blob(), allocated_new_card_id=901)
    assert transition(fsrs_card_json=cloned) == transition(fsrs_card_json=cloned)
    assert initial_position_state(902, ACCEPTED_AT) == initial_position_state(902, ACCEPTED_AT)


def test_position_transition_is_pure_and_preserves_legacy_behavior():
    serialized = json.dumps(INITIAL)
    inputs = dict(fsrs_card_json=serialized, reviewed_at=ACCEPTED_AT, review_day=STUDY_DAY)
    legacy_before = schedule_review('correct', **inputs)
    assert transition(fsrs_card_json=serialized) == transition(fsrs_card_json=serialized)
    assert schedule_review('correct', **inputs) == legacy_before
    assert legacy_before.requeue_today and legacy_before.reinforcement_pending
    assert Card.from_json(serialized).state == State.Learning


def test_aware_offsets_preserve_donor_and_acceptance_instants():
    offset = timezone(timedelta(hours=-7))
    memory = {**DONOR, **{field: datetime.fromisoformat(DONOR[field]).astimezone(offset).isoformat()
                          for field in ('due', 'last_review')}}
    cloned = Card.from_json(clone_state(json.dumps(memory), allocated_new_card_id=901))
    assert cloned.due == Card.from_dict(DONOR).due
    assert cloned.last_review == Card.from_dict(DONOR).last_review
    assert transition(accepted_at=ACCEPTED_AT.astimezone(offset)) == transition()


@pytest.mark.parametrize('changes', [
    {'accepted_at': None}, {'accepted_at': ACCEPTED_AT.replace(tzinfo=None)},
    {'accepted_at': datetime(2026, 10, 1, tzinfo=timezone.utc)},
    {'review_day': None}, {'review_day': ACCEPTED_AT}, {'review_day': '2026-11-03'},
])
def test_invalid_acceptance_context_is_held(changes):
    with pytest.raises(ValueError):
        transition(**changes)


@pytest.mark.parametrize('grade', ['correct', 'hard', 'easy', 'observe', None])
def test_position_grade_requires_authorized_good_or_again(grade):
    with pytest.raises(ValueError, match='grade must be good or again'):
        transition(grade)
