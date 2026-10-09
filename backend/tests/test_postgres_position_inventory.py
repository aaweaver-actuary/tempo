"""Worker boundaries and source-contract regressions for issue #108."""
from contextlib import contextmanager
from types import SimpleNamespace
import threading

import chess
import pytest

from app.services import postgres_position_inventory as inventory
from app.services.activity_gate import activity_gate


def test_issue108_source_cohort_keys_share_positions_and_preserve_supported_contexts():
    fields = dict(source_id='explorer',model_version='api-v1',schema_version='1',rating_cohort='1600')
    first = inventory.source_cohort_key(chess.STARTING_FEN,**fields)
    assert first == inventory.source_cohort_key(chess.STARTING_FEN.replace('0 1','7 12'),**fields)
    assert first != inventory.source_cohort_key(chess.STARTING_FEN,**{**fields,'rating_cohort':'1800'})
    assert first != inventory.source_cohort_key(chess.STARTING_FEN,**fields,speed_cohort='rapid')
    assert first[-1] == ''
    for field in fields:
        with pytest.raises(ValueError):
            inventory.source_cohort_key(chess.STARTING_FEN,**{**fields,field:''})


def test_issue108_foreground_contention_restart_and_idempotent_replay(monkeypatch):
    entered_read = threading.Event()
    read_open = False
    write_open = False
    committed = False
    route = dict(id='route',trained_color='white',scope_start_ply=0,absolute_ply_offset=0,
                 completed_plies=0,checkpoint_fen=chess.STARTING_FEN,complete=0,move_count=1,moves=['e2e4'])
    class Database:
        def execute_native(self, statement, parameters=()):
            if statement.startswith('SELECT id,trained_color'):
                return SimpleNamespace(fetchone=lambda: route)
            if statement.startswith('SELECT completed_plies'):
                return SimpleNamespace(fetchone=lambda: {'complete':0,'completed_plies':0})
            return SimpleNamespace(fetchone=lambda: None)
    database = Database()
    @contextmanager
    def reader():
        nonlocal read_open
        entered_read.set()
        with activity_gate.background_database_section():
            read_open = True
            try:
                yield database
            finally:
                read_open = False
    @contextmanager
    def writer(**kwargs):
        nonlocal write_open
        assert kwargs == {'background':True}
        with activity_gate.background_database_section():
            write_open = True
            try:
                yield database
            finally:
                write_open = False
    @contextmanager
    def lease():
        yield
    original_segment = inventory.inventory_segment
    def segment(*args,**kwargs):
        assert not read_open and not write_open, 'Chess computation holds a database connection'
        return original_segment(*args,**kwargs)
    staged = []
    def advance(*args,**kwargs):
        nonlocal committed
        committed = True
        return True
    monkeypatch.setattr(inventory,'background_read_connection',reader)
    monkeypatch.setattr(inventory,'connection',writer)
    monkeypatch.setattr(inventory,'background_lease',lease)
    monkeypatch.setattr(inventory,'inventory_segment',segment)
    monkeypatch.setattr(inventory,'_current',lambda *_: not committed)
    monkeypatch.setattr(inventory,'_stage_occurrences',lambda *args: staged.append(args[-1]))
    monkeypatch.setattr(inventory,'_advance',advance)
    task = {'payload':{'phase':'traverse','route_id':'route','line_id':'line'}}
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(inventory.execute_inventory_slice(task)))
    with activity_gate.foreground():
        worker.start()
        assert entered_read.wait(2)
        assert not staged and not outcomes
    worker.join(2)
    assert not worker.is_alive() and outcomes == [True]
    assert len(staged) == 1
    assert inventory.execute_inventory_slice(task) is False
    assert len(staged) == 1


def test_issue108_same_input_request_keeps_lease_and_cursor(monkeypatch):
    identity = dict(graph_generation=2,source_revision=7,prefix_revision=0,preview_id=None)
    latest = {**identity,'id':'existing','state':'building'}
    writes = []
    database = SimpleNamespace(execute_native=lambda statement,params=(): (
        SimpleNamespace(fetchone=lambda: latest) if statement.startswith('SELECT *')
        else writes.append(statement)))
    monkeypatch.setattr(inventory,'input_identity',lambda *args,**kwargs: identity)
    monkeypatch.setattr(inventory,'enqueue_task_in_transaction',lambda *args,**kwargs: pytest.fail('Reset task'))
    assert inventory.request_inventory_in_transaction(database,'repertoire') == 'existing'
    assert not writes


def test_issue108_invalid_pages_and_unknown_evidence_do_not_fabricate_success():
    database = SimpleNamespace(execute_native=lambda *args: SimpleNamespace(fetchone=lambda: None))
    assert inventory.source_evidence(database,('fen','source','model','schema','rating','')) is None
    for limit in (0,257,-1):
        with pytest.raises(ValueError,match='page size'):
            inventory.legal_replies(database,'fen',limit=limit)


def test_issue108_handlers_are_registered_in_regular_worker_dispatch():
    from app import tasks
    assert inventory.TASK_KIND in tasks._SUPPORTED_BACKGROUND_KINDS
    assert inventory.RECONCILE_KIND in tasks._SUPPORTED_BACKGROUND_KINDS
