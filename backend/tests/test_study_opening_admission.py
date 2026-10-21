"""Bounded essential opening selection and durable phase policy without PostgreSQL."""

from contextlib import contextmanager
import json

import chess
import pytest

from app.services import postgres_queue_refresh as worker, study_mode
from app.chess.cards import card_id


def candidate(moves=None):
    moves = moves or ['e2e4']
    return {'card_id': card_id(chess.STARTING_FEN, moves), 'repertoire_id': 'rep', 'reason': None,
            'study_route': {'generation': 1, 'line_id': 'line', 'decision_index': 0,
                            'content': [chess.STARTING_FEN, json.dumps(moves), 'white',
                                        chess.STARTING_FEN, json.dumps(moves)]}}


def test_study_opening_validation_is_pure_and_preserves_card_identity():
    planned = candidate()
    assert worker._study_opening_content_valid(planned)
    planned['study_route']['content'][3] = chess.STARTING_FEN.replace('0 1', '7 4')
    assert worker._study_opening_content_valid(planned)
    planned['card_id'] = 'unrelated'
    assert not worker._study_opening_content_valid(planned)


def test_study_opening_graph_order_is_stable_without_analysis_or_postgres():
    rows = [('2026-10-02', 'rep-b', '2026-10-01', 'line-b', 0, 'aaa'),
            ('2026-10-01', 'rep-a', '2026-10-01', 'line-a', 1, 'aaa'),
            ('2026-10-01', 'rep-a', '2026-10-01', 'line-a', 0, 'zzz')]
    expected = [rows[2], rows[1], rows[0]]
    assert sorted(rows, key=worker._study_opening_cursor) == expected
    assert sorted(reversed(rows), key=worker._study_opening_cursor) == expected


def test_study_opening_selector_reads_only_one_page_per_slice(monkeypatch):
    planned = candidate()
    content = planned['study_route']['content']
    rows = [('2026-10-01', 'rep', '2026-10-01', f'line-{index:02}', 0,
             planned['card_id'], 1, *content) for index in range(16)]
    reads = []
    def bounded_read(statement, parameters=(), *, native=False):
        reads.append((statement, parameters))
        if len(reads) == 1:
            return [(True,)]
        if len(reads) == 2:
            return [rows[-1][:6]]
        assert len(reads) == 3
        assert parameters[-1] == 16 and 'LIMIT %s' in statement
        return rows
    class Reader:
        def execute_native(self, *args):
            return self
        def fetchone(self):
            return (True,)
    @contextmanager
    def reader():
        yield Reader()
    monkeypatch.setattr(worker.read_storage, '_bounded_read', bounded_read)
    monkeypatch.setattr(worker, 'background_read_connection', reader)
    monkeypatch.setattr(worker.read_storage, 'background_read_connection', reader)
    monkeypatch.setattr(worker, '_study_opening_route_still_valid', lambda *_: True)
    monkeypatch.setattr(worker, 'has_outstanding_real_game_miss', lambda *_: False)
    page = worker._prepare_study_opening_page('2026-10-13', {})
    assert len(reads) == 3 and page['examined'] == 16
    assert len(page['items']) == 1  # shared route occurrences, one card
    assert page['has_more']


@pytest.mark.parametrize('moves,color', [(['e2e5'], 'white'), (['e2e4'], 'black'),
                                       ([17], 'white'), ([], 'white')])
def test_study_opening_validation_rejects_invalid_published_content(moves, color):
    planned = candidate()
    planned['study_route']['content'][1] = json.dumps(moves)
    planned['study_route']['content'][2] = color
    assert not worker._study_opening_content_valid(planned)


