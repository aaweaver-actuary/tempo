"""Current graph color and compatible failed projection recovery."""
from contextlib import contextmanager
import json
from types import SimpleNamespace

import chess
import pytest
from app.services import postgres_opening_segmentation as worker


def presentation_fixture(monkeypatch, colors, stored_color=None):
    source = {'id': 'older-card', 'start_fen': chess.STARTING_FEN,
              'moves_json': json.dumps(['e2e4', 'e7e5', 'g1f3']),
              'revision': 1, 'trained_color': stored_color}
    statements = []
    active = []
    class Read:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: source, fetchall=lambda: [(color,) for color in colors])
    @contextmanager
    def read(**options):
        statements.append(('read_options', options))
        active.append(True)
        try: yield Read()
        finally: active.clear()
    original = worker.presentation_occurrences
    def compute(*args):
        assert not active, 'Chess traversal held a database connection'
        return original(*args)
    monkeypatch.setattr(worker, 'background_read_connection', read)
    monkeypatch.setattr(worker, 'presentation_occurrences', compute)
    task = {'id': 'seg', 'generation': 4, 'payload': {
        'repertoire_id': 'London', 'graph_generation': 7, 'content_version': 3}}
    return task, source, statements


def test_segmentation_legacy_null_color_uses_unique_matching_current_graph(monkeypatch):
    task, source, statements = presentation_fixture(monkeypatch, ['white'])
    card_id, occurrences, _source_snapshot = worker.prepare_presentation(task)
    assert card_id == source['id'] and len(occurrences) == 2
    assert all(item['trained_color'] == 'white' for item in occurrences)
    assert source['trained_color'] is None, 'Resolving advisory provenance mutated study data'
    assert statements[0][1] == {'authoritative': True}
    assert 'opening_graph_publications' in statements[2][0]
    assert 'LIMIT 2' in statements[2][0]


@pytest.mark.parametrize('colors,stored_color,reason', [
    ([], None, 'missing'), (['white', 'black'], None, 'conflicting'),
    (['white'], 'black', 'conflicting'), (['invalid'], None, 'invalid')])
def test_segmentation_unresolved_color_reports_exact_repairable_source(monkeypatch, colors, stored_color, reason):
    task, source, _ = presentation_fixture(monkeypatch, colors, stored_color)
    with pytest.raises(ValueError, match='segmentation_provenance_' + reason) as error:
        worker.prepare_presentation(task)
    assert all(value in str(error.value) for value in ('London', 'older-card', '7'))


def test_segmentation_compatible_failed_retry_keeps_staged_run_and_cursor(monkeypatch):
    from app import activity_commands
    failed = {'id':'seg','kind':'opening_segmentation','deduplication_key':'London',
              'generation':4,'priority':60,'state':'failed','phase':'groups','attempt_count':5,
              'max_attempts':5,'next_attempt_at':None,'created_at':'2026-10-09T00:00:00+00:00',
              'updated_at':'2026-10-09T00:00:00+00:00','last_error':'interrupted',
              'payload_json':json.dumps({'repertoire_id':'London','graph_generation':7,
                   'content_version':3,'group_key':'saved','group_after_card':'older-card'})}
    statements=[]
    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))
            if statement.startswith('SELECT * FROM background_tasks'): row=failed
            elif 'opening_segmentation_state' in statement and statement.startswith('SELECT'):
                row=(3, 'seg:4', 'building', 7)
            elif 'opening_graph_publications' in statement and statement.startswith('SELECT'):
                row=(7,)
            else: row=None
            return SimpleNamespace(fetchone=lambda:row)
    monkeypatch.setattr(worker, 'graph_generation_is_current', lambda *_:True, raising=False)
    activity_commands.retry_failed_task(Database(), {'task_id':'seg'})
    parameters=next(values for statement,values in statements if statement.startswith('UPDATE background_tasks'))
    assert parameters[0]=='groups'
    assert not any('DELETE FROM opening_segmentation' in statement or 'payload_json=' in statement for statement,_ in statements)


