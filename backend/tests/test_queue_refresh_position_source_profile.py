"""Position due preparation uses the snapshot reader before the fenced writer."""
from contextlib import contextmanager

import pytest

from app.services import postgres_queue_refresh as refresh, position_queue_admission, study_mode


@pytest.fixture(autouse=True)
def native_store(monkeypatch):
    monkeypatch.setattr(refresh.read_storage, 'native_configured', lambda: True)


@pytest.mark.parametrize('lease_current', [True, False])
def test_position_due_snapshot_reader_closes_before_fenced_writer(monkeypatch, lease_current):
    events = []
    @contextmanager
    def background_reader():
        events.append('background_open')
        yield 'background'
        events.append('background_close')
    @contextmanager
    def snapshot_reader(*, repertoire_snapshot=False):
        assert repertoire_snapshot is True
        assert events[-1] == 'foreground_wait'
        events.append('snapshot_open')
        yield 'snapshot'
        events.append('snapshot_close')
    @contextmanager
    def writer(*, background):
        assert background and 'snapshot_close' in events
        events.append('writer_open')
        yield 'writer'
        events.append('writer_close')
    monkeypatch.setattr(refresh, 'background_read_connection', background_reader)
    monkeypatch.setattr(refresh, 'read_connection', snapshot_reader, raising=False)
    monkeypatch.setattr(refresh, 'connection', writer)
    monkeypatch.setattr(refresh.activity_gate, 'wait_for_foreground', lambda: events.append('foreground_wait'))
    monkeypatch.setattr(refresh.frozen_opening_order, 'activation', lambda *args, **kwargs: (None, 0))
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
    def prepare(db, day, payload):
        assert db == 'snapshot'
        events.append('prepare')
        return {'cursor': {'position_repertoire_after': 'r'}}
    monkeypatch.setattr(position_queue_admission, 'prepare_due', prepare)
    monkeypatch.setattr(refresh, 'lock_current_slice', lambda *args: lease_current)
    monkeypatch.setattr(position_queue_admission, 'admit_due', lambda *args: events.append('admit'))
    monkeypatch.setattr(refresh, 'advance_task_slice_in_transaction', lambda *args, **kwargs: True)
    task = {'payload': {'queue_date': '2026-11-03', '_queue_phase': 'admit_position_due', '_refresh_cycle_epoch': 1}}
    assert refresh.execute_postgres_queue_refresh_slice(task) is lease_current
    assert events.index('snapshot_close') < events.index('writer_open')
    assert ('admit' in events) is lease_current


def test_other_phase_keeps_background_reader_and_never_opens_snapshot(monkeypatch):
    events = []
    @contextmanager
    def background_reader():
        events.append('background_reader')
        yield object()
    @contextmanager
    def writer(*, background):
        assert background
        yield object()
    monkeypatch.setattr(refresh, 'background_read_connection', background_reader)
    monkeypatch.setattr(refresh, 'read_connection', lambda **kwargs: pytest.fail('other phase used snapshot profile'), raising=False)
    monkeypatch.setattr(refresh, 'connection', writer)
    monkeypatch.setattr(refresh.activity_gate, 'wait_for_foreground', lambda: None)
    monkeypatch.setattr(refresh.frozen_opening_order, 'activation', lambda *args, **kwargs: (None, 0))
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
    monkeypatch.setattr(refresh, '_prepare_due_card', lambda day: None)
    monkeypatch.setattr(refresh, 'lock_current_slice', lambda *args: False)
    task = {'payload': {'queue_date': '2026-11-03', '_queue_phase': 'admit_due', '_refresh_cycle_epoch': 1}}
    assert refresh.execute_postgres_queue_refresh_slice(task) is False
    assert events == ['background_reader']
