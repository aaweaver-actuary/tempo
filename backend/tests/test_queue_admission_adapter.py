"""Actual legacy storage contracts. SQLite proof is separate from native PG."""
from datetime import date

import pytest

from app import database
from app.services import queue_admission, queue_application
from app.services.queue_admission_adapter import QueueAdmissionAdapter, READ_BATCH_SIZE
from test_repertoire_settings import workspace, complete_cards
from test_opening_capacity_admission import capacity_database


def test_adapter_candidates_are_paged_and_keep_all_memberships(workspace, monkeypatch):
    day = date.today().isoformat()
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('second','first-00')")
        adapter = QueueAdmissionAdapter(connection)
        original, pages = adapter._priority_page, []
        def record(day, identifiers, **kwargs):
            assert 0 < len(identifiers) <= READ_BATCH_SIZE
            pages.append(tuple(identifiers))
            return original(day, identifiers, **kwargs)
        monkeypatch.setattr(adapter, '_priority_page', record)
        rows = queue_admission.read_opening_candidates(adapter, day)
        assert len(pages) == 3
        assert len({identifier for page in pages for identifier in page}) == 40
        assert {(row['id'], row['repertoire_id']) for row in rows} == {
            *((f'{rep}-{index:02}', rep) for rep in ('first', 'second') for index in range(20)),
            ('first-00', 'second'),
        }


def test_cross_page_shared_card_preserves_legacy_admitting_membership_with_opposed_insertion(workspace, monkeypatch):
    day = date.today().isoformat()
    with database.connection() as connection:
        connection.execute('UPDATE cards SET archived=1')
        # Insert repertoire/card owners opposite their identity order. The shared
        # card's shorter sequence outranks the private card within owner-a.
        for repertoire_id in ('owner-z', 'owner-a'):
            connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,'fixture',?)", (repertoire_id, repertoire_id, day))
        rows = [('z-shared', 'owner-z', '["e2e4"]', 'new'),
                ('a-private', 'owner-a', '["e2e4","e7e5","g1f3"]', 'new'),
                *[(f'm-rejected-{index:02}', 'owner-z', '["e2e4"]', 'locked') for index in range(18)]]
        for card_id, repertoire_id, moves, state in rows:
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES(?,?,'prefix','fen',?,?,?)", (card_id, repertoire_id, moves, state, day))
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('owner-a','z-shared')")
        adapter = QueueAdmissionAdapter(connection)
        pages, original = [], adapter._priority_page
        def capture(day, identifiers, **kwargs):
            pages.append(tuple(identifiers))
            return original(day, identifiers, **kwargs)
        monkeypatch.setattr(adapter, '_priority_page', capture)
        assert queue_admission.admit_prioritized_opening_cards(adapter, day, {'owner-a':1, 'owner-z':1}, -1) == 0
        assert len(pages) == 2 and pages[0][0] == 'a-private' and pages[-1][-1] == 'z-shared'
        assert [tuple(row) for row in connection.execute('SELECT card_id,admission_repertoire_id FROM daily_queue')] == [('z-shared', 'owner-a')]
        assert queue_admission.read_introduction_counts(adapter, day) == {'owner-a':1}
        assert adapter.introduced_count(day, 'owner-z') == 0
        assert connection.execute("SELECT introduced_at FROM cards WHERE id='a-private'").fetchone()[0] is None


@pytest.mark.parametrize('reviewed', [False, True])
def test_adapter_historical_counts_keep_admitting_membership_after_integrity_change(workspace, reviewed):
    day = date.today().isoformat()
    with database.connection() as connection:
        connection.execute("UPDATE repertoires SET new_cards_per_day=0 WHERE id='first'")
        connection.execute("UPDATE repertoires SET new_cards_per_day=1 WHERE id='second'")
        connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('second','first-00')")
        connection.execute("UPDATE cards SET archived=1 WHERE id!='first-00'")
        queue_application.seed_queue(connection, day)
        if reviewed:
            complete_cards(connection, day, 'second', 1)
        connection.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,signature,created_at,updated_at) VALUES('block','first','missing_response','shared',?,?)", (day, day))
        connection.execute("INSERT INTO repertoire_integrity_card_blocks(repertoire_id,card_id,issue_id,scan_generation,published_at) VALUES('first','first-00','block','current',?)", (day,))
        connection.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position) VALUES(?,'first-00',1,100)", (day,))
        adapter = QueueAdmissionAdapter(connection)
        assert queue_admission.read_introduction_counts(adapter, day) == {'second': 1}
        assert queue_admission.read_introduction_counts(adapter, day, reviewed_only=True) == ({'second': 1} if reviewed else {})
        assert adapter.introduced_count(day, 'first') == 0
        assert adapter.introduced_count(day, 'second') == 1


