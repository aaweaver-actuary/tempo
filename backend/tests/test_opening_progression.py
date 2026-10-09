"""Opening progression requires completed practice, independently of FSRS maturity."""
import sqlite3

import pytest

from app.main import _unlock_eligible_opening_cards


@pytest.fixture
def progression_database():
    with sqlite3.connect(':memory:') as connection:
        connection.executescript("""
            CREATE TABLE cards(id TEXT PRIMARY KEY,content_type TEXT,state TEXT,archived INTEGER);
            CREATE TABLE reviews(card_id TEXT,rating TEXT,source_kind TEXT,invalidated_at TEXT);
            CREATE TABLE opening_graph_steps(card_id TEXT,parent_card_id TEXT,repertoire_id TEXT,generation INTEGER);
            CREATE TABLE opening_graph_publications(repertoire_id TEXT,generation INTEGER);
            INSERT INTO opening_graph_publications VALUES('rep',2);
            INSERT INTO cards VALUES('parent','opening','learning',0),('child','opening','locked',0),('grandchild','opening','locked',0);
            INSERT INTO opening_graph_steps VALUES('parent',NULL,'rep',2),('child','parent','rep',2),('grandchild','child','rep',2);
        """)
        yield connection


@pytest.mark.parametrize('outcome', ['correct', 'again'])
@pytest.mark.parametrize('batch_size', [None, 1])
def test_learning_parent_completed_study_attempt_unlocks_only_next_decision(progression_database, outcome, batch_size):
    connection = progression_database
    connection.execute("INSERT INTO reviews VALUES('parent',?,'study',NULL)", (outcome,))
    cursor = ''
    while cursor is not None:
        cursor = _unlock_eligible_opening_cards(connection, '2026-10-09', after_card_id=cursor, batch_size=batch_size)
    assert connection.execute("SELECT state FROM cards WHERE id='child'").fetchone()[0] == 'new'
    assert connection.execute("SELECT state FROM cards WHERE id='grandchild'").fetchone()[0] == 'locked'
    assert connection.execute("SELECT state FROM cards WHERE id='parent'").fetchone()[0] == 'learning'


@pytest.mark.parametrize('parent_state,source_kind,invalidated_at', [
    ('learning', None, None), ('mature', None, None),
    ('learning', 'gameplay', None), ('learning', 'study', '2026-10-09'),
])
def test_admission_maturity_and_invalid_evidence_do_not_replace_completed_practice(progression_database, parent_state, source_kind, invalidated_at):
    connection = progression_database
    connection.execute("UPDATE cards SET state=? WHERE id='parent'", (parent_state,))
    if source_kind:
        connection.execute("INSERT INTO reviews VALUES('parent','correct',?,?)", (source_kind, invalidated_at))
    _unlock_eligible_opening_cards(connection, '2026-10-09', after_card_id='', batch_size=8)
    assert connection.execute("SELECT state FROM cards WHERE id='child'").fetchone()[0] == 'locked'


def test_any_practiced_current_incoming_path_unlocks_shared_card_without_historical_routes(progression_database):
    connection = progression_database
    connection.execute("INSERT INTO cards VALUES('alternate','opening','learning',0)")
    connection.execute("INSERT INTO reviews VALUES('alternate','again','study',NULL)")
    connection.execute("INSERT INTO opening_graph_steps VALUES('child','alternate','rep',1)")
    _unlock_eligible_opening_cards(connection, '2026-10-09', after_card_id='', batch_size=1)
    assert connection.execute("SELECT state FROM cards WHERE id='child'").fetchone()[0] == 'locked'
    connection.execute("INSERT INTO opening_graph_steps VALUES('child','alternate','rep',2)")
    _unlock_eligible_opening_cards(connection, '2026-10-09', after_card_id='', batch_size=1)
    assert connection.execute("SELECT state FROM cards WHERE id='child'").fetchone()[0] == 'new'


def test_review_triggered_queue_extension_preserves_surviving_attempt_order_and_shuffles_new_cards():
    from app.main import _plan_daily_queue_order
    rows = [dict(id=identifier, card_id=f'card-{identifier}', position=position,
                 admission_kind='new', content_type='opening', gameplay_priority_reason=None)
            for identifier, position in [(1, 7), (2, 0), (3, 3), (4, 8), (5, 9), (6, 10)]]
    _, _, ordered = _plan_daily_queue_order(rows, '2026-10-09', None, preserve_through_entry_id=3)
    assert [row['id'] for row in ordered[:3]] == [2, 3, 1]
    assert {row['id'] for row in ordered[3:]} == {4, 5, 6}
    assert _plan_daily_queue_order(rows, '2026-10-09', None, preserve_through_entry_id=3)[2] == ordered


def test_republished_routes_reuse_real_exposure_but_shortened_new_parent_does_not_inherit_it(progression_database):
    connection = progression_database
    connection.execute("INSERT INTO reviews VALUES('parent','again','study',NULL)")
    connection.execute('INSERT INTO opening_graph_steps SELECT card_id,parent_card_id,repertoire_id,3 FROM opening_graph_steps WHERE generation=2')
    connection.execute('UPDATE opening_graph_publications SET generation=3')
    _unlock_eligible_opening_cards(connection, '2026-10-09')
    assert connection.execute("SELECT state FROM cards WHERE id='child'").fetchone()[0] == 'new'
    connection.execute("INSERT INTO cards VALUES('shortened-parent','opening','learning',0)")
    connection.execute("UPDATE cards SET state='locked' WHERE id='child'")
    connection.execute("UPDATE opening_graph_steps SET parent_card_id='shortened-parent' WHERE card_id='child' AND generation=3")
    _unlock_eligible_opening_cards(connection, '2026-10-09')
    assert connection.execute("SELECT state FROM cards WHERE id='child'").fetchone()[0] == 'locked'
    assert connection.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 1


@pytest.mark.parametrize('stale_evidence', ['invalidated_review', 'unpublished_route'])
def test_bounded_unlock_rechecks_parent_exposure_and_publication_before_update(progression_database, stale_evidence):
    connection = progression_database
    connection.execute("INSERT INTO reviews VALUES('parent','again','study',NULL)")

    class CandidateRecheckDatabase:
        def execute(self, statement, parameters=()):
            cursor = connection.execute(statement, parameters)
            if statement.startswith('WITH root_candidates'):
                candidates = cursor.fetchall()
                assert candidates == [('child',)]
                if stale_evidence == 'invalidated_review':
                    connection.execute("UPDATE reviews SET invalidated_at='2026-10-09'")
                else:
                    connection.execute('UPDATE opening_graph_publications SET generation=3')

                class SelectedCandidates:
                    def fetchall(self):
                        return candidates

                return SelectedCandidates()
            return cursor

    _unlock_eligible_opening_cards(CandidateRecheckDatabase(), '2026-10-09', after_card_id='', batch_size=8)
    assert connection.execute("SELECT state FROM cards WHERE id='child'").fetchone()[0] == 'locked'