@pytest.fixture
def phase_harness(monkeypatch):
    saved = {}
    @contextmanager
    def section(*, background):
        assert background
        yield object()
    def advance(database, task, *, next_phase, next_payload):
        saved.update(phase=next_phase, payload=next_payload)
        return True
    monkeypatch.setattr(worker.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(worker.activity_gate, 'wait_for_foreground', lambda: None)
    monkeypatch.setattr(worker, 'connection', section)
    monkeypatch.setattr(worker, 'lock_current_slice', lambda database, task: True)
    monkeypatch.setattr(worker, 'advance_task_slice_in_transaction', advance)
    return saved


@pytest.mark.parametrize('previous_phase', ['prioritized_openings', 'prioritized_opening_item'])
def test_study_only_replaces_optional_ranked_checkpoint_with_bounded_page(previous_phase, phase_harness, monkeypatch):
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(worker, '_prepare_prioritized_openings', lambda *_: pytest.fail('Optional ranking'))
    monkeypatch.setattr(worker, '_prepare_study_opening_page', lambda *_: {
        'items': [candidate()], 'cursor': ['rep', 'line', 0], 'upper': ['rep', 'line', 1],
        'has_more': True, 'examined': 16, 'stop_reason': None})
    worker.execute_postgres_queue_refresh_slice({'payload': {'queue_date': '2026-10-13',
        '_queue_phase': previous_phase, 'opening_plan': [{'card_id': 'obsolete-ranked-card'}], 'opening_index': 0}})
    assert phase_harness['phase'] == 'study_opening_item'
    assert 'opening_plan' not in phase_harness['payload']
    assert len(phase_harness['payload']['study_opening_items']) == 1


def test_study_opening_rejected_page_advances_without_starvation(phase_harness, monkeypatch):
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(worker, '_prepare_study_opening_page', lambda *_: {
        'items': [], 'cursor': ['rep', 'line', 16], 'upper': ['rep', 'line', 30],
        'has_more': True, 'examined': 16, 'stop_reason': None})
    worker.execute_postgres_queue_refresh_slice({'payload': {'queue_date': '2026-10-13',
                                                            '_queue_phase': 'study_opening_page'}})
    assert phase_harness['phase'] == 'study_opening_page'
    assert phase_harness['payload']['study_opening_cursor'] == ['rep', 'line', 16]
    assert phase_harness['payload']['study_opening_counts']['excluded_or_no_allowance'] == 16


def test_study_opening_publishes_one_item_then_checkpoints_even_when_mode_resumes(phase_harness, monkeypatch):
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
    admitted = []
    monkeypatch.setattr(worker, '_admit_one_prioritized_opening',
                        lambda db, day, planned: admitted.append(planned['card_id']) or False)
    payload = {'queue_date': '2026-10-13', '_queue_phase': 'study_opening_item',
               'study_opening_items': [candidate(), candidate(['d2d4'])],
               'study_opening_index': 0, 'study_opening_more': True,
               'study_opening_counts': {'examined': 16, 'eligible': 2}}
    worker.execute_postgres_queue_refresh_slice({'payload': payload})
    assert len(admitted) == 1
    assert phase_harness['phase'] == 'study_opening_item'
    assert phase_harness['payload']['study_opening_index'] == 1
    assert phase_harness['payload']['study_opening_counts']['publication_rejected'] == 1


def test_normal_mode_keeps_existing_priority_preparation(phase_harness, monkeypatch):
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
    monkeypatch.setattr(worker, '_prepare_study_opening_page', lambda *_: pytest.fail('Fallback in normal mode'))
    monkeypatch.setattr(worker, '_prepare_prioritized_openings', lambda *_: [{'card_id': 'ranked'}])
    worker.execute_postgres_queue_refresh_slice({'payload': {'queue_date': '2026-10-13',
                                                            '_queue_phase': 'prioritized_openings'}})
    assert phase_harness['phase'] == 'prioritized_opening_item'
    assert phase_harness['payload']['opening_plan'] == [{'card_id': 'ranked'}]


@pytest.fixture(autouse=True)
def legacy_study_order_has_no_accepted_snapshot(monkeypatch):
    from app.services import frozen_opening_order
    monkeypatch.setattr(frozen_opening_order, 'activation', lambda database, **kwargs: (None, 0))
    # Existing tests replace the owning reader for route/allowance proofs.
    # Metadata reads use that seam without introducing provider/model access.
    original_reader = worker.background_read_connection
    @contextmanager
    def metadata_compatible_reader():
        from tempo_legacy_database.adapters import postgres_store
        import os
        if postgres_store.configured() and not os.getenv('TEMPO_DATABASE_READ_URL'):
            yield object()
        else:
            with original_reader() as database:
                yield database
    monkeypatch.setattr(worker, 'background_read_connection', metadata_compatible_reader)
    monkeypatch.setattr(worker.read_storage, 'background_read_connection', metadata_compatible_reader)
