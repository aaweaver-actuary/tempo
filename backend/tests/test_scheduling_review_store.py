"""Scheduling wrapper characterization and owned-connection adapter contracts."""
from datetime import date, datetime, timezone
import json
import sqlite3

import pytest

from app.services import review_service


@pytest.fixture
def scheduling_database(monkeypatch):
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.executescript('''
        CREATE TABLE cards(id TEXT PRIMARY KEY, archived INTEGER DEFAULT 0,
            due_date TEXT, interval_days INTEGER DEFAULT 0, fsrs_card_json TEXT,
            first_correct_at TEXT, reinforcement_pending INTEGER DEFAULT 0,
            stability REAL DEFAULT 0, guided_review INTEGER DEFAULT 0,
            state TEXT DEFAULT 'learning', scheduling_mode TEXT DEFAULT 'normal',
            hard_correct_streak INTEGER DEFAULT 0, recent_attempts_json TEXT DEFAULT '[]');
        CREATE TABLE reviews(id INTEGER PRIMARY KEY, card_id TEXT, rating TEXT,
            internal_rating INTEGER, guided INTEGER, reviewed_at TEXT,
            previous_interval INTEGER, next_interval INTEGER, source_kind TEXT,
            source_ref TEXT, invalidated_at TEXT);
        CREATE TABLE opening_card_schedule_seeds(card_id TEXT PRIMARY KEY,
            baseline_successful_days INTEGER, baseline_recent_clean INTEGER);
        INSERT INTO cards(id,due_date) VALUES('card','2026-01-01');
    ''')
    monkeypatch.setattr(review_service, 'unlock_legacy_children_after_review', lambda *args: None)
    from app.services import real_game_feedback_adapter as real_game_feedback
    monkeypatch.setattr(real_game_feedback, 'clear_satisfied_miss_priority', lambda *args: None)
    try:
        yield connection
    finally:
        connection.close()


def apply_review(connection, **changes):
    arguments = dict(guided=False, source_kind='study', source_ref='source',
                     light_first_interval_days=7,
                     reviewed_at=datetime(2026, 1, 2, 23, 30, tzinfo=timezone.utc),
                     review_day=date(2026, 1, 2))
    arguments.update(changes)
    return review_service.apply_scheduling_review(connection, 'card', 'correct', **arguments)


def test_scheduling_wrapper_preserves_history_clock_update_and_result(scheduling_database):
    result = apply_review(scheduling_database)
    assert result == dict(card_id='card', next_due='2026-01-02', interval_days=0,
                         state='learning', requeue_today=True, requeue_after_cards=None,
                         stability=2.3065, scheduling_mode='normal', hard_correct_streak=0,
                         suggest_shorter_prefix=False, idempotent=False)
    history = dict(scheduling_database.execute('SELECT * FROM reviews').fetchone())
    assert history == dict(id=1,card_id='card',rating='correct',internal_rating='good',
                           guided=0,reviewed_at='2026-01-02T23:30:00+00:00',
                           previous_interval=0,next_interval=0,source_kind='study',
                           source_ref='source',invalidated_at=None)
    card = dict(scheduling_database.execute('SELECT * FROM cards').fetchone())
    assert card['due_date'] == result['next_due']
    assert card['interval_days'] == result['interval_days']
    assert card['stability'] == result['stability']
    assert card['first_correct_at'] == history['reviewed_at']
    assert json.loads(card['recent_attempts_json']) == ['correct']


def test_scheduling_wrapper_source_replay_reads_current_archived_card_without_followups(scheduling_database, monkeypatch):
    apply_review(scheduling_database)
    scheduling_database.execute("UPDATE cards SET archived=1,due_date='2030-02-03',interval_days=88,state='mature',stability=42,scheduling_mode='light',hard_correct_streak=3,recent_attempts_json='malformed'")
    def forbidden(*args):
        raise AssertionError('source replay ran a follow-up')
    monkeypatch.setattr(review_service, 'unlock_legacy_children_after_review', forbidden)
    from app.services import real_game_feedback_adapter as real_game_feedback
    monkeypatch.setattr(real_game_feedback, 'clear_satisfied_miss_priority', forbidden)
    assert apply_review(scheduling_database) == dict(card_id='card',next_due='2030-02-03',interval_days=88,state='mature',requeue_today=True,requeue_after_cards=4,stability=42.0,scheduling_mode='light',hard_correct_streak=3,suggest_shorter_prefix=False,idempotent=True)
    assert scheduling_database.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 1