def test_segmentation_obsolete_failed_retry_returns_new_task_from_current_graph(monkeypatch):
    queued = {'id':'seg','kind':'opening_segmentation','deduplication_key':'London',
              'generation':5,'priority':60,'state':'queued','phase':'queued','attempt_count':0,
              'max_attempts':5,'next_attempt_at':None,'created_at':'2026-10-09T00:00:00+00:00',
              'updated_at':'2026-10-09T00:00:00+00:00','last_error':None}
    failed = {**queued, 'generation':4, 'phase':'groups', 'payload_json':json.dumps({
        'repertoire_id':'London','graph_generation':7,'content_version':3,'group_key':'saved'})}
    requests=[]
    class Database:
        def execute_native(self, statement, parameters=()):
            if 'SELECT content_version' in statement: row=(4,'seg:4','stale',8)
            elif 'SELECT generation FROM opening_graph_publications' in statement: row=(8,)
            elif 'SELECT * FROM background_tasks' in statement: row=queued
            else: row=None
            return SimpleNamespace(fetchone=lambda:row)
    monkeypatch.setattr(worker,'graph_generation_is_current',lambda *_:True)
    monkeypatch.setattr(worker,'request_segmentation_in_transaction',lambda database, repertoire, generation:
                        requests.append((repertoire,generation)) or {'queued':True,'task_id':'seg'})
    result=worker.retry_segmentation_in_transaction(Database(),failed)
    assert requests==[('London',8)]
    assert result['generation']==5 and result['phase']=='queued' and result['attempts']==0


def test_segmentation_source_edit_after_calculation_cannot_save_staged_occurrences(monkeypatch):
    task, source, _ = presentation_fixture(monkeypatch,['white'])
    prepared=worker.prepare_presentation(task)
    monkeypatch.setattr(worker,'current_slice',lambda *_:True)
    class EditedSource:
        def execute_native(self, *_): return SimpleNamespace(fetchone=lambda:{**source,'revision':2})
    with pytest.raises(ValueError,match='segmentation_source_changed.*older-card'):
        worker.stage_presentation(EditedSource(),task,prepared)


def test_segmentation_cleanup_drains_imported_run_children_before_parent(monkeypatch):
    monkeypatch.setattr(worker,'current_slice',lambda *_:True)
    advances=[]
    monkeypatch.setattr(worker,'advance_task_slice_in_transaction',lambda database, task, **values: advances.append(values) or True)
    statements=[]
    class OldImportedRun:
        def execute_native(self, statement, parameters=()):
            statements.append((statement,parameters))
            return SimpleNamespace(fetchone=lambda:('prior-task:3',),rowcount=8)
    task={'id':'current-task','generation':1,'payload':{'repertoire_id':'London','cleanup_table':4}}
    assert worker.cleanup_previous_runs(OldImportedRun(),task)
    assert advances[0]['next_payload']['cleanup_run_id']=='prior-task:3'
    assert advances[0]['next_payload']['cleanup_table']==0
    assert advances[0]['next_payload']['cleanup_completed_units']==8
    assert statements[1][1]==('prior-task:3',)
    assert 'LIMIT 8' in statements[1][0]
    assert all('DELETE FROM opening_segmentation_runs' not in statement for statement,_ in statements)


def test_segmentation_cleanup_cannot_cascade_remaining_child_rows(monkeypatch):
    monkeypatch.setattr(worker,'current_slice',lambda *_:True)
    statements=[]
    class RemainingChildren:
        def execute_native(self,statement,parameters=()):
            statements.append(statement)
            return SimpleNamespace(rowcount=0)
    task={'id':'current-task','generation':1,'payload':{'repertoire_id':'London','cleanup_run_id':'old-run','cleanup_table':5}}
    with pytest.raises(RuntimeError,match='parent still has children'):
        worker.cleanup_previous_runs(RemainingChildren(),task)
    assert all('NOT EXISTS(SELECT 1 FROM '+table in statements[0] for table in worker._RETENTION_TABLES)
