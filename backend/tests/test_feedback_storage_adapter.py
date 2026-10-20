"""Actual SQLite compatibility contracts; no SQL-prefix fake database."""
import sqlite3
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.storage.real_game_feedback import BATCH_SIZE, BorrowedFeedbackStore


@pytest.fixture
def database():
    database = sqlite3.connect(':memory:')
    database.row_factory = sqlite3.Row
    database.executescript('''
        CREATE TABLE imported_games(id TEXT PRIMARY KEY, adaptive_excluded INTEGER DEFAULT 0);
        CREATE TABLE cards(id TEXT PRIMARY KEY,state TEXT,introduced_at TEXT,content_type TEXT DEFAULT 'opening',
            archived INTEGER DEFAULT 0,pending_validation INTEGER DEFAULT 0,superseded_by TEXT,repertoire_id TEXT);
        CREATE TABLE repertoires(id TEXT PRIMARY KEY);
        CREATE TABLE repertoire_cards(card_id TEXT,repertoire_id TEXT);
        CREATE TABLE deleted_cards(card_id TEXT);
        CREATE TABLE repertoire_integrity_card_blocks(card_id TEXT,repertoire_id TEXT);
        CREATE TABLE current_repertoire_decision_events(id TEXT PRIMARY KEY,card_id TEXT,game_id TEXT,repertoire_id TEXT,
            ply INTEGER,outcome TEXT,played_at TEXT);
        CREATE TABLE reviews(card_id TEXT,source_kind TEXT,invalidated_at TEXT,reviewed_at TEXT);
        CREATE TABLE daily_queue(card_id TEXT,queue_date TEXT,status TEXT,gameplay_priority_reason TEXT);
        INSERT INTO repertoires VALUES('rep');
        INSERT INTO imported_games(id) VALUES('game');
        INSERT INTO cards(id,state,introduced_at,repertoire_id) VALUES('card','learning','2026-10-19','rep');
        INSERT INTO current_repertoire_decision_events VALUES('event','card','game','rep',5,'miss','2026-10-20T10:00:00.000001Z');
    ''')
    yield database
    database.close()


def test_bounded_typed_projection_deduplicates_and_rejects_oversized_batch(database):
    store = BorrowedFeedbackStore(database)
    assert [(row.card_id, row.state) for row in store.outstanding_cards(('card', 'card', 'missing'))] == [('card', 'learning')]
    assert store.outstanding_cards(()) == ()
    with pytest.raises(ValueError, match='128'):
        store.outstanding_cards(('card',) * (BATCH_SIZE + 1))


@pytest.mark.parametrize('statement', [
    "UPDATE cards SET archived=1", "UPDATE cards SET pending_validation=1",
    "UPDATE cards SET superseded_by='other'", "DELETE FROM repertoires",
    "UPDATE cards SET repertoire_id='other'", "INSERT INTO deleted_cards VALUES('card')",
    "INSERT INTO repertoire_integrity_card_blocks VALUES('card','rep')",
    "UPDATE imported_games SET adaptive_excluded=1",
])
def test_actual_projection_filters_noncurrent_or_unsafe_facts(database, statement):
    database.execute(statement)
    assert BorrowedFeedbackStore(database).outstanding_card('card') is None


@pytest.mark.parametrize('reviewed_at,source_kind,invalidated,expected', [
    ('2026-10-20T06:00:00.000000-04:00', 'study', None, True),
    ('2026-10-20T06:00:00.000001-04:00', 'study', None, False),
    ('2026-10-20T06:00:00.000002-04:00', 'study', None, False),
    ('2026-10-20T06:00:00.000002-04:00', 'game', None, True),
    ('2026-10-20T06:00:00.000002-04:00', 'study', 'invalidated', True),
])
def test_projection_retains_exact_instant_source_and_invalidation_rules(database, reviewed_at, source_kind, invalidated, expected):
    database.execute('INSERT INTO reviews VALUES(?,?,?,?)', ('card', source_kind, invalidated, reviewed_at))
    assert (BorrowedFeedbackStore(database).outstanding_card('card') is not None) == expected


def test_adapter_batches_preserve_one_snapshot_and_original_scope_order(database):
    for index in range(260):
        database.execute('INSERT INTO current_repertoire_decision_events VALUES(?,?,?, ?,?,?,?)',
                         (f'event-{index:03}', 'card', 'game', 'rep', index + 6, 'miss', '2026-10-20T10:00:01Z'))
    store = BorrowedFeedbackStore(database)
    batches = list(store.missed_event_batches('game'))
    assert [len(batch) for batch in batches] == [128, 128, 5]
    assert [event for batch in batches for event in batch] == ['event', *[f'event-{i:03}' for i in range(260)]]
    evidence = list(store.repertoire_miss_batches('rep'))
    assert [len(batch) for batch in evidence] == [128, 128, 5]
    assert {row.event_id for batch in evidence for row in batch} == set(event for batch in batches for event in batch)


def test_adapter_effects_remain_inside_callers_rollback_and_preserve_other_reasons(database):
    store = BorrowedFeedbackStore(database)
    database.execute("INSERT INTO daily_queue VALUES('card','day','queued','legacy')")
    database.execute("INSERT INTO daily_queue VALUES('card','other-day','queued','accepted')")
    database.commit()
    database.execute('BEGIN')
    store.replace_miss_priority('card', 'legacy', 'replacement')
    assert database.execute("SELECT gameplay_priority_reason FROM daily_queue WHERE queue_date='day'").fetchone()[0] == 'replacement'
    database.rollback()
    assert [row[0] for row in database.execute('SELECT gameplay_priority_reason FROM daily_queue')] == ['legacy', 'accepted']