def test_adapter_count_and_reconciliation_pages_cross_boundaries_without_refunding(workspace):
    day = date.today().isoformat()
    with database.connection() as connection:
        # Several entries share each position; ID is the required cursor tie-break.
        for index in range(35):
            repertoire_id, card_id = f'page-{index:02}', f'card-{index:02}'
            connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,'fixture',?)", (repertoire_id, repertoire_id, day))
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at) VALUES(?,?,'prefix','fen','[]','learning',?,?)", (card_id, repertoire_id, day, day))
            connection.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)", (day, card_id, index//3))
        adapter = QueueAdmissionAdapter(connection)
        rows = adapter.unseen_openings(day)
        assert len(rows) == 35
        assert [row['id'] for row in rows] == sorted(row['id'] for row in rows)
        assert queue_admission.read_introduction_counts(adapter, day) == {f'page-{index:02}': 1 for index in range(35)}
        queue_admission.reconcile_unseen_queue(adapter, day, 0)
        assert adapter.unseen_openings(day) == []
        assert queue_admission.read_introduction_counts(adapter, day) == {}
        assert connection.execute("SELECT COUNT(*) FROM cards WHERE id LIKE 'card-%' AND state='new' AND introduced_at IS NULL").fetchone()[0] == 35


def test_adapter_effect_failure_rolls_back_with_callers_savepoint(workspace, monkeypatch):
    day = date.today().isoformat()
    with database.connection() as connection:
        connection.execute("UPDATE cards SET archived=1 WHERE id!='first-00'")
        adapter = QueueAdmissionAdapter(connection)
        seen = []
        def fail_after_queue_insert(day, card_id):
            seen.append(connection.execute('SELECT card_id FROM daily_queue').fetchone()[0])
            raise RuntimeError('injected after admission insert')
        monkeypatch.setattr(adapter, 'retain_unseen', fail_after_queue_insert)
        connection.execute('SAVEPOINT caller_admission')
        with pytest.raises(RuntimeError, match='after admission insert'):
            queue_admission.admit_prioritized_opening_cards(adapter, day, 1, -1)
        connection.execute('ROLLBACK TO caller_admission')
        connection.execute('RELEASE caller_admission')
        assert seen == ['first-00']  # Fault was reached after a real effect.
        assert connection.execute('SELECT COUNT(*) FROM daily_queue').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM queue_attempt_origins').fetchone()[0] == 0
        assert connection.execute("SELECT state,introduced_at FROM cards WHERE id='first-00'").fetchone()[:] == ('new', None)
        # The adapter neither closed nor rolled back the caller's outer work.
        assert connection.execute("SELECT COUNT(*) FROM cards WHERE archived=1").fetchone()[0] == 39


def test_adapter_capacity_witness_revalidates_exact_published_route(capacity_database, monkeypatch):
    connection, day, _ = capacity_database
    monkeypatch.setenv('OPENING_CAPACITY_FRONTIER_EXPANSION', '1')
    adapter = QueueAdmissionAdapter(connection)
    plan = queue_admission.plan_opening_admissions(adapter, day, {'rep': 10})
    parents = [(rep, row) for rep, row in plan if 'capacity_route' not in row]
    children = [(rep, row) for rep, row in plan if 'capacity_route' in row]
    assert len(parents) == 2 and len(children) == 8
    for position, (rep, row) in enumerate(parents):
        adapter.introduce_opening(day, {**row, 'card_id': row['id'], 'repertoire_id': rep, 'reason': None}, position)
    child = children[0][1]
    proposal = {**child, 'card_id': child['id']}
    assert adapter.capacity_route_valid(proposal, day)
    connection.execute("UPDATE opening_graph_publications SET generation=generation+1 WHERE repertoire_id='rep'")
    assert not adapter.capacity_route_valid(proposal, day)
    monkeypatch.setattr(queue_admission, 'plan_opening_admissions', lambda *args: children)
    assert queue_admission.admit_prioritized_opening_cards(adapter, day, {'rep': 10}, 1) == 1
    assert connection.execute('SELECT COUNT(*) FROM daily_queue').fetchone()[0] == 2