@pytest.mark.parametrize('archived', [False, True])
def test_scheduling_wrapper_missing_or_archived_fresh_card_preserves_error(scheduling_database, archived):
    scheduling_database.execute('UPDATE cards SET archived=1' if archived else 'DELETE FROM cards')
    with pytest.raises(KeyError, match='Card not found'):
        apply_review(scheduling_database)
    assert scheduling_database.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 0


def test_scheduling_wrapper_missing_source_replay_card_preserves_error(scheduling_database):
    apply_review(scheduling_database)
    scheduling_database.execute('DELETE FROM cards')
    with pytest.raises(TypeError):
        apply_review(scheduling_database)


def test_scheduling_store_never_commits_outside_command_owner(scheduling_database):
    from app.services.scheduling_review_store import OwnedConnectionSchedulingReviewStore
    class BorrowedConnection:
        def execute(self, *args):
            return scheduling_database.execute(*args)
        def __getattr__(self, name):
            raise AssertionError(f'adapter accessed transaction ownership: {name}')
    scheduling_database.commit()
    assert not scheduling_database.in_transaction
    apply_review(BorrowedConnection())
    assert scheduling_database.in_transaction
    scheduling_database.rollback()
    assert scheduling_database.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 0
    assert scheduling_database.execute('SELECT first_correct_at FROM cards').fetchone()[0] is None
    assert not any(hasattr(OwnedConnectionSchedulingReviewStore,name) for name in
                   ('commit','rollback','close','connection','__enter__','__exit__'))


def test_scheduling_store_maturity_filters_invalidated_history_and_preserves_seed_and_ties(scheduling_database):
    from app.services.scheduling_review_application import MaturityFacts, OpeningSeedFacts
    from app.services.scheduling_review_store import OwnedConnectionSchedulingReviewStore
    for rating, timestamp, invalidated in [('correct','2026-01-01T10:00:00',None),
        ('correct','2026-01-01T12:00:00',None),('correct','2026-01-02T10:00:00',None),
        ('again','2026-01-02T10:00:00',None),('again','2026-01-03T10:00:00','retired')]:
        scheduling_database.execute('INSERT INTO reviews(card_id,rating,reviewed_at,invalidated_at) VALUES(?,?,?,?)',('card',rating,timestamp,invalidated))
    scheduling_database.execute('INSERT INTO opening_card_schedule_seeds VALUES(?,?,?)',('card',5,1))
    assert OwnedConnectionSchedulingReviewStore(scheduling_database).load_maturity_facts('card') == MaturityFacts(2,('again','correct'),OpeningSeedFacts(5,1))


def test_scheduling_wrapper_followups_borrow_same_connection_after_save(scheduling_database,monkeypatch):
    events = []
    def followup(connection,*args):
        assert connection is scheduling_database
        assert connection.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 1
        assert connection.execute('SELECT first_correct_at FROM cards').fetchone()[0] is not None
        events.append(args)
    monkeypatch.setattr(review_service,'unlock_legacy_children_after_review',followup)
    from app.services import real_game_feedback_adapter as real_game_feedback
    monkeypatch.setattr(real_game_feedback,'clear_satisfied_miss_priority',followup)
    apply_review(scheduling_database)
    assert events == [('card',),('card','2026-01-02','learning')]


@pytest.mark.parametrize('outcome,recent_json', [('invalid','[]'),('correct','invalid')])
def test_scheduling_wrapper_invalid_inputs_do_not_append_history(scheduling_database,outcome,recent_json):
    scheduling_database.execute('UPDATE cards SET recent_attempts_json=?',(recent_json,))
    with pytest.raises(ValueError):
        review_service.apply_scheduling_review(scheduling_database,'card',outcome,
            guided=False,source_kind='study',source_ref=None,light_first_interval_days=7,
            reviewed_at=datetime(2026,1,2,tzinfo=timezone.utc),review_day=date(2026,1,2))
    assert scheduling_database.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 0
